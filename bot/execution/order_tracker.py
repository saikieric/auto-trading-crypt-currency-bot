from __future__ import annotations

import time
from pathlib import Path

import aiosqlite

from bot.data.market_data import OrderResult

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange    TEXT    NOT NULL,
    order_id    TEXT    NOT NULL,
    strategy    TEXT,
    side        TEXT    NOT NULL,
    pair        TEXT    NOT NULL,
    amount_btc  REAL    NOT NULL,
    price       REAL    NOT NULL,
    fee_jpy     REAL    NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL,
    timestamp   REAL    NOT NULL,
    created_at  REAL    NOT NULL
)
"""

_INSERT = """
INSERT INTO trades
    (exchange, order_id, strategy, side, pair, amount_btc, price, fee_jpy, status, timestamp, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class OrderTracker:
    def __init__(self, db_path: str = "storage/trades.db") -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            await db.commit()

    async def register(self, order: OrderResult, strategy: str = "") -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                _INSERT,
                (
                    order.exchange,
                    order.order_id,
                    strategy,
                    order.side,
                    order.pair,
                    order.amount_btc,
                    order.price,
                    order.fee_jpy,
                    order.status,
                    order.timestamp,
                    time.time() * 1000,
                ),
            )
            await db.commit()

    async def get_recent_trades(self, limit: int = 50) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
