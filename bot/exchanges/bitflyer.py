from __future__ import annotations

import asyncio
import time
from typing import Callable, Awaitable

import ccxt

from bot.data.market_data import Ticker, OHLCV, OrderBook, OrderResult
from bot.exchanges.base import ExchangeAdapter

_TIMEFRAME_MAP = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m",
    "30m": "30m", "1h": "1h", "4h": "4h", "1d": "1d",
}


def _run(fn):
    """Run a synchronous ccxt call in a thread pool so it doesn't block the event loop."""
    return asyncio.get_event_loop().run_in_executor(None, fn)


class BitflyerAdapter(ExchangeAdapter):
    name = "bitflyer"

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._exchange = ccxt.bitflyer({
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
        # bitFlyer は OHLCV API 未対応のため、fetch_trades から candle を再構成
        try:
            # 最新から limit*100 件の trade を取得（十分なデータを確保）
            trades = await _run(lambda: self._exchange.fetch_trades(pair, limit=limit * 100))
            if not trades:
                # fallback: 現在のティッカーで 1 本のキャンドルを返す
                ticker = await self.fetch_ticker(pair)
                return [OHLCV(
                    timestamp=int(ticker.timestamp),
                    open=ticker.last,
                    high=ticker.last,
                    low=ticker.last,
                    close=ticker.last,
                    volume=0.0,
                )]

            # timeframe を秒に変換
            tf_seconds = {
                "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
                "4h": 14400, "1d": 86400,
            }.get(timeframe, 900)

            # trade を timeframe ごとに bucket にまとめる
            buckets = {}
            for trade in trades:
                # trade timestamp を timeframe 単位に丸める
                bucket_key = (trade["timestamp"] // 1000) // tf_seconds
                if bucket_key not in buckets:
                    buckets[bucket_key] = []
                buckets[bucket_key].append(trade)

            # bucket をソートして oldest から newest へ
            sorted_buckets = sorted(buckets.items())

            # 各 bucket を OHLCV に変換
            result = []
            for bucket_key, bucket_trades in sorted_buckets[-limit:]:  # 最新 limit 本のみ
                prices = [t["price"] for t in bucket_trades]
                volumes = [t["amount"] for t in bucket_trades]
                timestamp = bucket_key * tf_seconds * 1000

                ohlcv = OHLCV(
                    timestamp=timestamp,
                    open=bucket_trades[0]["price"],
                    high=max(prices),
                    low=min(prices),
                    close=bucket_trades[-1]["price"],
                    volume=sum(volumes),
                )
                result.append(ohlcv)

            return result if result else [OHLCV(
                timestamp=int(time.time() * 1000),
                open=0, high=0, low=0, close=0, volume=0,
            )]
        except Exception as e:
            raise RuntimeError(f"Failed to fetch OHLCV from trades: {e}") from e

    async def fetch_balance(self) -> dict[str, float]:
        raw = await _run(lambda: self._exchange.fetch_balance())
        return {
            "JPY": raw.get("JPY", {}).get("free", 0.0),
            "BTC": raw.get("BTC", {}).get("free", 0.0),
        }

    async def place_market_order(self, side: str, amount_btc: float) -> OrderResult:
        amount_btc = round(amount_btc, 8)  # bitFlyer最小単位: 0.00000001 BTC
        raw = await _run(lambda: self._exchange.create_order(
            "BTC/JPY", "market", side, amount_btc
        ))
        order_id = str(raw.get("id", ""))
        # bitFlyerのcreate_orderはIDしか返さないため約定情報をfetchで取得
        await asyncio.sleep(1.0)
        try:
            result = await self.fetch_order_status(order_id)
            result.side = result.side or side
            result.pair = result.pair or "BTC/JPY"
            return result
        except Exception:
            raw["side"] = side
            return self._parse_order(raw)

    async def place_limit_order(self, side: str, amount_btc: float, price: float) -> OrderResult:
        amount_btc = round(amount_btc, 8)
        raw = await _run(lambda: self._exchange.create_order(
            "BTC/JPY", "limit", side, amount_btc, price
        ))
        if not raw.get("side"):
            raw["side"] = side
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
        # bitFlyer は WebSocket 未対応のため REST ポーリングで代替
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
        # 未使用だが抽象メソッドの実装として必要
        while True:
            await asyncio.sleep(60)

    async def disconnect(self) -> None:
        pass

    def _parse_order(self, raw: dict) -> OrderResult:
        # ccxtがbitFlyer生APIのフィールドを変換した結果を使う
        # 生API: executed_size→filled, average_price→average, child_order_state→status
        # create_orderはIDのみ返すためほぼ全フィールドがNoneになりうる
        fee = 0.0
        if raw.get("fee"):
            fee = float(raw["fee"].get("cost") or 0.0)

        side = (raw.get("side") or "buy").lower()
        pair = raw.get("symbol") or "BTC/JPY"
        amount_btc = float(raw.get("filled") or raw.get("amount") or 0.0)
        price = float(raw.get("average") or raw.get("price") or 0.0)
        status = self._map_status(raw.get("status") or "") or "open"
        order_id = str(raw.get("id") or raw.get("child_order_acceptance_id") or "")
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
