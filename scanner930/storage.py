from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DATABASE_FLUSH_SECONDS
from .config import (
    QUANTITY,
    RUNNER_QUANTITY,
    TP1_QUANTITY,
)


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS ticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_ts TEXT NOT NULL,
    received_ts TEXT NOT NULL,
    token TEXT NOT NULL,
    symbol TEXT NOT NULL,
    price REAL NOT NULL,
    cumulative_volume INTEGER,
    sequence_number INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ticks_time ON ticks(exchange_ts, id);

CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start_ts TEXT NOT NULL,
    completion_ts TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    vwap REAL,
    UNIQUE(symbol, timeframe, start_ts)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL,
    stock_price REAL,
    details_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS setups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trading_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    setup_number INTEGER NOT NULL,
    event_ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    outcome TEXT,
    details_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    trading_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    setup_number INTEGER NOT NULL,
    entry_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    initial_sl REAL NOT NULL,
    tp1_target REAL NOT NULL,
    tp1_touch_time TEXT,
    tp1_exit_time TEXT,
    tp1_exit_price REAL,
    tp1_quantity INTEGER NOT NULL,
    tp2_target REAL,
    tp2_touch_time TEXT,
    tp2_exit_time TEXT,
    tp2_exit_price REAL,
    tp2_quantity INTEGER NOT NULL DEFAULT 0,
    final_exit_time TEXT,
    final_exit_price REAL,
    final_exit_quantity INTEGER NOT NULL,
    final_exit_reason TEXT,
    realized_pnl REAL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_ts TEXT NOT NULL,
    updated_ts TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    trading_symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    reason TEXT NOT NULL,
    mode TEXT NOT NULL,
    signal_price REAL NOT NULL,
    broker_order_id TEXT,
    status TEXT NOT NULL,
    fill_price REAL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS daily_status (
    trading_date TEXT PRIMARY KEY,
    updated_ts TEXT NOT NULL,
    mode TEXT NOT NULL,
    websocket_status TEXT NOT NULL,
    last_tick_ts TEXT,
    subscribed_stocks INTEGER NOT NULL,
    open_positions INTEGER NOT NULL,
    realized_pnl REAL NOT NULL,
    last_error TEXT
);
"""


class SQLiteStore:
    """Single-writer SQLite store; WebSocket callbacks only enqueue writes."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.items: queue.Queue[Any] = queue.Queue()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._writer, daemon=True)
        self.thread.start()
        if not self.ready.wait(10):
            raise RuntimeError("Database writer did not initialize.")

    def execute(self, sql: str, values: tuple[Any, ...]) -> None:
        self.items.put(("SQL", sql, values))

    def flush(self) -> None:
        complete = threading.Event()
        self.items.put(("FLUSH", complete))
        if not complete.wait(15):
            raise RuntimeError("Database flush timed out.")

    def close(self) -> None:
        self.items.put(("CLOSE",))
        self.thread.join(15)

    def save_tick(self, tick: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT INTO ticks(
                exchange_ts, received_ts, token, symbol, price,
                cumulative_volume, sequence_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tick["exchange_ts"].isoformat(),
                tick["received_ts"].isoformat(),
                tick["token"],
                tick["symbol"],
                tick["price"],
                tick.get("cumulative_volume"),
                tick.get("sequence_number"),
            ),
        )

    def save_candle(self, candle) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO candles(
                symbol, timeframe, start_ts, completion_ts, open, high,
                low, close, volume, vwap
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candle.symbol,
                candle.timeframe,
                candle.start.isoformat(),
                candle.completion_time.isoformat(),
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
                candle.vwap,
            ),
        )

    def save_event(
        self,
        timestamp: datetime,
        symbol: str,
        event_type: str,
        price: float | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}
        encoded = json.dumps(details, default=str, sort_keys=True)
        self.execute(
            """
            INSERT INTO events(event_ts, symbol, event_type, stock_price, details_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (timestamp.isoformat(), symbol, event_type, price, encoded),
        )
        if event_type in {"G1_VALID", "SETUP_FAILED", "ENTRY_SIGNAL"}:
            self.execute(
                """
                INSERT INTO setups(
                    trading_date, symbol, setup_number, event_ts, event_type,
                    outcome, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp.date().isoformat(),
                    symbol,
                    int(details.get("setup_number") or 0),
                    timestamp.isoformat(),
                    event_type,
                    details.get("outcome"),
                    encoded,
                ),
            )

    def update_tp1_touch(self, position) -> None:
        self.execute(
            "UPDATE trades SET tp1_touch_time=? WHERE trade_id=?",
            (position.tp1_touch_time.isoformat(), position.trade_id),
        )

    def insert_trade(self, position) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO trades(
                trade_id, trading_date, symbol, setup_number, entry_time,
                entry_price, quantity, initial_sl, tp1_target, tp1_quantity,
                final_exit_quantity, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position.trade_id,
                position.entry_time.date().isoformat(),
                position.symbol,
                position.setup_number,
                position.entry_time.isoformat(),
                position.entry_price,
                QUANTITY,
                position.initial_sl,
                position.tp1_target or 0.0,
                TP1_QUANTITY,
                RUNNER_QUANTITY,
                "OPEN",
            ),
        )

    def update_tp1(self, position) -> None:
        self.execute(
            """
            UPDATE trades SET tp1_touch_time=?, tp1_exit_time=?,
                tp1_exit_price=?, status='RUNNER_OPEN'
            WHERE trade_id=?
            """,
            (
                position.tp1_touch_time.isoformat(),
                position.tp1_exit_time.isoformat(),
                position.tp1_exit_price,
                position.trade_id,
            ),
        )

    def update_trade_target(self, position) -> None:
        self.execute(
            "UPDATE trades SET tp1_target=? WHERE trade_id=?",
            (position.tp1_target, position.trade_id),
        )

    def close_trade(self, position) -> None:
        tp1_pnl = 0.0
        if position.tp1_exit_price is not None:
            tp1_pnl = (
                position.tp1_exit_price - position.entry_price
            ) * TP1_QUANTITY
        final_quantity = RUNNER_QUANTITY if position.tp1_booked else QUANTITY
        final_pnl = (
            (position.final_exit_price - position.entry_price) * final_quantity
            if position.final_exit_price is not None
            else 0.0
        )
        self.execute(
            """
            UPDATE trades SET final_exit_time=?, final_exit_price=?,
                final_exit_quantity=?, final_exit_reason=?, realized_pnl=?,
                status='CLOSED'
            WHERE trade_id=?
            """,
            (
                position.final_exit_time.isoformat(),
                position.final_exit_price,
                final_quantity,
                position.final_exit_reason,
                round(tp1_pnl + final_pnl, 2),
                position.trade_id,
            ),
        )

    def save_order(self, order: dict[str, Any]) -> None:
        now = order.get("updated_ts") or order["requested_ts"]
        self.execute(
            """
            INSERT INTO orders(
                requested_ts, updated_ts, trade_id, symbol, trading_symbol,
                side, quantity, reason, mode, signal_price, broker_order_id,
                status, fill_price, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order["requested_ts"].isoformat(),
                now.isoformat(),
                order["trade_id"],
                order["symbol"],
                order["trading_symbol"],
                order["side"],
                order["quantity"],
                order["reason"],
                order["mode"],
                order["signal_price"],
                order.get("broker_order_id"),
                order["status"],
                order.get("fill_price"),
                order.get("error"),
            ),
        )

    def save_status(self, status: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO daily_status(
                trading_date, updated_ts, mode, websocket_status, last_tick_ts,
                subscribed_stocks, open_positions, realized_pnl, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                status["trading_date"],
                status["updated_ts"],
                status["mode"],
                status["websocket_status"],
                status.get("last_tick_ts"),
                status["subscribed_stocks"],
                status["open_positions"],
                status["realized_pnl"],
                status.get("last_error"),
            ),
        )

    def _writer(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.executescript(SCHEMA)
        existing_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(trades)")
        }
        migrations = {
            "tp2_target": "ALTER TABLE trades ADD COLUMN tp2_target REAL",
            "tp2_touch_time": "ALTER TABLE trades ADD COLUMN tp2_touch_time TEXT",
            "tp2_exit_time": "ALTER TABLE trades ADD COLUMN tp2_exit_time TEXT",
            "tp2_exit_price": "ALTER TABLE trades ADD COLUMN tp2_exit_price REAL",
            "tp2_quantity": (
                "ALTER TABLE trades ADD COLUMN "
                "tp2_quantity INTEGER NOT NULL DEFAULT 0"
            ),
        }
        for column, statement in migrations.items():
            if column not in existing_columns:
                connection.execute(statement)
        connection.commit()
        self.ready.set()
        last_commit = time.monotonic()
        while True:
            timeout = max(
                0.05,
                DATABASE_FLUSH_SECONDS - (time.monotonic() - last_commit),
            )
            try:
                item = self.items.get(timeout=timeout)
            except queue.Empty:
                item = None
            if item:
                if item[0] == "CLOSE":
                    connection.commit()
                    connection.close()
                    return
                if item[0] == "FLUSH":
                    connection.commit()
                    last_commit = time.monotonic()
                    item[1].set()
                    continue
                _, sql, values = item
                connection.execute(sql, values)
            if time.monotonic() - last_commit >= DATABASE_FLUSH_SECONDS:
                connection.commit()
                last_commit = time.monotonic()
