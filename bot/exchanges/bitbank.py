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

    async def fetch_order_book(self, pair: str = "BTC/JPY", limit: int = 20) -> "OrderBook":
        from bot.data.market_data import OrderBook
        raw = await _run(lambda: self._exchange.fetch_order_book(pair, limit))
        return OrderBook(
            exchange=self.name,
            pair=pair,
            bids=[(b[0], b[1]) for b in raw.get("bids", [])],
            asks=[(a[0], a[1]) for a in raw.get("asks", [])],
            timestamp=raw.get("timestamp") or time.time() * 1000,
        )

    async def fetch_balance(self) -> dict[str, float]:
        raw = await _run(lambda: self._exchange.fetch_balance())
        return {
            "JPY": raw.get("JPY", {}).get("free", 0.0),
            "BTC": raw.get("BTC", {}).get("free", 0.0),
            "SOL": raw.get("SOL", {}).get("free", 0.0),
        }

    async def place_market_order(
        self, side: str, amount_btc: float, pair: str = "BTC/JPY"
    ) -> OrderResult:
        # bitbank SOL最小単位: 0.0001、BTC: 0.00000001
        precision = 4 if pair.startswith("SOL") else 8
        amount_btc = round(amount_btc, precision)
        logger.info(f"[bitbank] place_market_order: {side} {amount_btc} {pair}")
        raw = await _run(lambda: self._exchange.create_order(
            pair, "market", side, amount_btc
        ))
        order_id = str(raw.get("id") or "")
        # 約定情報が揃うまで最大5回リトライ
        if order_id:
            for attempt in range(5):
                await asyncio.sleep(1.5 + attempt)
                try:
                    result = await self.fetch_order_status(order_id, pair)
                    result.side = result.side or side
                    result.pair = result.pair or pair
                    if result.price > 0 and result.amount_btc > 0:
                        return result
                except Exception:
                    pass
        raw["side"] = raw.get("side") or side
        return self._parse_order(raw)

    async def place_limit_order(
        self, side: str, amount_btc: float, price: float, pair: str = "BTC/JPY"
    ) -> OrderResult:
        precision = 4 if pair.startswith("SOL") else 8
        amount_btc = round(amount_btc, precision)
        logger.info(f"[bitbank] place_limit_order: {side} {amount_btc} {pair} @ {price}")
        raw = await _run(lambda: self._exchange.create_order(
            pair, "limit", side, amount_btc, price
        ))
        return self._parse_order(raw)

    async def cancel_order(self, order_id: str, pair: str = "BTC/JPY") -> bool:
        try:
            await _run(lambda: self._exchange.cancel_order(order_id, pair))
            return True
        except Exception:
            return False

    async def fetch_order_status(self, order_id: str, pair: str = "BTC/JPY") -> OrderResult:
        raw = await _run(lambda: self._exchange.fetch_order(order_id, pair))
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
            fee = float(raw["fee"].get("cost") or 0.0)

        side = (raw.get("side") or "buy").lower()
        pair = raw.get("symbol") or "BTC/JPY"
        amount_btc = float(raw.get("filled") or raw.get("amount") or 0.0)
        price = float(raw.get("average") or raw.get("price") or 0.0)
        status = self._map_status(raw.get("status") or "") or "open"
        order_id = str(raw.get("id") or "")
        timestamp = float(raw.get("timestamp") or time.time() * 1000)

        return OrderResult(
            exchange=self.name,
            order_id=order_id,
            side=side,
            pair=pair,
            amount_btc=amount_btc,
            price=price,
            fee_jpy=fee,
            status=status,
            timestamp=timestamp,
            raw=raw,
        )

    @staticmethod
    def _map_status(status: str) -> str:
        return {"open": "open", "closed": "filled", "canceled": "cancelled"}.get(status, status)
