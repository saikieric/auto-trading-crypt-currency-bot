from __future__ import annotations

from typing import Dict, Optional, Tuple

from bot.data.market_data import Ticker
from bot.exchanges.base import ExchangeAdapter


class ExchangeAggregator:
    def __init__(self, adapters: Dict[str, ExchangeAdapter]) -> None:
        self._adapters = adapters
        # keyed by (exchange, pair) to support multiple pairs per exchange
        self._latest_tickers: Dict[Tuple[str, str], Ticker] = {}

    def update_ticker(self, ticker: Ticker) -> None:
        self._latest_tickers[(ticker.exchange, ticker.pair)] = ticker

    def get_ticker(self, exchange: str, pair: Optional[str] = None) -> Optional[Ticker]:
        if pair:
            return self._latest_tickers.get((exchange, pair))
        # backward compat: return the most recently updated ticker for this exchange
        result = None
        for (ex, _), t in self._latest_tickers.items():
            if ex == exchange:
                if result is None or t.timestamp > result.timestamp:
                    result = t
        return result

    def get_all_tickers(self) -> Dict[str, Ticker]:
        # backward compat: return latest ticker per exchange
        result: Dict[str, Ticker] = {}
        for (exchange, _), ticker in self._latest_tickers.items():
            if exchange not in result or ticker.timestamp > result[exchange].timestamp:
                result[exchange] = ticker
        return result

    def get_best_ask(self, pair: str = "BTC/JPY") -> Tuple[str, float]:
        best_exchange = ""
        best_price = float("inf")
        for (name, p), ticker in self._latest_tickers.items():
            if p == pair and ticker.ask < best_price:
                best_price = ticker.ask
                best_exchange = name
        return best_exchange, best_price

    def get_best_bid(self, pair: str = "BTC/JPY") -> Tuple[str, float]:
        best_exchange = ""
        best_price = 0.0
        for (name, p), ticker in self._latest_tickers.items():
            if p == pair and ticker.bid > best_price:
                best_price = ticker.bid
                best_exchange = name
        return best_exchange, best_price

    def get_max_spread(self, pair: str = "BTC/JPY") -> Tuple[str, str, float]:
        buy_exchange, best_ask = self.get_best_ask(pair)
        sell_exchange, best_bid = self.get_best_bid(pair)

        if not buy_exchange or not sell_exchange or buy_exchange == sell_exchange:
            return "", "", 0.0
        if best_ask == 0:
            return "", "", 0.0

        spread_pct = (best_bid - best_ask) / best_ask * 100
        return buy_exchange, sell_exchange, spread_pct

    def get_adapter(self, exchange: str) -> Optional[ExchangeAdapter]:
        return self._adapters.get(exchange)

    @property
    def adapters(self) -> Dict[str, ExchangeAdapter]:
        return self._adapters
