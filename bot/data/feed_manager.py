from __future__ import annotations

import asyncio
from typing import Dict, Callable, Awaitable, List, Tuple

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
        subscriptions: List[Tuple[str, str]] = None,
    ) -> None:
        self._adapters = adapters
        self._aggregator = aggregator
        self._on_ticker = on_ticker
        # list of (exchange, pair) to subscribe to
        # default: each exchange → BTC/JPY
        if subscriptions is not None:
            self._subscriptions = subscriptions
        else:
            self._subscriptions = [(ex, "BTC/JPY") for ex in adapters]

    async def _watch(self, exchange: str, pair: str, adapter: ExchangeAdapter) -> None:
        while True:
            try:
                await adapter.subscribe_ticker(pair, self._handle_ticker)
            except Exception as e:
                logger.warning(f"Feed disconnected {exchange}/{pair}: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def _handle_ticker(self, ticker: Ticker) -> None:
        self._aggregator.update_ticker(ticker)
        all_tickers = self._aggregator.get_all_tickers()
        await self._on_ticker(all_tickers)

    async def run(self) -> None:
        tasks: List[asyncio.Task] = []
        for exchange, pair in self._subscriptions:
            adapter = self._adapters.get(exchange)
            if not adapter:
                continue
            task = asyncio.create_task(
                self._watch(exchange, pair, adapter),
                name=f"feed_{exchange}_{pair.replace('/', '_')}",
            )
            tasks.append(task)
        await asyncio.gather(*tasks)
