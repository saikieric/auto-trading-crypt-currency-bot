from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, Optional

from bot.data.market_data import OrderResult


@dataclass
class ExchangeBalance:
    jpy: float = 0.0
    btc: float = 0.0
    sol: float = 0.0

    def available_jpy(self) -> float:
        return max(0.0, self.jpy)

    def available_btc(self) -> float:
        return max(0.0, self.btc)

    def available_sol(self) -> float:
        return max(0.0, self.sol)


class Portfolio:
    def __init__(self, starting_capital_jpy: float) -> None:
        self._starting_capital = starting_capital_jpy
        self._balances: Dict[str, ExchangeBalance] = {}
        self._todays_realized_pnl: float = 0.0
        self._total_realized_pnl: float = 0.0
        self._open_positions: list[OrderResult] = []
        self._lock = asyncio.Lock()

    # --- Balance management ---

    def set_balance(self, exchange: str, jpy: float, btc: float, sol: float = 0.0) -> None:
        self._balances[exchange] = ExchangeBalance(jpy=jpy, btc=btc, sol=sol)

    def get_available_jpy(self, exchange: str) -> float:
        return self._balances.get(exchange, ExchangeBalance()).available_jpy()

    def get_available_btc(self, exchange: str) -> float:
        return self._balances.get(exchange, ExchangeBalance()).available_btc()

    def get_total_jpy(self) -> float:
        return sum(b.jpy for b in self._balances.values())

    def get_total_btc(self) -> float:
        return sum(b.btc for b in self._balances.values())

    def get_total_sol(self) -> float:
        return sum(b.sol for b in self._balances.values())

    # --- P&L ---

    def get_todays_realized_loss(self) -> float:
        """Returns today's realized loss as a positive number (loss = positive)."""
        return max(0.0, -self._todays_realized_pnl)

    def get_todays_realized_pnl(self) -> float:
        return self._todays_realized_pnl

    def get_total_realized_pnl(self) -> float:
        return self._total_realized_pnl

    def reset_daily_pnl(self) -> None:
        self._todays_realized_pnl = 0.0

    # --- Order updates ---

    async def update_from_order(self, order: OrderResult) -> None:
        async with self._lock:
            if order.status != "filled":
                return

            exchange = order.exchange
            if exchange not in self._balances:
                self._balances[exchange] = ExchangeBalance()

            bal = self._balances[exchange]

            base = order.pair.split("/")[0] if "/" in order.pair else "BTC"

            if order.side == "buy":
                cost_jpy = order.total_jpy + order.fee_jpy
                bal.jpy -= cost_jpy
                if base == "SOL":
                    bal.sol += order.amount_btc
                else:
                    bal.btc += order.amount_btc
                self._todays_realized_pnl -= order.fee_jpy
                self._total_realized_pnl -= order.fee_jpy
            elif order.side == "sell":
                proceeds_jpy = order.total_jpy - order.fee_jpy
                bal.jpy += proceeds_jpy
                if base == "SOL":
                    bal.sol -= order.amount_btc
                else:
                    bal.btc -= order.amount_btc
                self._todays_realized_pnl -= order.fee_jpy
                self._total_realized_pnl -= order.fee_jpy

    # --- Open positions ---

    def add_open_position(self, order: OrderResult) -> None:
        self._open_positions.append(order)

    def remove_open_position(self, order_id: str) -> None:
        self._open_positions = [o for o in self._open_positions if o.order_id != order_id]

    def count_open_positions(self, exchange: Optional[str] = None) -> int:
        if exchange:
            return sum(1 for o in self._open_positions if o.exchange == exchange)
        return len(self._open_positions)

    # --- Summary ---

    def summary(self) -> dict:
        return {
            "balances": {
                name: {"jpy": b.jpy, "btc": b.btc, "sol": b.sol}
                for name, b in self._balances.items()
            },
            "total_jpy": self.get_total_jpy(),
            "total_btc": self.get_total_btc(),
            "total_sol": self.get_total_sol(),
            "todays_pnl": self._todays_realized_pnl,
            "total_pnl": self._total_realized_pnl,
            "open_positions": len(self._open_positions),
        }
