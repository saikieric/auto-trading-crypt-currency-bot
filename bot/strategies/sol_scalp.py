from __future__ import annotations

import time
from collections import deque
from typing import Optional, Dict, List

from loguru import logger

from bot.config.schema import SolScalpConfig
from bot.data.market_data import Ticker, OHLCV
from bot.exchanges.aggregator import ExchangeAggregator
from bot.indicators.moving_averages import ema, ema_series
from bot.state.portfolio import Portfolio
from bot.strategies.base import Strategy, TradeSignal, SignalType


class SolScalpStrategy(Strategy):
    """
    2タイムフレーム スキャルピング戦略 (SOL/JPY on Bitbank)

    地合い判定: 15m EMA20 > EMA50 かつ EMA20 が上向き
    エントリー: 1m 足で EMA9 より下に押した瞬間に Maker 買い指値
    BTC 回路ブレーカー: BTC/JPY の直近1m 変動が -btc_crash_pct% 以上の急落
    出口: TP / SL / タイムSL はポジションモニター (main_loop) が担当
    """

    name = "sol_scalp"

    def __init__(
        self,
        config: SolScalpConfig,
        aggregator: ExchangeAggregator,
        portfolio: Portfolio,
    ) -> None:
        self._config = config
        self._aggregator = aggregator
        self._portfolio = portfolio
        self._candles_15m: deque[OHLCV] = deque(maxlen=200)
        self._candles_1m: deque[OHLCV] = deque(maxlen=100)
        # BTC circuit breaker: track recent BTC tickers
        self._btc_prices: deque[tuple[float, float]] = deque(maxlen=10)  # (ts, price)
        self._last_signal_ts: float = 0.0

    async def on_ticker_update(self, tickers: Dict[str, Ticker]) -> Optional[TradeSignal]:
        # BTC 急落サーキットブレーカー用に BTC/JPY ティッカーを追記
        for ticker in tickers.values():
            if ticker.pair == "BTC/JPY":
                self._btc_prices.append((ticker.timestamp, ticker.last))
        return None

    async def on_ohlcv_update(
        self, exchange: str, candles: List[OHLCV], timeframe: str = ""
    ) -> Optional[TradeSignal]:
        if exchange != "bitbank":
            return None

        if timeframe == self._config.timeframe_trend:
            # 15m ローソク足: トレンドフィルタ用
            for c in candles:
                self._candles_15m.append(c)
            return None  # エントリー判定は 1m 足更新時のみ

        if timeframe == self._config.timeframe_entry:
            # 1m ローソク足: エントリー判定
            for c in candles:
                self._candles_1m.append(c)
            return self._check_entry()

        return None

    def _check_entry(self) -> Optional[TradeSignal]:
        # ─── 1. 15m トレンドフィルタ ───────────────────────────────────
        closes_15m = [c.close for c in self._candles_15m]
        if len(closes_15m) < self._config.trend_ema_slow + 2:
            logger.debug(f"[sol_scalp] 15m candles insufficient: {len(closes_15m)}")
            return None

        ema20_all = ema_series(closes_15m, self._config.trend_ema_fast)
        ema50_val = ema(closes_15m, self._config.trend_ema_slow)
        valid_ema20 = [v for v in ema20_all if v is not None]

        if len(valid_ema20) < 2 or ema50_val is None:
            return None

        ema20_last = valid_ema20[-1]
        ema20_prev = valid_ema20[-2]

        if ema20_last <= ema50_val:
            logger.debug(
                f"[sol_scalp] No uptrend: EMA20({ema20_last:.3f}) <= EMA50({ema50_val:.3f})"
            )
            return None
        if ema20_last <= ema20_prev:
            logger.debug(
                f"[sol_scalp] EMA20 not rising: {ema20_last:.3f} <= {ema20_prev:.3f}"
            )
            return None

        # ─── 2. 1m 押し目チェック ──────────────────────────────────────
        closes_1m = [c.close for c in self._candles_1m]
        if len(closes_1m) < self._config.entry_ema_period + 1:
            return None

        ema9_val = ema(closes_1m, self._config.entry_ema_period)
        if ema9_val is None:
            return None

        last_close = closes_1m[-1]
        if last_close >= ema9_val:
            logger.debug(
                f"[sol_scalp] No pullback: close({last_close:.3f}) >= EMA9({ema9_val:.3f})"
            )
            return None

        # ─── 3. 急騰 / 急落チェック (最新 1m ローソク足) ──────────────
        last_candle = self._candles_1m[-1]
        if last_candle.open > 0:
            candle_move_pct = abs(last_candle.close - last_candle.open) / last_candle.open * 100
            if candle_move_pct > self._config.sol_spike_pct:
                logger.debug(
                    f"[sol_scalp] Sudden move {candle_move_pct:.2f}% > {self._config.sol_spike_pct}%"
                )
                return None

        # ─── 4. BTC 急落サーキットブレーカー ──────────────────────────
        if self._btc_crash_detected():
            logger.warning("[sol_scalp] BTC crash detected — skipping entry")
            return None

        # ─── 5. スプレッドチェック ─────────────────────────────────────
        ticker = self._aggregator.get_ticker("bitbank", "SOL/JPY")
        if not ticker:
            logger.debug("[sol_scalp] No SOL/JPY ticker")
            return None

        if ticker.spread_pct > self._config.max_spread_pct:
            logger.debug(
                f"[sol_scalp] Spread too wide: {ticker.spread_pct:.3f}%"
            )
            return None

        # ─── 6. クールダウン ───────────────────────────────────────────
        if time.time() - self._last_signal_ts < self._config.cooldown_seconds:
            return None

        # ─── 7. ポジション上限 ─────────────────────────────────────────
        open_sol = [p for p in self._portfolio._open_positions if p.pair == "SOL/JPY"]
        if len(open_sol) >= self._config.max_positions:
            logger.debug(f"[sol_scalp] Max positions reached: {len(open_sol)}")
            return None

        # ─── 8. シグナル生成 ───────────────────────────────────────────
        # Best Bid - maker_offset で Maker 維持
        limit_price = ticker.bid - self._config.maker_offset
        trend_gap_pct = (ema20_last - ema50_val) / ema50_val * 100
        confidence = min(trend_gap_pct / 2.0, 1.0)

        reason = (
            f"SOL scalp BUY: 15m EMA20({ema20_last:.2f})>EMA50({ema50_val:.2f}) rising, "
            f"1m pullback close({last_close:.3f})<EMA9({ema9_val:.3f}), "
            f"bid={ticker.bid:.3f} spread={ticker.spread_pct:.3f}%"
        )
        logger.info(f"[sol_scalp] {reason}")

        self._last_signal_ts = time.time()

        return TradeSignal(
            strategy=self.name,
            signal=SignalType.BUY,
            pair="SOL/JPY",
            buy_exchange="bitbank",
            sell_exchange="",
            suggested_amount_jpy=self._config.order_size_jpy,
            reason=reason,
            confidence=confidence,
            use_maker=True,
            maker_timeout_seconds=self._config.maker_timeout_seconds,
            metadata={
                "ema20_15m": ema20_last,
                "ema50_15m": ema50_val,
                "ema9_1m": ema9_val,
                "close_1m": last_close,
                "limit_price": limit_price,
                "spread_pct": ticker.spread_pct,
            },
        )

    def _btc_crash_detected(self) -> bool:
        if len(self._btc_prices) < 2:
            return False
        now = time.time() * 1000
        # 直近60秒以内の最高値と現在値を比較
        recent = [(ts, p) for ts, p in self._btc_prices if now - ts < 60_000]
        if len(recent) < 2:
            return False
        peak = max(p for _, p in recent)
        current = recent[-1][1]
        if peak == 0:
            return False
        drop_pct = (peak - current) / peak * 100
        return drop_pct >= self._config.btc_crash_pct
