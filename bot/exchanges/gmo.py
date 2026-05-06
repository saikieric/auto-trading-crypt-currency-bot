from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Callable, Awaitable, Optional

import aiohttp

from bot.data.market_data import Ticker, OHLCV, OrderBook, OrderResult
from bot.exchanges.base import ExchangeAdapter

_REST_BASE = "https://api.coin.z.com"
_WS_PUBLIC = "wss://push.coin.z.com/topics"

_TIMEFRAME_MAP = {
    "1m": "1min", "5m": "5min", "10m": "10min", "15m": "15min",
    "30m": "30min", "1h": "1hour", "4h": "4hour", "8h": "8hour",
    "1d": "1day", "1w": "1week", "1M": "1month",
}

_SYMBOL_MAP = {"BTC/JPY": "BTC_JPY"}


class GmoAdapter(ExchangeAdapter):
    name = "gmo"

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._session: Optional[aiohttp.ClientSession] = None

    def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _symbol(self, pair: str) -> str:
        return _SYMBOL_MAP.get(pair, pair.replace("/", "_"))

    def _sign_request(self, method: str, path: str, body: str = "") -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        text = timestamp + method.upper() + path + body
        signature = hmac.new(
            self._api_secret.encode(), text.encode(), hashlib.sha256
        ).hexdigest()
        return {
            "API-KEY": self._api_key,
            "API-TIMESTAMP": timestamp,
            "API-SIGN": signature,
        }

    async def _get_public(self, path: str, params: Optional[dict] = None) -> dict:
        url = _REST_BASE + "/public" + path
        async with self._session_get().get(url, params=params) as resp:
            data = await resp.json()
        if data.get("status") != 0:
            raise RuntimeError(f"GMO public API error: {data}")
        return data

    async def _get_private(self, path: str) -> dict:
        headers = self._sign_request("GET", "/private" + path)
        url = _REST_BASE + "/private" + path
        async with self._session_get().get(url, headers=headers) as resp:
            data = await resp.json()
        if data.get("status") != 0:
            raise RuntimeError(f"GMO private API error: {data}")
        return data

    async def _post_private(self, path: str, body: dict) -> dict:
        body_str = json.dumps(body)
        headers = {
            "Content-Type": "application/json",
            **self._sign_request("POST", "/private" + path, body_str),
        }
        url = _REST_BASE + "/private" + path
        async with self._session_get().post(url, headers=headers, data=body_str) as resp:
            data = await resp.json()
        if data.get("status") != 0:
            raise RuntimeError(f"GMO private API error: {data}")
        return data

    async def fetch_ticker(self, pair: str = "BTC/JPY") -> Ticker:
        symbol = self._symbol(pair)
        data = await self._get_public("/v1/ticker", {"symbol": symbol})
        item = data["data"][0]
        return Ticker(
            exchange=self.name,
            pair=pair,
            bid=float(item["bid"]),
            ask=float(item["ask"]),
            last=float(item["last"]),
            timestamp=time.time() * 1000,
        )

    async def fetch_ohlcv(self, pair: str, timeframe: str, limit: int) -> list[OHLCV]:
        symbol = self._symbol(pair)
        interval = _TIMEFRAME_MAP.get(timeframe, "15min")
        import datetime
        date_str = datetime.date.today().strftime("%Y%m%d")
        data = await self._get_public(
            f"/v1/klines",
            {"symbol": symbol, "interval": interval, "date": date_str},
        )
        candles = data.get("data", [])[-limit:]
        return [
            OHLCV(
                timestamp=float(c["openTime"]),
                open=float(c["open"]),
                high=float(c["high"]),
                low=float(c["low"]),
                close=float(c["close"]),
                volume=float(c["volume"]),
            )
            for c in candles
        ]

    async def fetch_balance(self) -> dict[str, float]:
        data = await self._get_private("/v1/account/assets")
        result = {"JPY": 0.0, "BTC": 0.0}
        for item in data.get("data", []):
            symbol = item.get("symbol", "")
            available = float(item.get("available", 0))
            if symbol == "JPY":
                result["JPY"] = available
            elif symbol == "BTC":
                result["BTC"] = available
        return result

    async def place_market_order(self, side: str, amount_btc: float, pair: str = "BTC/JPY") -> OrderResult:
        body = {
            "symbol": "BTC_JPY",
            "side": side.upper(),
            "executionType": "MARKET",
            "size": str(amount_btc),
        }
        data = await self._post_private("/v1/order", body)
        order_id = str(data["data"])
        await asyncio.sleep(0.5)
        return await self.fetch_order_status(order_id)

    async def place_limit_order(self, side: str, amount_btc: float, price: float, pair: str = "BTC/JPY") -> OrderResult:
        body = {
            "symbol": "BTC_JPY",
            "side": side.upper(),
            "executionType": "LIMIT",
            "price": str(int(price)),
            "size": str(amount_btc),
        }
        data = await self._post_private("/v1/order", body)
        order_id = str(data["data"])
        return await self.fetch_order_status(order_id)

    async def cancel_order(self, order_id: str) -> bool:
        try:
            await self._post_private("/v1/cancelOrder", {"orderId": int(order_id)})
            return True
        except Exception:
            return False

    async def fetch_order_status(self, order_id: str) -> OrderResult:
        data = await self._get_private(f"/v1/orders?orderId={order_id}")
        items = data.get("data", {}).get("list", [])
        if not items:
            raise RuntimeError(f"Order not found: {order_id}")
        item = items[0]
        filled = float(item.get("executedSize", 0))
        price = float(item.get("executedPrice", 0) or item.get("price", 0))
        fee_jpy = float(item.get("fee", 0))
        return OrderResult(
            exchange=self.name,
            order_id=order_id,
            side=item["side"].lower(),
            pair="BTC/JPY",
            amount_btc=filled,
            price=price,
            fee_jpy=fee_jpy,
            status=self._map_status(item.get("status", "")),
            timestamp=time.time() * 1000,
            raw=item,
        )

    async def subscribe_ticker(
        self,
        pair: str,
        callback: Callable[[Ticker], Awaitable[None]],
    ) -> None:
        symbol = self._symbol(pair)
        channel = f"ticker.{symbol}"
        session = aiohttp.ClientSession()
        try:
            async with session.ws_connect(_WS_PUBLIC) as ws:
                await ws.send_json({"command": "subscribe", "channel": channel})
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        ticker = Ticker(
                            exchange=self.name,
                            pair=pair,
                            bid=float(data.get("bid", 0)),
                            ask=float(data.get("ask", 0)),
                            last=float(data.get("last", 0)),
                            timestamp=time.time() * 1000,
                        )
                        await callback(ticker)
        finally:
            await session.close()

    async def subscribe_orderbook(
        self,
        pair: str,
        callback: Callable[[OrderBook], Awaitable[None]],
    ) -> None:
        symbol = self._symbol(pair)
        channel = f"orderbooks.{symbol}"
        session = aiohttp.ClientSession()
        try:
            async with session.ws_connect(_WS_PUBLIC) as ws:
                await ws.send_json({"command": "subscribe", "channel": channel})
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        ob = OrderBook(
                            exchange=self.name,
                            pair=pair,
                            bids=[(float(b["price"]), float(b["size"])) for b in data.get("bids", [])[:20]],
                            asks=[(float(a["price"]), float(a["size"])) for a in data.get("asks", [])[:20]],
                            timestamp=time.time() * 1000,
                        )
                        await callback(ob)
        finally:
            await session.close()

    async def disconnect(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    def _map_status(status: str) -> str:
        mapping = {
            "WAITING": "open",
            "ORDERED": "open",
            "MODIFYING": "open",
            "CANCELLING": "open",
            "EXECUTED": "filled",
            "EXPIRED": "cancelled",
            "CANCELED": "cancelled",
        }
        return mapping.get(status.upper(), status.lower())
