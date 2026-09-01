from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from typing import Iterator

from .config import Settings


def _sqlite_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise ValueError("Only sqlite:/// DATABASE_URL values are supported in this local build")
    raw_path = database_url.replace("sqlite:///", "", 1)
    return Path(raw_path).resolve()


class CandleStore:
    def __init__(self, settings: Settings) -> None:
        self.path = _sqlite_path(settings.database_url)
        self.using_memory = False
        self._memory_conn: sqlite3.Connection | None = None
        self._memory_lock = threading.RLock()
        self._disk_write_lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @staticmethod
    def _is_disk_io_error(exc: sqlite3.OperationalError) -> bool:
        return "disk I/O" in str(exc)

    @staticmethod
    def _is_database_locked(exc: sqlite3.OperationalError) -> bool:
        return "database is locked" in str(exc).lower()

    def _switch_to_memory(self) -> None:
        self.using_memory = True
        self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._memory_conn.row_factory = sqlite3.Row
        self._create_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if self.using_memory:
            with self._memory_lock:
                if self._memory_conn is None:
                    self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
                    self._memory_conn.row_factory = sqlite3.Row
                    self._create_schema()
                yield self._memory_conn
                self._memory_conn.commit()
            return

        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            conn.execute("PRAGMA journal_mode=DELETE")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        try:
            self._create_schema()
        except sqlite3.OperationalError as exc:
            if not self._is_disk_io_error(exc):
                raise
            self._switch_to_memory()

    def _create_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS candles (
                    provider TEXT NOT NULL,
                    feed TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    trade_count INTEGER,
                    vwap REAL,
                    inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (provider, feed, symbol, timeframe, timestamp)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_candles_lookup
                ON candles (symbol, timeframe, feed, timestamp)
                """
            )

    def upsert_many(self, candles: list[dict]) -> None:
        if not candles:
            return
        try:
            with self._disk_write_lock:
                self._upsert_many(candles)
        except sqlite3.OperationalError as exc:
            if self._is_database_locked(exc):
                with self._disk_write_lock:
                    self._upsert_many(candles)
                return
            if not self._is_disk_io_error(exc):
                raise
            self._switch_to_memory()
            self._upsert_many(candles)

    def _upsert_many(self, candles: list[dict]) -> None:
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO candles (
                    provider, feed, symbol, timeframe, timestamp,
                    open, high, low, close, volume, trade_count, vwap
                )
                VALUES (
                    :provider, :feed, :symbol, :timeframe, :timestamp,
                    :open, :high, :low, :close, :volume, :trade_count, :vwap
                )
                ON CONFLICT(provider, feed, symbol, timeframe, timestamp)
                DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    trade_count = excluded.trade_count,
                    vwap = excluded.vwap,
                    inserted_at = CURRENT_TIMESTAMP
                """,
                candles,
            )

    def latest(
        self,
        *,
        symbol: str,
        timeframe: str,
        feed: str,
        limit: int,
        provider: str | None = None,
    ) -> list[dict]:
        # provider is part of the primary key, so two sources can hold the same symbol,
        # feed and timeframe as separate rows. Reads did not filter on it, which was
        # harmless while Alpaca was the only writer and becomes a silent interleave of two
        # sources the moment a second one exists. Optional, so existing callers keep their
        # behaviour and anything reading one specific source asks for it.
        clauses = ["symbol = ?", "timeframe = ?", "feed = ?"]
        params: list[Any] = [symbol, timeframe, feed]
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        params.append(limit)
        query = f"""
            SELECT provider, feed, symbol, timeframe, timestamp,
                   open, high, low, close, volume, trade_count, vwap
            FROM candles
            WHERE {" AND ".join(clauses)}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in reversed(rows)]

    def latest_until(
        self,
        *,
        symbol: str,
        timeframe: str,
        feed: str,
        limit: int,
        end: str,
        provider: str | None = None,
    ) -> list[dict]:
        # provider is part of the primary key, so two sources can hold the same symbol,
        # feed and timeframe as separate rows. Reads did not filter on it, which was
        # harmless while Alpaca was the only writer and becomes a silent interleave of two
        # sources the moment a second one exists. Optional, so existing callers keep their
        # behaviour and anything reading one specific source asks for it.
        clauses = ["symbol = ?", "timeframe = ?", "feed = ?", "timestamp <= ?"]
        params: list[Any] = [symbol, timeframe, feed, end]
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        params.append(limit)
        query = f"""
            SELECT provider, feed, symbol, timeframe, timestamp,
                   open, high, low, close, volume, trade_count, vwap
            FROM candles
            WHERE {" AND ".join(clauses)}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in reversed(rows)]

    def range(
        self,
        *,
        symbol: str,
        timeframe: str,
        feed: str,
        start: str | None = None,
        end: str | None = None,
        provider: str | None = None,
    ) -> list[dict]:
        # provider is part of the primary key, so two sources can hold the same symbol,
        # feed and timeframe as separate rows. Reads did not filter on it, which was
        # harmless while Alpaca was the only writer and becomes a silent interleave of two
        # sources the moment a second one exists. Optional, so existing callers keep their
        # behaviour and anything reading one specific source asks for it.
        clauses = ["symbol = ?", "timeframe = ?", "feed = ?"]
        params: list[str] = [symbol, timeframe, feed]
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        if start:
            clauses.append("timestamp >= ?")
            params.append(start)
        if end:
            clauses.append("timestamp <= ?")
            params.append(end)
        query = f"""
            SELECT provider, feed, symbol, timeframe, timestamp,
                   open, high, low, close, volume, trade_count, vwap
            FROM candles
            WHERE {" AND ".join(clauses)}
            ORDER BY timestamp ASC
        """
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def coverage(self, *, symbol: str, timeframe: str, feed: str) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count, MIN(timestamp) AS start, MAX(timestamp) AS end
                FROM candles
                WHERE symbol = ? AND timeframe = ? AND feed = ?
                """,
                (symbol, timeframe, feed),
            ).fetchone()
        return {
            "count": int(row["count"] or 0),
            "start": row["start"],
            "end": row["end"],
        }
