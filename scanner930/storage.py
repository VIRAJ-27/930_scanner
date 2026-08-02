from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
from datetime import date, datetime
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
    entry_tier TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS option_paper_trades (
    trade_id TEXT PRIMARY KEY,
    trading_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    entry_tier TEXT NOT NULL DEFAULT '',
    option_symbol TEXT,
    option_token TEXT,
    expiry TEXT,
    strike REAL,
    lot_size INTEGER,
    paper_lots INTEGER,
    paper_quantity REAL,
    entry_time TEXT NOT NULL,
    underlying_entry_price REAL NOT NULL,
    underlying_initial_sl REAL,
    underlying_tp1_target REAL,
    entry_quote_time TEXT,
    entry_option_ltp REAL,
    entry_option_bid REAL,
    entry_option_ask REAL,
    entry_spread_percent REAL,
    tp1_exit_time TEXT,
    tp1_underlying_price REAL,
    tp1_option_bid REAL,
    tp1_quantity REAL NOT NULL DEFAULT 0,
    remaining_quantity REAL NOT NULL DEFAULT 0,
    final_exit_time TEXT,
    final_underlying_price REAL,
    final_option_bid REAL,
    final_quantity REAL NOT NULL DEFAULT 0,
    final_exit_reason TEXT,
    realized_pnl REAL,
    option_return_percent REAL,
    status TEXT NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_option_paper_date
ON option_paper_trades(trading_date, entry_time);
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

    def load_completed_closes(
        self,
        symbol: str,
        timeframe: str,
        before_date: date,
        limit: int = 500,
    ) -> list[float]:
        """Return prior-session closes oldest-first for indicator warm-up."""
        self.flush()
        connection = sqlite3.connect(self.path)
        rows = connection.execute(
            """
            SELECT close FROM candles
            WHERE symbol=? AND timeframe=? AND start_ts < ?
            ORDER BY start_ts DESC LIMIT ?
            """,
            (symbol, timeframe, before_date.isoformat(), int(limit)),
        ).fetchall()
        connection.close()
        return [float(row[0]) for row in reversed(rows)]

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
        if event_type in {
            "G1_VALID",
            "B1_VALID",
            "SETUP_FAILED",
            "ENTRY_SIGNAL",
        }:
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
                entry_price, entry_tier, quantity, initial_sl, tp1_target, tp1_quantity,
                final_exit_quantity, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position.trade_id,
                position.entry_time.date().isoformat(),
                position.symbol,
                position.setup_number,
                position.entry_time.isoformat(),
                position.entry_price,
                position.entry_tier,
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

    def insert_option_trade(self, position, stock_position) -> None:
        quote = position.entry_quote
        instrument = position.instrument
        self.execute(
            """
            INSERT OR REPLACE INTO option_paper_trades(
                trade_id, trading_date, symbol, entry_tier, option_symbol,
                option_token, expiry, strike, lot_size, paper_lots,
                paper_quantity, entry_time, underlying_entry_price,
                underlying_initial_sl, underlying_tp1_target, entry_quote_time,
                entry_option_ltp, entry_option_bid, entry_option_ask,
                entry_spread_percent, tp1_quantity, remaining_quantity,
                status, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position.trade_id,
                position.entry_time.date().isoformat(),
                position.underlying,
                position.entry_tier,
                instrument.trading_symbol,
                instrument.token,
                instrument.expiry.isoformat(),
                instrument.strike,
                instrument.lot_size,
                position.paper_lots,
                position.paper_quantity,
                position.entry_time.isoformat(),
                position.underlying_entry,
                stock_position.initial_sl,
                stock_position.tp1_target,
                quote.quote_time.isoformat(),
                quote.ltp,
                quote.bid,
                quote.ask,
                quote.spread_fraction * 100,
                position.tp1_quantity,
                position.remaining_quantity,
                "OPEN",
                None,
            ),
        )

    def insert_option_rejection(
        self,
        timestamp: datetime,
        stock_position,
        details: dict[str, Any],
    ) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO option_paper_trades(
                trade_id, trading_date, symbol, entry_tier, option_symbol,
                entry_time, underlying_entry_price, underlying_initial_sl,
                underlying_tp1_target, status, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'REJECTED', ?)
            """,
            (
                stock_position.trade_id,
                timestamp.date().isoformat(),
                stock_position.symbol,
                stock_position.entry_tier,
                details.get("option_symbol") or None,
                timestamp.isoformat(),
                stock_position.entry_price,
                stock_position.initial_sl,
                stock_position.tp1_target,
                f"{details.get('outcome', '')}: {details.get('error', '')}".strip(),
            ),
        )

    def update_option_tp1(self, position, underlying_price: float, quote) -> None:
        self.execute(
            """
            UPDATE option_paper_trades SET tp1_exit_time=?,
                tp1_underlying_price=?, tp1_option_bid=?,
                remaining_quantity=?, status='RUNNER_OPEN'
            WHERE trade_id=?
            """,
            (
                position.tp1_exit_time.isoformat(),
                underlying_price,
                quote.bid,
                position.remaining_quantity,
                position.trade_id,
            ),
        )

    def close_option_trade(self, position, underlying_price: float, quote) -> None:
        tp1_pnl = (
            (position.tp1_option_price - position.option_entry_price)
            * position.tp1_quantity
            if position.tp1_option_price is not None
            else 0.0
        )
        final_quantity = position.remaining_quantity
        final_pnl = (
            (quote.bid - position.option_entry_price) * final_quantity
        )
        realized = round(tp1_pnl + final_pnl, 2)
        capital = position.option_entry_price * position.paper_quantity
        option_return = realized / capital * 100 if capital > 0 else None
        self.execute(
            """
            UPDATE option_paper_trades SET final_exit_time=?,
                final_underlying_price=?, final_option_bid=?, final_quantity=?,
                final_exit_reason=?, realized_pnl=?, option_return_percent=?,
                remaining_quantity=0, status='CLOSED'
            WHERE trade_id=?
            """,
            (
                position.final_exit_time.isoformat(),
                underlying_price,
                quote.bid,
                final_quantity,
                position.final_exit_reason,
                realized,
                option_return,
                position.trade_id,
            ),
        )

    def load_open_option_trades(self) -> list[sqlite3.Row]:
        self.flush()
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT * FROM option_paper_trades
            WHERE status IN ('OPEN', 'RUNNER_OPEN')
            ORDER BY entry_time
            """
        ).fetchall()
        connection.close()
        return rows

    def option_realized_pnl(self) -> float:
        self.flush()
        connection = sqlite3.connect(self.path)
        value = connection.execute(
            """
            SELECT COALESCE(SUM(realized_pnl), 0)
            FROM option_paper_trades WHERE status='CLOSED'
            """
        ).fetchone()[0]
        connection.close()
        return round(float(value or 0.0), 2)

    def _writer(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.executescript(SCHEMA)
        existing_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(trades)")
        }
        migrations = {
            "entry_tier": (
                "ALTER TABLE trades ADD COLUMN "
                "entry_tier TEXT NOT NULL DEFAULT ''"
            ),
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
