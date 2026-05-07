from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Awaitable, Optional

from bot.data.market_data import Ticker, OHLCV, OrderBook, OrderResult  # noqa: F401


class ExchangeAdapter(ABC):
    name: str  # "bitflyer" | "gmo" | "bitbank"

    # --- REST ---

    @abstractmethod
    async def fetch_ticker(self, pair: str = "BTC/JPY") -> Ticker:
        ...

    @abstractmethod
    async def fetch_ohlcv(self, pair: str, timeframe: str, limit: int) -> list[OHLCV]:
        ...

    @abstractmethod
    async def fetch_balance(self) -> dict[str, float]:
        """Returns {"JPY": float, "BTC": float, ...}"""
        ...

    @abstractmethod
    async def fetch_order_book(self, pair: str = "BTC/JPY", limit: int = 20) -> "OrderBook":
        ...

    @abstractmethod
    async def place_market_order(self, side: str, amount_btc: float, pair: str = "BTC/JPY") -> OrderResult:
        ...

    @abstractmethod
    async def place_limit_order(self, side: str, amount_btc: float, price: float, pair: str = "BTC/JPY") -> OrderResult:
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        ...

    @abstractmethod
    async def fetch_order_status(self, order_id: str) -> OrderResult:
        ...

    # --- WebSocket ---

    @abstractmethod
    async def subscribe_ticker(
        self,
        pair: str,
        callback: Callable[[Ticker], Awaitable[None]],
    ) -> None:
        ...

    @abstractmethod
    async def subscribe_orderbook(
        self,
        pair: str,
        callback: Callable[[OrderBook], Awaitable[None]],
    ) -> None:
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        ...
