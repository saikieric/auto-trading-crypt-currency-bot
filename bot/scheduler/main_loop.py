from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger

from bot.config.schema import Config
from bot.data.feed_manager import FeedManager
from bot.data.market_data import Ticker, OrderResult
from bot.data.ohlcv_store import OhlcvStore
from bot.exchanges.aggregator import ExchangeAggregator
from bot.exchanges.bitbank import BitbankAdapter
from bot.exchanges.bitflyer import BitflyerAdapter
from bot.exchanges.gmo import GmoAdapter
from bot.execution.order_router import OrderRouter
from bot.execution.order_tracker import OrderTracker
from bot.notifications.discord import DiscordNotifier
from bot.notifications.discord_bot import DiscordCommandBot
from bot.risk.risk_manager import ApprovedOrder, RiskManager
from bot.state.portfolio import Portfolio
from bot.strategies.arbitrage import ArbitrageStrategy
from bot.strategies.composer import StrategyComposer
from bot.strategies.sol_scalp import SolScalpStrategy
from bot.strategies.trend_following import TrendFollowingStrategy

_JST = ZoneInfo("Asia/Tokyo")


class BotMainLoop:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._running = False
        self._paused = False  # /stop コマンドで一時停止

        # --- Notification ---
        notif_cfg = config.notifications
        self._notifier = DiscordNotifier(
            webhook_url=notif_cfg.discord_webhook_url,
            dry_run=config.bot.dry_run,
            notify_on=notif_cfg.notify_on,
        )

        # --- Exchanges ---
        self._adapters = self._build_adapters()

        # --- Aggregator ---
        self._aggregator = ExchangeAggregator(self._adapters)

        # --- Portfolio ---
        self._portfolio = Portfolio(config.risk.starting_capital_jpy)

        # --- Risk manager ---
        # bitFlyer（唯一有効な取引所）の手数料を取得
        ex_configs = config.exchanges
        active_fee = next(
            (c.trading_fee_pct for c in ex_configs.values() if c.enabled),
            0.0,
        )
        self._risk = RiskManager(
            config=config.risk,
            portfolio=self._portfolio,
            state_path=config.storage.state_path,
            dry_run=config.bot.dry_run,
            fee_pct=active_fee,
        )

        # --- Order tracking ---
        self._tracker = OrderTracker(config.storage.db_path)

        # --- OHLCV persistence ---
        self._ohlcv_store = OhlcvStore(config.storage.db_path)

        # --- Order router ---
        self._router = OrderRouter(
            adapters=self._adapters,
            tracker=self._tracker,
            portfolio=self._portfolio,
            notifier=self._notifier,
            dry_run=config.bot.dry_run,
        )

        # --- Strategies ---
        risk_cfg = config.risk
        strategies = []

        # BTC/JPY トレンド（bitFlyer が有効な場合のみ）
        if "bitflyer" in self._adapters:
            strategies.append(
                TrendFollowingStrategy(
                    config=config.strategy.trend,
                    aggregator=self._aggregator,
                    max_trade_amount_jpy=risk_cfg.max_trade_amount_jpy,
                    pair="BTC/JPY",
                    strategy_name="trend_btc",
                )
            )

        # アービトラージ（複数取引所が有効な場合）
        if len(self._adapters) > 1:
            strategies.append(
                ArbitrageStrategy(
                    config=config.strategy.arbitrage,
                    aggregator=self._aggregator,
                    portfolio=self._portfolio,
                    max_trade_amount_jpy=risk_cfg.max_trade_amount_jpy,
                )
            )

        # SOL スキャルピング（Bitbank が有効な場合）
        if "bitbank" in self._adapters:
            strategies.append(
                SolScalpStrategy(
                    config=config.strategy.sol_scalp,
                    aggregator=self._aggregator,
                    portfolio=self._portfolio,
                )
            )

        if not strategies:
            raise RuntimeError("No strategies configured")

        self._composer = StrategyComposer(
            strategies=strategies,
            config=config.strategy,
        )

        # --- Feed manager ---
        feed_subscriptions = []
        for exchange in self._adapters:
            feed_subscriptions.append((exchange, "BTC/JPY"))
        if "bitbank" in self._adapters:
            feed_subscriptions.append(("bitbank", "SOL/JPY"))

        self._feed = FeedManager(
            adapters=self._adapters,
            aggregator=self._aggregator,
            on_ticker=self._on_ticker_update,
            subscriptions=feed_subscriptions,
        )

        # --- Discord command bot ---
        self._discord_cmd = DiscordCommandBot(
            bot_token=notif_cfg.discord_bot_token,
            guild_id=notif_cfg.discord_guild_id,
            command_channel_id=notif_cfg.discord_command_channel_id,
            main_loop=self,
        )

    def _build_adapters(self) -> dict:
        adapters = {}
        ex_cfg = self._config.exchanges

        if ex_cfg.get("bitflyer", None) and ex_cfg["bitflyer"].enabled:
            cfg = ex_cfg["bitflyer"]
            adapters["bitflyer"] = BitflyerAdapter(cfg.api_key, cfg.api_secret)

        if ex_cfg.get("gmo", None) and ex_cfg["gmo"].enabled:
            cfg = ex_cfg["gmo"]
            adapters["gmo"] = GmoAdapter(cfg.api_key, cfg.api_secret)

        if ex_cfg.get("bitbank", None) and ex_cfg["bitbank"].enabled:
            cfg = ex_cfg["bitbank"]
            adapters["bitbank"] = BitbankAdapter(cfg.api_key, cfg.api_secret)

        if not adapters:
            raise RuntimeError("No exchanges enabled in config")

        logger.info(f"Loaded exchanges: {list(adapters.keys())}")
        return adapters

    async def _on_ticker_update(self, tickers: dict[str, Ticker]) -> None:
        if self._paused:
            return
        signals = await self._composer.on_ticker_update(tickers)
        await self._process_signals(signals)

    async def _ohlcv_poller(self) -> None:
        """15m 足ポーラー（地合い判定用）"""
        scalp_cfg = self._config.strategy.sol_scalp
        data_cfg = self._config.data
        limit = data_cfg.ohlcv_history_candles
        timeframe = scalp_cfg.timeframe_trend  # "15m"

        # (exchange, pair) リスト
        targets: list[tuple[str, str]] = []
        if "bitflyer" in self._adapters:
            targets.append(("bitflyer", "BTC/JPY"))
        if "bitbank" in self._adapters:
            targets.append(("bitbank", "SOL/JPY"))

        # 起動時に DB から復元
        for exchange, pair in targets:
            cached = await self._ohlcv_store.load(exchange, pair, timeframe, limit)
            if cached:
                await self._composer.on_ohlcv_update(exchange, cached, timeframe)
                logger.info(f"Restored {len(cached)} {timeframe} candles for {exchange} {pair}")

        while self._running:
            if not self._paused:
                for exchange, pair in targets:
                    adapter = self._adapters.get(exchange)
                    if not adapter:
                        continue
                    try:
                        candles = await adapter.fetch_ohlcv(pair, timeframe, limit)
                        await self._ohlcv_store.save(exchange, pair, timeframe, candles)
                        signals = await self._composer.on_ohlcv_update(exchange, candles, timeframe)
                        await self._process_signals(signals)
                    except Exception as e:
                        logger.warning(f"OHLCV({timeframe}) poll failed for {exchange} {pair}: {e}")
            await asyncio.sleep(data_cfg.ohlcv_poll_interval_seconds)

    async def _ohlcv_1m_poller(self) -> None:
        """1m 足ポーラー（スキャルエントリー用）"""
        if "bitbank" not in self._adapters:
            return

        scalp_cfg = self._config.strategy.sol_scalp
        timeframe = scalp_cfg.timeframe_entry  # "1m"
        limit = 50
        adapter = self._adapters["bitbank"]

        # 起動時に DB から復元
        cached = await self._ohlcv_store.load("bitbank", "SOL/JPY", timeframe, limit)
        if cached:
            await self._composer.on_ohlcv_update("bitbank", cached, timeframe)
            logger.info(f"Restored {len(cached)} {timeframe} candles for bitbank SOL/JPY")

        while self._running:
            if not self._paused:
                try:
                    candles = await adapter.fetch_ohlcv("SOL/JPY", timeframe, limit)
                    await self._ohlcv_store.save("bitbank", "SOL/JPY", timeframe, candles)
                    signals = await self._composer.on_ohlcv_update("bitbank", candles, timeframe)
                    await self._process_signals(signals)
                except Exception as e:
                    logger.warning(f"OHLCV(1m) poll failed for bitbank SOL/JPY: {e}")
            await asyncio.sleep(30)  # 30秒ごとに1m足を更新

    async def _process_signals(self, signals) -> None:
        for signal in signals:
            try:
                ticker = self._aggregator.get_ticker(
                    signal.buy_exchange or signal.sell_exchange,
                    signal.pair,
                )
                price = ticker.ask if ticker and ticker.ask > 0 else 0.0
                approved = await self._risk.approve(signal, current_btc_price=price)
                if isinstance(approved, ApprovedOrder):
                    await self._router.execute(approved)
            except Exception as e:
                logger.exception(f"Signal processing failed ({signal.strategy}): {e}")

    async def _sol_position_monitor(self) -> None:
        """SOL ポジションの TP / SL / タイム SL を監視して自動決済"""
        if "bitbank" not in self._adapters:
            return

        adapter = self._adapters["bitbank"]
        cfg = self._config.strategy.sol_scalp

        while self._running:
            await asyncio.sleep(5)
            if self._paused:
                continue

            sol_positions = [
                p for p in list(self._portfolio._open_positions)
                if p.pair == "SOL/JPY"
            ]
            if not sol_positions:
                continue

            try:
                ticker = await adapter.fetch_ticker("SOL/JPY")
            except Exception as e:
                logger.warning(f"SOL ticker fetch failed in position monitor: {e}")
                continue

            now_ms = time.time() * 1000

            for pos in sol_positions:
                if pos.price <= 0:
                    logger.warning(f"[sol_scalp] Position {pos.order_id} has price=0, skipping TP/SL but applying TimeSL")
                    elapsed_min = (now_ms - pos.timestamp) / 1000 / 60
                    if elapsed_min >= cfg.time_stop_minutes and pos.timestamp > 0:
                        try:
                            sell = await adapter.place_market_order("sell", pos.amount_btc, "SOL/JPY")
                            await self._tracker.register(sell, strategy="sol_scalp_exit")
                            await self._portfolio.update_from_order(sell)
                            self._portfolio.remove_open_position(pos.order_id)
                            await self._notifier.send(
                                f"🔔 **SOL EXIT** TimeSL (price=0 position) {elapsed_min:.0f}min\n"
                                f"売り: `{sell.amount_btc:.4f} SOL @ ¥{sell.price:,.2f}`"
                            )
                        except Exception as e:
                            logger.error(f"Failed to exit zero-price SOL position {pos.order_id}: {e}")
                    continue
                pnl_pct = (ticker.bid - pos.price) / pos.price * 100
                elapsed_min = (now_ms - pos.timestamp) / 1000 / 60

                exit_reason = None
                if pnl_pct >= cfg.tp_pct:
                    exit_reason = f"TP +{pnl_pct:.2f}%"
                elif pnl_pct <= -cfg.sl_pct:
                    exit_reason = f"SL {pnl_pct:.2f}%"
                elif elapsed_min >= cfg.time_stop_minutes:
                    exit_reason = f"TimeSL {elapsed_min:.0f}min ({pnl_pct:+.2f}%)"

                if not exit_reason:
                    continue

                logger.info(f"[sol_scalp] Exiting position {pos.order_id}: {exit_reason}")
                try:
                    sell = await adapter.place_market_order("sell", pos.amount_btc, "SOL/JPY")
                    await self._tracker.register(sell, strategy="sol_scalp_exit")
                    await self._portfolio.update_from_order(sell)
                    self._portfolio.remove_open_position(pos.order_id)
                    await self._notifier.send(
                        f"🔔 **SOL EXIT** {exit_reason}\n"
                        f"売り: `{sell.amount_btc:.4f} SOL @ ¥{sell.price:,.2f}`\n"
                        f"エントリー: `¥{pos.price:,.2f}` → `¥{sell.price:,.2f}`"
                    )
                except Exception as e:
                    logger.error(f"Failed to exit SOL position {pos.order_id}: {e}")
                    await self._notifier.send_error(e, f"SOL exit failed: {pos.order_id}")

    async def _balance_sync_task(self) -> None:
        while self._running:
            await asyncio.sleep(300)  # every 5 minutes
            for exchange, adapter in self._adapters.items():
                try:
                    balances = await adapter.fetch_balance()
                    self._portfolio.set_balance(
                        exchange,
                        jpy=balances.get("JPY", 0.0),
                        btc=balances.get("BTC", 0.0),
                        sol=balances.get("SOL", 0.0),
                    )
                    logger.debug(f"Balance sync {exchange}: {balances}")
                except Exception as e:
                    logger.warning(f"Balance sync failed for {exchange}: {e}")

    async def _daily_reset_task(self) -> None:
        while self._running:
            now = datetime.now(_JST)
            next_midnight = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            await asyncio.sleep((next_midnight - now).total_seconds())
            self._risk.reset_daily_counters()
            summary = self._risk.get_daily_summary()
            await self._notifier.send_daily_summary(summary)

    async def _heartbeat_task(self) -> None:
        interval = self._config.notifications.heartbeat_interval_hours * 3600
        while self._running:
            await asyncio.sleep(interval)
            portfolio = self._portfolio.summary()
            sol = portfolio.get("total_sol", 0.0)
            btc = portfolio.get("total_btc", 0.0)
            msg = (
                f"*Heartbeat* Bot稼働中\n"
                f"JPY合計: `¥{portfolio['total_jpy']:,.0f}`\n"
                f"BTC合計: `{btc:.6f} BTC`\n"
                f"SOL合計: `{sol:.4f} SOL`\n"
                f"本日損益: `¥{portfolio['todays_pnl']:,.0f}`"
            )
            await self._notifier.send(msg)

    async def run(self) -> None:
        self._running = True
        logger.info("Bot starting up...")

        await self._tracker.initialize()
        await self._ohlcv_store.initialize()

        # Initial balance fetch
        for exchange, adapter in self._adapters.items():
            try:
                balances = await adapter.fetch_balance()
                jpy = balances.get("JPY", 0.0)
                btc = balances.get("BTC", 0.0)
                sol = balances.get("SOL", 0.0)
                self._portfolio.set_balance(exchange, jpy=jpy, btc=btc, sol=sol)
                logger.info(f"Initial balance {exchange}: {balances}")

                # BTC保有量からオープンポジションを復元（再起動後も制限が効くように）
                if btc > 0.00001:
                    dummy = OrderResult(
                        exchange=exchange,
                        order_id=f"RESTORED_{exchange}",
                        side="buy",
                        pair="BTC/JPY",
                        amount_btc=btc,
                        price=0.0,
                        fee_jpy=0.0,
                        status="filled",
                        timestamp=0.0,
                    )
                    self._portfolio.add_open_position(dummy)
                    logger.info(f"Restored open position from balance: {btc:.6f} BTC on {exchange}")
            except Exception as e:
                logger.warning(f"Could not fetch initial balance for {exchange}: {e}")

        await self._notifier.send_startup(self._config.bot.dry_run)

        tasks = [
            asyncio.create_task(self._feed.run(), name="feed_manager"),
            asyncio.create_task(self._ohlcv_poller(), name="ohlcv_poller"),
            asyncio.create_task(self._ohlcv_1m_poller(), name="ohlcv_1m_poller"),
            asyncio.create_task(self._sol_position_monitor(), name="sol_position_monitor"),
            asyncio.create_task(self._balance_sync_task(), name="balance_sync"),
            asyncio.create_task(self._daily_reset_task(), name="daily_reset"),
            asyncio.create_task(self._heartbeat_task(), name="heartbeat"),
            asyncio.create_task(self._discord_cmd.run(), name="discord_command_bot"),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Main loop cancelled, shutting down...")
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        self._running = False
        logger.info("Shutting down bot...")

        for exchange, adapter in self._adapters.items():
            try:
                await adapter.disconnect()
            except Exception:
                pass

        self._risk._save_state()
        await self._discord_cmd.stop()
        await self._notifier.send_shutdown()
        logger.info("Bot stopped cleanly.")
