from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Optional

import aiohttp
from loguru import logger

if TYPE_CHECKING:
    from bot.risk.risk_manager import RiskManager
    from bot.state.portfolio import Portfolio
    from bot.strategies.composer import StrategyComposer
    from bot.scheduler.main_loop import BotMainLoop

# Discord API base
_API = "https://discord.com/api/v10"

HELP_TEXT = """**Trading Bot コマンド一覧**
```
/status          現在の損益・残高・稼働状態
/stop            Botの売買を一時停止
/start           Botの売買を再開
/config          現在の設定値を表示
/config <key> <value>  設定値を変更
  例: /config fast_period 12
      /config slow_period 26
      /config min_spread_pct 0.5
      /config max_trade_amount_jpy 5000
      /config dry_run true
/trades          直近10件の取引履歴
/help            このヘルプを表示
```"""

# 変更可能な設定キーとその型・パス
_EDITABLE_KEYS: dict[str, tuple[type, list[str]]] = {
    "fast_period":           (int,   ["strategy", "trend", "fast_period"]),
    "slow_period":           (int,   ["strategy", "trend", "slow_period"]),
    "signal_confirmation_bars": (int, ["strategy", "trend", "signal_confirmation_bars"]),
    "min_signal_confidence": (float, ["strategy", "trend", "min_signal_confidence"]),
    "timeframe":             (str,   ["strategy", "trend", "timeframe"]),
    "min_spread_pct":        (float, ["strategy", "arbitrage", "min_spread_pct"]),
    "cooldown_seconds":      (int,   ["strategy", "arbitrage", "cooldown_seconds"]),
    "max_trade_amount_jpy":  (float, ["risk", "max_trade_amount_jpy"]),
    "max_daily_loss_jpy":    (float, ["risk", "max_daily_loss_jpy"]),
    "dry_run":               (bool,  ["bot", "dry_run"]),
    "active":                (str,   ["strategy", "active"]),
}


class DiscordCommandBot:
    """
    Discord Gateway に接続し、テキストコマンドを受信して Bot を操作する。
    Webhook とは別のBot Tokenが必要。
    """

    def __init__(
        self,
        bot_token: str,
        guild_id: str,
        command_channel_id: str,
        main_loop: "BotMainLoop",
    ) -> None:
        self._token = bot_token
        self._guild_id = guild_id
        self._command_channel_id = command_channel_id
        self._main_loop = main_loop
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._heartbeat_interval: float = 41.25
        self._sequence: Optional[int] = None
        self._running = False

    # ------------------------------------------------------------------ #
    # Public                                                               #
    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        if not self._token:
            logger.info("Discord Bot Token not configured — command bot disabled")
            return

        self._running = True
        while self._running:
            try:
                await self._connect()
            except Exception as e:
                logger.warning(f"Discord command bot disconnected: {e}. Reconnecting in 10s...")
                await asyncio.sleep(10)

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()

    # ------------------------------------------------------------------ #
    # Gateway                                                              #
    # ------------------------------------------------------------------ #

    async def _connect(self) -> None:
        _headers = {
            "Authorization": f"Bot {self._token}",
            "User-Agent": "DiscordBot (trading-bot, 1.0)",
        }
        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
        self._session = aiohttp.ClientSession(headers=_headers, connector=connector)
        # Get gateway URL
        async with self._session.get(f"{_API}/gateway") as resp:
            data = await resp.json()
        gateway_url = data["url"] + "?v=10&encoding=json"

        async with self._session.ws_connect(gateway_url) as ws:
            self._ws = ws
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_event(json.loads(msg.data))
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

    async def _handle_event(self, payload: dict) -> None:
        op = payload.get("op")
        data = payload.get("d", {})
        self._sequence = payload.get("s") or self._sequence

        if op == 10:  # Hello
            self._heartbeat_interval = data["heartbeat_interval"] / 1000
            asyncio.create_task(self._heartbeat_loop())
            await self._identify()

        elif op == 0:  # Dispatch
            event = payload.get("t")
            if event == "MESSAGE_CREATE":
                await self._on_message(data)

        elif op == 11:  # Heartbeat ACK
            pass

    async def _identify(self) -> None:
        await self._ws.send_json({
            "op": 2,
            "d": {
                "token": self._token,
                "intents": 1 << 9 | 1 << 15,  # GUILD_MESSAGES + MESSAGE_CONTENT
                "properties": {"os": "windows", "browser": "trading-bot", "device": "trading-bot"},
            },
        })

    async def _heartbeat_loop(self) -> None:
        while self._running and self._ws and not self._ws.closed:
            await self._ws.send_json({"op": 1, "d": self._sequence})
            await asyncio.sleep(self._heartbeat_interval)

    # ------------------------------------------------------------------ #
    # Message handling                                                     #
    # ------------------------------------------------------------------ #

    async def _on_message(self, data: dict) -> None:
        content: str = data.get("content", "").strip()
        channel_id = data.get("channel_id", "")
        author = data.get("author", {})

        # Botからのメッセージは無視
        if author.get("bot"):
            return

        # チャンネル制限（設定されている場合）
        if self._command_channel_id and channel_id != self._command_channel_id:
            return

        if not content.startswith("/"):
            return

        parts = content.split()
        cmd = parts[0].lower()

        response = await self._dispatch(cmd, parts[1:])
        if response:
            await self._send_message(channel_id, response)

    async def _dispatch(self, cmd: str, args: list[str]) -> Optional[str]:
        if cmd == "/help":
            return HELP_TEXT

        if cmd == "/status":
            return self._cmd_status()

        if cmd == "/stop":
            return self._cmd_stop()

        if cmd == "/start":
            return self._cmd_start()

        if cmd == "/trades":
            return await self._cmd_trades()

        if cmd == "/config":
            if not args:
                return self._cmd_config_show()
            if len(args) >= 2:
                return self._cmd_config_set(args[0], args[1])
            return "使い方: `/config <key> <value>`\n`/config` で設定一覧を表示"

        return None

    # ------------------------------------------------------------------ #
    # Commands                                                             #
    # ------------------------------------------------------------------ #

    def _cmd_status(self) -> str:
        portfolio = self._main_loop._portfolio.summary()
        risk_summary = self._main_loop._risk.get_daily_summary()
        paused = getattr(self._main_loop, "_paused", False)
        dry = self._main_loop._config.bot.dry_run

        state = "⏸ 一時停止中" if paused else ("🟡 DRY RUN" if dry else "🟢  稼働中")
        pnl = portfolio["todays_pnl"]
        sign = "+" if pnl >= 0 else ""

        loss_used = risk_summary['daily_loss_used']
        loss_limit = risk_summary['daily_loss_limit']
        lines = [
            f"**Bot ステータス** {state}",
            "```",
            f"本日損益 : {sign}{pnl:,.0f} JPY",
            f"損失使用 : {loss_used:,.0f} / {loss_limit:,.0f} JPY",
            "---",
        ]
        for ex, bal in portfolio["balances"].items():
            lines.append(f"{ex}: {bal['jpy']:,.0f} JPY  {bal['btc']:.6f} BTC")
        lines.append("```")
        return "\n".join(lines)

    def _cmd_stop(self) -> str:
        self._main_loop._paused = True
        logger.info("Bot paused via Discord command")
        return "⏸ **売買を一時停止しました。**\n`/start` で再開できます。"

    def _cmd_start(self) -> str:
        self._main_loop._paused = False
        logger.info("Bot resumed via Discord command")
        return "▶️ **売買を再開しました。**"

    async def _cmd_trades(self) -> str:
        trades = await self._main_loop._tracker.get_recent_trades(10)
        if not trades:
            return "取引履歴がありません。"

        lines = ["**直近の取引履歴**", "```"]
        for t in trades:
            side_mark = "買" if t["side"] == "buy" else "売"
            lines.append(
                f"{side_mark} {t['exchange']:<10} "
                f"{t['amount_btc']:.5f}BTC @ ¥{t['price']:,.0f} [{t['strategy']}]"
            )
        lines.append("```")
        return "\n".join(lines)

    def _cmd_config_show(self) -> str:
        cfg = self._main_loop._config
        t = cfg.strategy.trend
        a = cfg.strategy.arbitrage
        r = cfg.risk

        lines = [
            "**現在の設定値**",
            "```",
            "[Strategy]",
            f"  active                   : {cfg.strategy.active}",
            f"  dry_run                  : {cfg.bot.dry_run}",
            "[Trend Following]",
            f"  timeframe                : {t.timeframe}",
            f"  fast_period              : {t.fast_period}",
            f"  slow_period              : {t.slow_period}",
            f"  signal_confirmation_bars : {t.signal_confirmation_bars}",
            f"  min_signal_confidence    : {t.min_signal_confidence}",
            "[Arbitrage]",
            f"  min_spread_pct           : {a.min_spread_pct}",
            f"  cooldown_seconds         : {a.cooldown_seconds}",
            "[Risk]",
            f"  max_trade_amount_jpy     : {r.max_trade_amount_jpy:,.0f} JPY",
            f"  max_daily_loss_jpy       : {r.max_daily_loss_jpy:,.0f} JPY",
            "```",
            "Change: `/config <key> <value>`",
        ]
        return "\n".join(lines)

    def _cmd_config_set(self, key: str, raw_value: str) -> str:
        if key not in _EDITABLE_KEYS:
            valid = ", ".join(_EDITABLE_KEYS.keys())
            return f"❌ 不明なキー: `{key}`\n変更可能: `{valid}`"

        typ, path = _EDITABLE_KEYS[key]

        try:
            if typ == bool:
                value = raw_value.lower() in ("true", "1", "yes", "on")
            else:
                value = typ(raw_value)
        except ValueError:
            return f"❌ 値の形式が不正です: `{raw_value}` ({typ.__name__} が必要)"

        # ネストされた設定オブジェクトを辿って値を更新
        cfg = self._main_loop._config
        obj = cfg
        for attr in path[:-1]:
            obj = getattr(obj, attr)
        old_value = getattr(obj, path[-1])
        object.__setattr__(obj, path[-1], value)

        logger.info(f"Config changed via Discord: {key} {old_value} → {value}")
        return f"✅ `{key}` を `{old_value}` → `{value}` に変更しました。\n⚠️ この変更は再起動すると元に戻ります。config.yaml も更新してください。"

    # ------------------------------------------------------------------ #
    # HTTP helper                                                          #
    # ------------------------------------------------------------------ #

    async def _send_message(self, channel_id: str, content: str) -> None:
        url = f"{_API}/channels/{channel_id}/messages"
        headers = {
            "Authorization": f"Bot {self._token}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (trading-bot, 1.0)",
        }
        try:
            async with self._session.post(
                url,
                json={"content": content},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    logger.warning(f"Discord message send failed {resp.status}: {body}")
        except Exception as e:
            logger.warning(f"Discord send_message error: {e}")
