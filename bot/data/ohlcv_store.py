from __future__ import annotations

import time
from pathlib import Path

import aiosqlite

from bot.data.market_data import OHLCV

_CREATE = """
CREATE TABLE IF NOT EXISTS ohlcv_cache (
    exchange  TEXT  NOT NULL,
    pair      TEXT  NOT NULL,
    timeframe TEXT  NOT NULL,
    timestamp REAL  NOT NULL,
    open      REAL  NOT NULL,
    high      REAL  NOT NULL,
    low       REAL  NOT NULL,
    close     REAL  NOT NULL,
    volume    REAL  NOT NULL,
    PRIMARY KEY (exchange, pair, timeframe, timestamp)
)
"""

_UPSERT = """
INSERT OR REPLACE INTO ohlcv_cache
    (exchange, pair, timeframe, timestamp, open, high, low, close, volume)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT = """
SELECT timestamp, open, high, low, close, volume
FROM ohlcv_cache
WHERE exchange=? AND pair=? AND timeframe=?
ORDER BY timestamp DESC
LIMIT ?
"""

# 保持する最大本数（DB肥大化防止）
_MAX_ROWS = 500

_PRUNE = """
DELETE FROM ohlcv_cache
WHERE exchange=? AND pair=? AND timeframe=?
  AND timestamp NOT IN (
      SELECT timestamp FROM ohlcv_cache
      WHERE exchange=? AND pair=? AND timeframe=?
      ORDER BY timestamp DESC
      LIMIT ?
  )
"""


class OhlcvStore:
    def __init__(self, db_path: str = "storage/trades.db") -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE)
            await db.commit()

    async def save(
        self, exchange: str, pair: str, timeframe: str, candles: list[OHLCV]
    ) -> None:
        if not candles:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.executemany(
                _UPSERT,
                [
                    (exchange, pair, timeframe, c.timestamp,
                     c.open, c.high, c.low, c.close, c.volume)
                    for c in candles
                ],
            )
            await db.execute(_PRUNE, (exchange, pair, timeframe,
                                      exchange, pair, timeframe, _MAX_ROWS))
            await db.commit()

    async def load(
        self, exchange: str, pair: str, timeframe: str, limit: int
    ) -> list[OHLCV]:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(_SELECT, (exchange, pair, timeframe, limit))
            rows = await cursor.fetchall()

        rows = list(reversed(rows))  # DESC取得を時系列順に戻す
        return [
            OHLCV(
                timestamp=row[0],
                open=row[1],
                high=row[2],
                low=row[3],
                close=row[4],
                volume=row[5],
            )
            for row in rows
        ]
