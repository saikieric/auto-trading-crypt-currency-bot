from __future__ import annotations

import time
from typing import Optional, Dict

from bot.config.schema import ArbitrageConfig
from bot.data.market_data import Ticker, OHLCV
from bot.exchanges.aggregator import ExchangeAggregator
from bot.state.portfolio import Portfolio
from bot.strategies.base import Strategy, TradeSignal, SignalType


class ArbitrageStrategy(Strategy):
    name = "arbitrage"

    def __init__(
        self,
        config: ArbitrageConfig,
        aggregator: ExchangeAggregator,
        portfolio: Portfolio,
        max_trade_amount_jpy: float,
    ) -> None:
        self._config = config
        self._aggregator = aggregator
        self._portfolio = portfolio
        self._max_amount = max_trade_amount_jpy
        self._last_trade_ts: float = 0.0

    async def on_ohlcv_update(
        self, exchange: str, candles: list[OHLCV], timeframe: str = ""
    ) -> Optional[TradeSignal]:
        return None  # arbitrage uses real-time tickers, not candles

    async def on_ticker_update(
        self, tickers: Dict[str, Ticker]
    ) -> Optional[TradeSignal]:
        # Update aggregator with fresh tickers
        for ticker in tickers.values():
            self._aggregator.update_ticker(ticker)

        # Cooldown check
        if time.time() - self._last_trade_ts < self._config.cooldown_seconds:
            return None

        buy_exchange, sell_exchange, spread_pct = self._aggregator.get_max_spread()
        if not buy_exchange or not sell_exchange:
            return None

        # Net spread after both fees
        buy_ticker = self._aggregator.get_ticker(buy_exchange)
        sell_ticker = self._aggregator.get_ticker(sell_exchange)
        if not buy_ticker or not sell_ticker:
            return None

        if spread_pct < self._config.min_spread_pct:
            return None

        # Balance check: need JPY on buy side, BTC on sell side
        available_jpy = self._portfolio.get_available_jpy(buy_exchange)
        available_btc = self._portfolio.get_available_btc(sell_exchange)

        if available_jpy < 1000 or available_btc <= 0:
            return None

        # Calculate trade size: limited by both sides
        amount_jpy = min(self._max_amount, available_jpy)
        amount_btc_from_jpy = amount_jpy / buy_ticker.ask
        amount_btc = min(amount_btc_from_jpy, available_btc)

        if amount_btc * buy_ticker.ask < 1000:
            return None

        final_amount_jpy = amount_btc * buy_ticker.ask
        min_spread = self._config.min_spread_pct
        confidence = min((spread_pct - min_spread) / min_spread, 1.0) if min_spread > 0 else 1.0

        reason = (
            f"Arb spread {spread_pct:.3f}%: buy on {buy_exchange} "
            f"@ ¥{buy_ticker.ask:,.0f}, sell on {sell_exchange} @ ¥{sell_ticker.bid:,.0f}"
        )

        return TradeSignal(
            strategy=self.name,
            signal=SignalType.BUY,
            pair="BTC/JPY",
            buy_exchange=buy_exchange,
            sell_exchange=sell_exchange,
            suggested_amount_jpy=final_amount_jpy,
            reason=reason,
            confidence=confidence,
            metadata={
                "spread_pct": spread_pct,
                "buy_ask": buy_ticker.ask,
                "sell_bid": sell_ticker.bid,
                "amount_btc": amount_btc,
            },
        )

    def record_trade(self) -> None:
        self._last_trade_ts = time.time()
