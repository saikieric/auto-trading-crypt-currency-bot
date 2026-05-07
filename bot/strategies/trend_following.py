from __future__ import annotations

from collections import deque
from typing import Optional, Dict

from bot.config.schema import TrendConfig
from bot.data.market_data import Ticker, OHLCV
from bot.exchanges.aggregator import ExchangeAggregator
from bot.indicators.moving_averages import detect_crossover
from loguru import logger
from bot.strategies.base import Strategy, TradeSignal, SignalType


class TrendFollowingStrategy(Strategy):
    name = "trend"

    def __init__(
        self,
        config: TrendConfig,
        aggregator: ExchangeAggregator,
        max_trade_amount_jpy: float,
        pair: str = "BTC/JPY",
        exchange_filter: Optional[str] = None,
        strategy_name: str = "trend",
    ) -> None:
        self._config = config
        self._aggregator = aggregator
        self._max_amount = max_trade_amount_jpy
        self._pair = pair
        self._exchange_filter = exchange_filter  # None = all exchanges
        self.name = strategy_name
        self._candles: deque[OHLCV] = deque(maxlen=200)
        self._last_cross: Optional[str] = None  # 同じクロス方向の重複シグナルを防ぐ

    async def on_ticker_update(self, tickers: Dict[str, Ticker]) -> Optional[TradeSignal]:
        return None  # trend strategy uses OHLCV, not real-time ticks

    async def on_ohlcv_update(
        self, exchange: str, candles: list[OHLCV], timeframe: str = ""
    ) -> Optional[TradeSignal]:
        if self._exchange_filter and exchange != self._exchange_filter:
            return None
        for c in candles:
            self._candles.append(c)

        closes = [c.close for c in self._candles]
        if len(closes) < self._config.slow_period + self._config.signal_confirmation_bars:
            return None

        cross = detect_crossover(
            closes,
            closes,
            self._config.fast_period,
            self._config.slow_period,
            self._config.signal_confirmation_bars,
        )
        from bot.indicators.moving_averages import ema as _ema
        _fast = _ema(closes, self._config.fast_period) or 0.0
        _slow = _ema(closes, self._config.slow_period) or 0.0
        _gap = abs(_fast - _slow) / _slow * 100 if _slow else 0.0
        logger.debug(
            f"[{self.name}/{self._pair}] candles={len(closes)} cross={cross} "
            f"fast={_fast:.2f} slow={_slow:.2f} gap={_gap:.3f}%"
        )

        if cross is None:
            self._last_cross = None  # クロスなしでリセット（次のクロスを検出可能にする）
            return None

        # 同じ方向のクロスが連続したら無視
        if cross == self._last_cross:
            return None

        signal_type = SignalType.BUY if cross == "golden" else SignalType.SELL

        # Pick the exchange with the tightest ask for buys, tightest bid for sells
        if self._exchange_filter:
            # 特定取引所に固定
            best_ex = self._exchange_filter
            ticker = self._aggregator.get_ticker(best_ex)
            best_price = (ticker.ask if signal_type == SignalType.BUY else ticker.bid) if ticker else 0.0
        elif signal_type == SignalType.BUY:
            best_ex, best_price = self._aggregator.get_best_ask(pair=self._pair)
        else:
            best_ex, best_price = self._aggregator.get_best_bid(pair=self._pair)

        if not best_ex or best_price == 0:
            return None

        # Confidence proportional to EMA gap — simple proxy
        from bot.indicators.moving_averages import ema
        fast_val = ema(closes, self._config.fast_period) or 0.0
        slow_val = ema(closes, self._config.slow_period) or 0.0
        gap_pct = abs(fast_val - slow_val) / slow_val * 100 if slow_val else 0.0
        confidence = min(gap_pct / 2.0, 1.0)

        if confidence < self._config.min_signal_confidence:
            return None

        reason = (
            f"{cross.capitalize()} cross: fast_EMA({self._config.fast_period})={fast_val:.0f} "
            f"slow_EMA({self._config.slow_period})={slow_val:.0f}"
        )

        self._last_cross = cross

        return TradeSignal(
            strategy=self.name,
            signal=signal_type,
            pair=self._pair,
            buy_exchange=best_ex if signal_type == SignalType.BUY else "",
            sell_exchange=best_ex if signal_type == SignalType.SELL else "",
            suggested_amount_jpy=self._max_amount,
            reason=reason,
            confidence=confidence,
            use_maker=self._config.use_maker_orders,
            maker_timeout_seconds=self._config.maker_timeout_seconds,
            metadata={
                "cross": cross,
                "fast_ema": fast_val,
                "slow_ema": slow_val,
                "gap_pct": gap_pct,
            },
        )
