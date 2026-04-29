from __future__ import annotations

from typing import Dict, Optional, Tuple

from bot.data.market_data import Ticker
from bot.exchanges.base import ExchangeAdapter


class ExchangeAggregator:
    def __init__(self, adapters: Dict[str, ExchangeAdapter]) -> None:
        self._adapters = adapters
        self._latest_tickers: Dict[str, Ticker] = {}

    def update_ticker(self, ticker: Ticker) -> None:
        self._latest_tickers[ticker.exchange] = ticker

    def get_ticker(self, exchange: str) -> Optional[Ticker]:
        return self._latest_tickers.get(exchange)

    def get_all_tickers(self) -> Dict[str, Ticker]:
        return dict(self._latest_tickers)

    def get_best_ask(self, pair: str = "BTC/JPY") -> Tuple[str, float]:
        """Returns (exchange_name, best_ask_price) — cheapest ask across all exchanges."""
        best_exchange = ""
        best_price = float("inf")
        for name, ticker in self._latest_tickers.items():
            if ticker.pair == pair and ticker.ask < best_price:
                best_price = ticker.ask
                best_exchange = name
        return best_exchange, best_price

    def get_best_bid(self, pair: str = "BTC/JPY") -> Tuple[str, float]:
        """Returns (exchange_name, best_bid_price) — highest bid across all exchanges."""
        best_exchange = ""
        best_price = 0.0
        for name, ticker in self._latest_tickers.items():
            if ticker.pair == pair and ticker.bid > best_price:
                best_price = ticker.bid
                best_exchange = name
        return best_exchange, best_price

    def get_max_spread(self, pair: str = "BTC/JPY") -> Tuple[str, str, float]:
        """
        Returns (buy_exchange, sell_exchange, spread_pct).
        buy_exchange has the lowest ask, sell_exchange has the highest bid.
        """
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
