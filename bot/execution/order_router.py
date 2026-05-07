from __future__ import annotations

import asyncio
from typing import Dict, TYPE_CHECKING

from loguru import logger

from bot.data.market_data import OrderResult
from bot.exchanges.base import ExchangeAdapter
from bot.execution.order_tracker import OrderTracker
from bot.risk.risk_manager import ApprovedOrder
from bot.state.portfolio import Portfolio

if TYPE_CHECKING:
    from bot.notifications.base import Notifier


class OrderRouter:
    def __init__(
        self,
        adapters: Dict[str, ExchangeAdapter],
        tracker: OrderTracker,
        portfolio: Portfolio,
        notifier: "Notifier",
        dry_run: bool = True,
    ) -> None:
        self._adapters = adapters
        self._tracker = tracker
        self._portfolio = portfolio
        self._notifier = notifier
        self._dry_run = dry_run

    async def execute(self, order: ApprovedOrder) -> None:
        if self._dry_run:
            await self._simulate(order)
            return

        if order.is_arbitrage:
            await self._execute_arbitrage(order)
        else:
            await self._execute_single(order)

    async def _execute_single(self, order: ApprovedOrder) -> None:
        exchange = order.buy_exchange or order.sell_exchange
        adapter = self._adapters.get(exchange)
        if not adapter:
            logger.error(f"No adapter for exchange: {exchange}")
            return

        try:
            # 売りの場合は直前にリアルタイム残高を取得して使う
            amount_btc = order.amount_btc
            pair = order.pair or "BTC/JPY"

            # ベース通貨を判定（BTC/JPY→BTC、SOL/JPY→SOL）
            base_currency = pair.split("/")[0] if "/" in pair else "BTC"

            if order.side == "sell":
                try:
                    balances = await adapter.fetch_balance()
                    available_base = balances.get(base_currency, 0.0)
                    self._portfolio.set_balance(
                        exchange,
                        jpy=balances.get("JPY", 0.0),
                        btc=balances.get("BTC", 0.0),
                    )
                except Exception as e:
                    logger.warning(f"Failed to refresh balance before sell: {e}")
                    available_base = self._portfolio.get_available_btc(exchange)

                # 最小注文サイズチェック（BTC: 0.001、SOL: 0.01）
                min_amount = 0.001 if base_currency == "BTC" else 0.01
                if available_base < min_amount:
                    logger.warning(
                        f"{base_currency} balance too small to sell: "
                        f"{available_base:.8f} (min {min_amount})"
                    )
                    return
                # 手数料分（概ね0.12〜0.15%）をベース通貨で差し引いてから売る
                fee_rate = 0.0015
                amount_btc = round(available_base * (1 - fee_rate), 8)

            if order.use_maker and not order.is_arbitrage:
                result = await self._place_maker_order(
                    adapter, order.side, amount_btc, pair, order.maker_timeout_seconds
                )
            else:
                result = await adapter.place_market_order(order.side, amount_btc, pair=pair)
            await self._tracker.register(result, strategy=order.signal.strategy)
            await self._portfolio.update_from_order(result)

            if order.side == "buy" and result.status == "filled":
                self._portfolio.add_open_position(result)
            elif order.side == "sell":
                # 同じ取引所・ペアの買いポジションをクリア
                for pos in list(self._portfolio._open_positions):
                    if pos.exchange == exchange and pos.pair == pair:
                        self._portfolio.remove_open_position(pos.order_id)

            await self._notifier.send_trade_notification(result, order.signal)
            logger.info(
                f"Order executed: {exchange} {order.side} "
                f"{result.amount_btc:.6f} BTC @ ¥{result.price:,.0f}"
            )
        except Exception as e:
            logger.exception(f"Order execution failed on {exchange}: {e}")
            await self._notifier.send_error(e, f"Order execution failed on {exchange}")

    async def _execute_arbitrage(self, order: ApprovedOrder) -> None:
        buy_adapter = self._adapters.get(order.buy_exchange)
        sell_adapter = self._adapters.get(order.sell_exchange)

        if not buy_adapter or not sell_adapter:
            logger.error("Missing adapter for arbitrage legs")
            return

        buy_result, sell_result = await asyncio.gather(
            buy_adapter.place_market_order("buy", order.amount_btc),
            sell_adapter.place_market_order("sell", order.amount_btc),
            return_exceptions=True,
        )

        buy_ok = isinstance(buy_result, OrderResult) and buy_result.status == "filled"
        sell_ok = isinstance(sell_result, OrderResult) and sell_result.status == "filled"

        if buy_ok and sell_ok:
            await self._tracker.register(buy_result, strategy=order.signal.strategy)
            await self._tracker.register(sell_result, strategy=order.signal.strategy)
            await self._portfolio.update_from_order(buy_result)
            await self._portfolio.update_from_order(sell_result)
            await self._notifier.send_trade_notification(buy_result, order.signal)
            logger.info(
                f"Arbitrage executed: buy {order.buy_exchange} "
                f"@ ¥{buy_result.price:,.0f}, sell {order.sell_exchange} "
                f"@ ¥{sell_result.price:,.0f}"
            )
        else:
            await self._handle_partial_fill(order, buy_result, sell_result, buy_ok, sell_ok)

    async def _handle_partial_fill(
        self,
        order: ApprovedOrder,
        buy_result: object,
        sell_result: object,
        buy_ok: bool,
        sell_ok: bool,
    ) -> None:
        """One leg filled, the other failed — attempt immediate reversal."""
        msg = (
            f"CRITICAL: Arbitrage partial fill! "
            f"buy_ok={buy_ok}, sell_ok={sell_ok}"
        )
        logger.critical(msg)
        await self._notifier.send_error(RuntimeError(msg), "Arbitrage partial fill")

        if buy_ok and not sell_ok and isinstance(buy_result, OrderResult):
            logger.warning(f"Reversing buy leg on {order.buy_exchange}")
            try:
                adapter = self._adapters[order.buy_exchange]
                reverse = await adapter.place_market_order("sell", buy_result.amount_btc)
                await self._tracker.register(reverse, strategy="arb_reversal")
                logger.info(f"Buy leg reversed: sold {reverse.amount_btc:.6f} BTC on {order.buy_exchange}")
            except Exception as e:
                logger.critical(f"Failed to reverse buy leg: {e}")

        elif sell_ok and not buy_ok and isinstance(sell_result, OrderResult):
            logger.warning(f"Reversing sell leg on {order.sell_exchange}")
            try:
                adapter = self._adapters[order.sell_exchange]
                reverse = await adapter.place_market_order("buy", sell_result.amount_btc)
                await self._tracker.register(reverse, strategy="arb_reversal")
                logger.info(f"Sell leg reversed: bought {reverse.amount_btc:.6f} BTC on {order.sell_exchange}")
            except Exception as e:
                logger.critical(f"Failed to reverse sell leg: {e}")

    async def _place_maker_order(
        self,
        adapter,
        side: str,
        amount: float,
        pair: str,
        timeout_seconds: int,
    ) -> "OrderResult":
        """指値でMaker注文を出し、timeout後に未約定ならキャンセルして成行にフォールバック。"""
        ticker = await adapter.fetch_ticker(pair)
        # 板に乗る価格: 買いはbid、売りはask
        limit_price = ticker.bid if side == "buy" else ticker.ask
        if limit_price <= 0:
            logger.warning(f"Invalid limit price {limit_price}, falling back to market")
            return await adapter.place_market_order(side, amount, pair=pair)

        result = await adapter.place_limit_order(side, amount, limit_price, pair=pair)
        order_id = result.order_id
        logger.info(f"Maker limit order placed: {side} {amount} {pair} @ {limit_price:,.2f} (id={order_id})")

        # 約定待ち
        elapsed = 0
        poll_interval = 3
        while elapsed < timeout_seconds:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            try:
                status = await adapter.fetch_order_status(order_id, pair)
                if status.status == "filled":
                    logger.info(f"Maker order filled: {order_id}")
                    return status
                if status.status == "cancelled":
                    break
            except Exception as e:
                logger.warning(f"Failed to poll maker order {order_id}: {e}")

        # タイムアウト → キャンセルして成行
        logger.warning(f"Maker order {order_id} not filled after {timeout_seconds}s, cancelling → market")
        await adapter.cancel_order(order_id, pair)
        return await adapter.place_market_order(side, amount, pair=pair)

    async def _simulate(self, order: ApprovedOrder) -> None:
        """Paper-trade: update portfolio with current market price, no real order."""
        from bot.data.market_data import OrderResult
        import time

        price = order.amount_jpy / order.amount_btc if order.amount_btc > 0 else 0.0
        fee_jpy = order.amount_jpy * (order.signal.metadata.get("fee_pct", 0.0) / 100)

        result = OrderResult(
            exchange=order.buy_exchange or order.sell_exchange,
            order_id=f"DRY_{int(time.time() * 1000)}",
            side=order.side,
            pair=order.pair,
            amount_btc=order.amount_btc,
            price=price,
            fee_jpy=fee_jpy,
            status="filled",
            timestamp=time.time() * 1000,
        )
        await self._tracker.register(result, strategy=order.signal.strategy)
        await self._portfolio.update_from_order(result)
        await self._notifier.send_trade_notification(result, order.signal)
        logger.info(
            f"[DRY RUN] Simulated {order.side} {order.amount_btc:.6f} BTC "
            f"@ ¥{price:,.0f} on {result.exchange}"
        )
