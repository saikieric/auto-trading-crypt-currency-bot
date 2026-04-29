from __future__ import annotations

import time
from typing import Optional, TYPE_CHECKING

import aiohttp
from loguru import logger

from bot.notifications.base import Notifier

if TYPE_CHECKING:
    from bot.data.market_data import OrderResult
    from bot.strategies.base import TradeSignal

_API_BASE = "https://api.telegram.org"
_ERROR_COOLDOWN = 60  # suppress repeated error logs for this many seconds


class TelegramNotifier(Notifier):
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        dry_run: bool = True,
        notify_on: Optional[list[str]] = None,
    ) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._dry_run = dry_run
        self._notify_on = notify_on or []
        self._last_error_ts: float = 0.0

    async def send(self, message: str, level: str = "info") -> None:
        if not self._token or not self._chat_id:
            logger.debug(f"Telegram not configured, skipping: {message[:80]}")
            return

        url = f"{_API_BASE}/bot{self._token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "Markdown",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise RuntimeError(f"Telegram API {resp.status}: {body}")
        except Exception as e:
            now = time.time()
            if now - self._last_error_ts > _ERROR_COOLDOWN:
                logger.warning(f"Telegram send failed: {e}")
                self._last_error_ts = now

    async def send_trade_notification(
        self, order: "OrderResult", signal: "TradeSignal"
    ) -> None:
        event = "trade_open" if signal.signal.value == "buy" else "trade_close"
        if event not in self._notify_on:
            return

        prefix = "\\[DRY RUN\\] " if self._dry_run else ""
        msg = (
            f"{prefix}*取引実行*\n"
            f"戦略: `{signal.strategy}`\n"
            f"種別: `{order.side.upper()}`\n"
            f"取引所: `{order.exchange}`\n"
            f"数量: `{order.amount_btc:.6f} BTC`\n"
            f"価格: `¥{order.price:,.0f}`\n"
            f"金額: `¥{order.total_jpy:,.0f}`\n"
            f"理由: {signal.reason}"
        )
        await self.send(msg)

    async def send_daily_summary(self, summary: dict) -> None:
        if "daily_summary" not in self._notify_on:
            return

        pnl = summary.get("todays_pnl", 0.0)
        loss_used = summary.get("daily_loss_used", 0.0)
        loss_limit = summary.get("daily_loss_limit", 0.0)
        portfolio = summary.get("portfolio", {})

        sign = "+" if pnl >= 0 else ""
        msg = (
            f"*日次サマリー*\n"
            f"本日損益: `{sign}¥{pnl:,.0f}`\n"
            f"損失使用: `¥{loss_used:,.0f} / ¥{loss_limit:,.0f}`\n"
            f"JPY合計: `¥{portfolio.get('total_jpy', 0):,.0f}`\n"
            f"BTC合計: `{portfolio.get('total_btc', 0):.6f} BTC`"
        )
        await self.send(msg)

    async def send_error(self, error: Exception, context: str) -> None:
        if "error" not in self._notify_on:
            return
        msg = f"*エラー*\n`{context}`\n```\n{type(error).__name__}: {error}\n```"
        await self.send(msg, level="error")

    async def send_startup(self, dry_run: bool) -> None:
        if "bot_start" not in self._notify_on:
            return
        mode = "\\[DRY RUN\\]" if dry_run else "\\[LIVE\\]"
        await self.send(f"*Bot起動* {mode}\nBTC/JPY 自動売買Bot が起動しました。")

    async def send_shutdown(self) -> None:
        if "bot_stop" not in self._notify_on:
            return
        await self.send("*Bot停止*\n自動売買Bot が正常に停止しました。")
