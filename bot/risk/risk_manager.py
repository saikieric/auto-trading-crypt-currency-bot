from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from loguru import logger

from bot.config.schema import RiskConfig
from bot.state.portfolio import Portfolio
from bot.strategies.base import TradeSignal, SignalType


@dataclass
class ApprovedOrder:
    """Only RiskManager.approve() can produce this type. OrderRouter accepts nothing else."""
    signal: TradeSignal
    pair: str
    side: str
    buy_exchange: str
    sell_exchange: str
    amount_jpy: float
    amount_btc: float
    is_arbitrage: bool


@dataclass
class RejectedOrder:
    signal: TradeSignal
    reason: str


def _is_approved(result: object) -> bool:
    return isinstance(result, ApprovedOrder)


class RiskManager:
    def __init__(
        self,
        config: RiskConfig,
        portfolio: Portfolio,
        state_path: str = "storage/state.json",
        dry_run: bool = True,
    ) -> None:
        self._config = config
        self._portfolio = portfolio
        self._state_path = Path(state_path)
        self._dry_run = dry_run
        self._load_state()

    def _load_state(self) -> None:
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                self._portfolio._todays_realized_pnl = data.get("todays_pnl", 0.0)
            except Exception:
                pass

    def _save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"todays_pnl": self._portfolio.get_todays_realized_pnl(), "ts": time.time()}
        self._state_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def reset_daily_counters(self) -> None:
        self._portfolio.reset_daily_pnl()
        self._save_state()
        logger.info("Daily risk counters reset (JST midnight)")

    async def approve(
        self, signal: TradeSignal, current_btc_price: float = 0.0
    ) -> Union[ApprovedOrder, RejectedOrder]:
        # 1. Dry-run: approve but mark as simulation (execution layer handles it)
        if self._dry_run:
            logger.info(f"[DRY RUN] Signal received: {signal.strategy} {signal.signal.value}")

        # 2. Daily loss limit
        daily_loss = self._portfolio.get_todays_realized_loss()
        if daily_loss >= self._config.max_daily_loss_jpy:
            reason = (
                f"Daily loss limit reached: ¥{daily_loss:,.0f} >= "
                f"¥{self._config.max_daily_loss_jpy:,.0f}"
            )
            logger.warning(f"RISK REJECT: {reason}")
            return RejectedOrder(signal=signal, reason=reason)

        # 3. Open position count
        open_positions = self._portfolio.count_open_positions()
        if open_positions >= self._config.max_open_positions:
            reason = f"Max open positions reached: {open_positions}/{self._config.max_open_positions}"
            logger.warning(f"RISK REJECT: {reason}")
            return RejectedOrder(signal=signal, reason=reason)

        # 4. Clamp trade size
        amount_jpy = min(signal.suggested_amount_jpy, self._config.max_trade_amount_jpy)

        if current_btc_price <= 0:
            # Estimate from signal metadata
            buy_ask = signal.metadata.get("buy_ask", 0.0)
            current_btc_price = buy_ask if buy_ask > 0 else 1.0

        amount_btc = amount_jpy / current_btc_price if current_btc_price > 0 else 0.0

        # 5. Balance check
        is_arbitrage = bool(signal.buy_exchange and signal.sell_exchange and
                            signal.buy_exchange != signal.sell_exchange)

        if signal.signal == SignalType.BUY or is_arbitrage:
            buy_ex = signal.buy_exchange
            available_jpy = self._portfolio.get_available_jpy(buy_ex)
            if available_jpy < amount_jpy and not self._dry_run:
                reason = (
                    f"Insufficient JPY on {buy_ex}: "
                    f"need ¥{amount_jpy:,.0f}, have ¥{available_jpy:,.0f}"
                )
                logger.warning(f"RISK REJECT: {reason}")
                return RejectedOrder(signal=signal, reason=reason)

        if is_arbitrage:
            sell_ex = signal.sell_exchange
            available_btc = self._portfolio.get_available_btc(sell_ex)
            if available_btc < amount_btc and not self._dry_run:
                reason = (
                    f"Insufficient BTC on {sell_ex}: "
                    f"need {amount_btc:.6f}, have {available_btc:.6f}"
                )
                logger.warning(f"RISK REJECT: {reason}")
                return RejectedOrder(signal=signal, reason=reason)

        side = signal.signal.value  # "buy" or "sell"

        approved = ApprovedOrder(
            signal=signal,
            pair=signal.pair,
            side=side,
            buy_exchange=signal.buy_exchange,
            sell_exchange=signal.sell_exchange,
            amount_jpy=amount_jpy,
            amount_btc=amount_btc,
            is_arbitrage=is_arbitrage,
        )
        logger.info(
            f"RISK APPROVED: {signal.strategy} {side} ¥{amount_jpy:,.0f} "
            f"({amount_btc:.6f} BTC) | confidence={signal.confidence:.2f}"
        )
        return approved

    def get_daily_summary(self) -> dict:
        return {
            "todays_pnl": self._portfolio.get_todays_realized_pnl(),
            "daily_loss_used": self._portfolio.get_todays_realized_loss(),
            "daily_loss_limit": self._config.max_daily_loss_jpy,
            "loss_pct_used": (
                self._portfolio.get_todays_realized_loss()
                / self._config.max_daily_loss_jpy * 100
            ),
            "portfolio": self._portfolio.summary(),
        }
