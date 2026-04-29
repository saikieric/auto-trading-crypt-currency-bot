from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Ticker:
    exchange: str
    pair: str
    bid: float
    ask: float
    last: float
    timestamp: float  # Unix milliseconds

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_pct(self) -> float:
        if self.ask == 0:
            return 0.0
        return (self.ask - self.bid) / self.ask * 100


@dataclass
class OHLCV:
    timestamp: float  # Unix milliseconds
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class OrderBook:
    exchange: str
    pair: str
    bids: list[tuple[float, float]]  # [(price, size), ...]
    asks: list[tuple[float, float]]
    timestamp: float

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0][0] if self.asks else None


@dataclass
class OrderResult:
    exchange: str
    order_id: str
    side: str           # "buy" | "sell"
    pair: str
    amount_btc: float
    price: float
    fee_jpy: float
    status: str         # "open" | "filled" | "cancelled" | "failed"
    timestamp: float    # Unix milliseconds
    raw: dict = field(default_factory=dict)

    @property
    def total_jpy(self) -> float:
        return self.amount_btc * self.price
