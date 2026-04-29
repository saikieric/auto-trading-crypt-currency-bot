from __future__ import annotations

from typing import Dict, List, Optional

from bot.config.schema import StrategyConfig
from bot.data.market_data import Ticker, OHLCV
from bot.strategies.base import Strategy, TradeSignal


class StrategyComposer:
    def __init__(self, strategies: List[Strategy], config: StrategyConfig) -> None:
        self._all_strategies = {s.name: s for s in strategies}
        self._config = config

    def _active_strategies(self) -> List[Strategy]:
        active = self._config.active
        if active == "both":
            return list(self._all_strategies.values())
        return [s for name, s in self._all_strategies.items() if name == active]

    async def on_ticker_update(
        self, tickers: Dict[str, Ticker]
    ) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        for strategy in self._active_strategies():
            signal = await strategy.on_ticker_update(tickers)
            if signal and signal.is_actionable:
                signals.append(signal)
        return signals

    async def on_ohlcv_update(
        self, exchange: str, candles: List[OHLCV]
    ) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        for strategy in self._active_strategies():
            signal = await strategy.on_ohlcv_update(exchange, candles)
            if signal and signal.is_actionable:
                signals.append(signal)
        return signals
