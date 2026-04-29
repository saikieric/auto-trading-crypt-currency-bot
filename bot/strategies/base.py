from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict

from bot.data.market_data import Ticker, OHLCV


class SignalType(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class TradeSignal:
    strategy: str
    signal: SignalType
    pair: str
    buy_exchange: str
    sell_exchange: str
    suggested_amount_jpy: float
    reason: str
    confidence: float       # 0.0–1.0
    metadata: Dict = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.signal in (SignalType.BUY, SignalType.SELL)


class Strategy(ABC):
    name: str

    @abstractmethod
    async def on_ticker_update(
        self, tickers: Dict[str, Ticker]
    ) -> Optional[TradeSignal]:
        ...

    @abstractmethod
    async def on_ohlcv_update(
        self, exchange: str, candles: list[OHLCV]
    ) -> Optional[TradeSignal]:
        ...
