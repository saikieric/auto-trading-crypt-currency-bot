from __future__ import annotations

import asyncio
import time
from typing import Callable, Awaitable

import ccxt

from bot.data.market_data import Ticker, OHLCV, OrderBook, OrderResult
from bot.exchanges.base import ExchangeAdapter


def _run(fn):
    """Run a synchronous ccxt call in a thread pool so it doesn't block the event loop."""
    return asyncio.get_event_loop().run_in_executor(None, fn)


class BitbankAdapter(ExchangeAdapter):
    name = "bitbank"

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._exchange = ccxt.bitbank({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        })

    async def fetch_ticker(self, pair: str = "BTC/JPY") -> Ticker:
        raw = await _run(lambda: self._exchange.fetch_ticker(pair))
        return Ticker(
            exchange=self.name,
            pair=pair,
            bid=raw["bid"],
            ask=raw["ask"],
            last=raw["last"],
            timestamp=raw["timestamp"] or time.time() * 1000,
        )

    async def fetch_ohlcv(self, pair: str, timeframe: str, limit: int) -> list[OHLCV]:
        raw_list = await _run(lambda: self._exchange.fetch_ohlcv(pair, timeframe, limit=limit))
        return [
            OHLCV(
                timestamp=r[0], open=r[1], high=r[2],
                low=r[3], close=r[4], volume=r[5],
            )
            for r in raw_list
        ]

    async def fetch_balance(self) -> dict[str, float]:
        raw = await _run(lambda: self._exchange.fetch_balance())
        return {
            "JPY": raw.get("JPY", {}).get("free", 0.0),
            "BTC": raw.get("BTC", {}).get("free", 0.0),
        }

    async def place_market_order(self, side: str, amount_btc: float) -> OrderResult:
        raw = await _run(lambda: self._exchange.create_order(
            "BTC/JPY", "market", side, amount_btc
        ))
        return self._parse_order(raw)

    async def place_limit_order(self, side: str, amount_btc: float, price: float) -> OrderResult:
        raw = await _run(lambda: self._exchange.create_order(
            "BTC/JPY", "limit", side, amount_btc, price
        ))
        return self._parse_order(raw)

    async def cancel_order(self, order_id: str) -> bool:
        try:
            await _run(lambda: self._exchange.cancel_order(order_id, "BTC/JPY"))
            return True
        except Exception:
            return False

    async def fetch_order_status(self, order_id: str) -> OrderResult:
        raw = await _run(lambda: self._exchange.fetch_order(order_id, "BTC/JPY"))
        return self._parse_order(raw)

    async def subscribe_ticker(
        self,
        pair: str,
        callback: Callable[[Ticker], Awaitable[None]],
    ) -> None:
        # REST ポーリングで代替（3秒ごと）
        while True:
            try:
                ticker = await self.fetch_ticker(pair)
                await callback(ticker)
            except Exception:
                pass
            await asyncio.sleep(3)

    async def subscribe_orderbook(
        self,
        pair: str,
        callback: Callable[[OrderBook], Awaitable[None]],
    ) -> None:
        while True:
            await asyncio.sleep(60)

    async def disconnect(self) -> None:
        pass

    def _parse_order(self, raw: dict) -> OrderResult:
        fee = 0.0
        if raw.get("fee"):
            fee = raw["fee"].get("cost", 0.0)
        return OrderResult(
            exchange=self.name,
            order_id=str(raw["id"]),
            side=raw["side"],
            pair=raw.get("symbol", "BTC/JPY"),
            amount_btc=raw.get("filled", 0.0) or raw.get("amount", 0.0),
            price=raw.get("average", 0.0) or raw.get("price", 0.0),
            fee_jpy=fee,
            status=self._map_status(raw.get("status", "")),
            timestamp=raw.get("timestamp") or time.time() * 1000,
            raw=raw,
        )

    @staticmethod
    def _map_status(status: str) -> str:
        return {"open": "open", "closed": "filled", "canceled": "cancelled"}.get(status, status)
