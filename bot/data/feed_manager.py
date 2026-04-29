from __future__ import annotations

import asyncio
from typing import Dict, Callable, Awaitable, List

from loguru import logger

from bot.data.market_data import Ticker
from bot.exchanges.base import ExchangeAdapter
from bot.exchanges.aggregator import ExchangeAggregator


class FeedManager:
    def __init__(
        self,
        adapters: Dict[str, ExchangeAdapter],
        aggregator: ExchangeAggregator,
        on_ticker: Callable[[Dict[str, Ticker]], Awaitable[None]],
    ) -> None:
        self._adapters = adapters
        self._aggregator = aggregator
        self._on_ticker = on_ticker

    async def _watch_exchange(self, exchange: str, adapter: ExchangeAdapter) -> None:
        pair = "BTC/JPY"
        while True:
            try:
                await adapter.subscribe_ticker(pair, self._handle_ticker)
            except Exception as e:
                logger.warning(f"WebSocket disconnected from {exchange}: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def _handle_ticker(self, ticker: Ticker) -> None:
        self._aggregator.update_ticker(ticker)
        all_tickers = self._aggregator.get_all_tickers()
        await self._on_ticker(all_tickers)

    async def run(self) -> None:
        tasks: List[asyncio.Task] = []
        for exchange, adapter in self._adapters.items():
            task = asyncio.create_task(
                self._watch_exchange(exchange, adapter),
                name=f"feed_{exchange}",
            )
            tasks.append(task)
        await asyncio.gather(*tasks)
