from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.data.market_data import OrderResult
    from bot.strategies.base import TradeSignal


class Notifier(ABC):
    @abstractmethod
    async def send(self, message: str, level: str = "info") -> None:
        ...

    @abstractmethod
    async def send_trade_notification(
        self, order: "OrderResult", signal: "TradeSignal"
    ) -> None:
        ...

    @abstractmethod
    async def send_daily_summary(self, summary: dict) -> None:
        ...

    @abstractmethod
    async def send_error(self, error: Exception, context: str) -> None:
        ...
