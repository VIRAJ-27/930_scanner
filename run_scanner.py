from __future__ import annotations

import argparse
import signal
import socket
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scanner930.angel import AngelMarketData, AngelSession
from scanner930.broker import EquityBroker
from scanner930.config import (
    DATABASE_PATH,
    INSTRUMENT_MASTER_PATH,
    LIVE_APPROVAL_FILE,
    LIVE_APPROVAL_PHRASE,
    REPORT_DIR,
    REPORT_REFRESH_SECONDS,
    SERVICE_STOP,
    SINGLE_INSTANCE_PORT,
    STRATEGY_NAME,
    TIMEZONE,
)
from scanner930.instruments import EquityCatalog, download_instrument_master
from scanner930.notifications import NotificationService
from scanner930.option_paper import (
    OptionCatalog,
    OptionPaperExecutor,
    SmartApiOptionQuoteProvider,
)
from scanner930.reports import export_reports, option_daily_message
from scanner930.runtime import LiveTradingSystem
from scanner930.storage import SQLiteStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="9:30 VWAP F&O-stock scanner")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--paper", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--option-paper", action="store_true")
    parser.add_argument("--confirm-live", default="")
    parser.add_argument(
        "--stop-at",
        type=parse_clock_time,
        default=SERVICE_STOP,
        metavar="HH:MM",
        help="Scheduled service stop time in Asia/Kolkata (default: 15:20).",
    )
    parser.add_argument(
        "--stop-reason",
        default="SCHEDULED_SERVICE_STOP",
        help="Exit label used only when --stop-at is reached.",
    )
    parser.add_argument(
        "--force-exit-at",
        type=parse_clock_time,
        default=None,
        metavar="HH:MM",
        help="Close open positions at this time but keep the service running.",
    )
    parser.add_argument(
        "--force-exit-reason",
        default="SCHEDULED_FORCE_EXIT",
        help="Exit label used when --force-exit-at is reached.",
    )
    return parser.parse_args()


def parse_clock_time(value: str):
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as error:
        raise argparse.ArgumentTypeError("time must use HH:MM format") from error


def validate_live_approval(args: argparse.Namespace) -> None:
    if not args.live:
        return
    file_value = (
        LIVE_APPROVAL_FILE.read_text(encoding="utf-8").strip()
        if LIVE_APPROVAL_FILE.exists()
        else ""
    )
    if (
        args.confirm_live != LIVE_APPROVAL_PHRASE
        or file_value != LIVE_APPROVAL_PHRASE
    ):
        raise PermissionError(
            "Live orders are locked. The command confirmation and untracked "
            "LIVE_APPROVAL.txt must both contain LIVE_EQUITY_ORDERS."
        )


def acquire_lock() -> socket.socket:
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        lock.listen(1)
    except OSError as error:
        lock.close()
        raise RuntimeError("Another 9:30 scanner process is already running.") from error
    return lock


def replay_today(
    database_path: Path,
    system: LiveTradingSystem,
    trading_day,
) -> int:
    if not database_path.exists():
        return 0
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    next_day = trading_day + timedelta(days=1)
    rows = connection.execute(
        """
        SELECT exchange_ts, received_ts, token, symbol, price,
               cumulative_volume, sequence_number
        FROM ticks
        WHERE exchange_ts >= ? AND exchange_ts < ?
        ORDER BY exchange_ts, id
        """,
        (trading_day.isoformat(), next_day.isoformat()),
    ).fetchall()
    connection.close()
    system.replaying = True
    try:
        for row in rows:
            system.on_tick(
                {
                    "exchange_ts": datetime.fromisoformat(row["exchange_ts"]),
                    "received_ts": datetime.fromisoformat(row["received_ts"]),
                    "token": row["token"],
                    "symbol": row["symbol"],
                    "price": row["price"],
                    "cumulative_volume": row["cumulative_volume"],
                    "sequence_number": row["sequence_number"],
                }
            )
    finally:
        system.replaying = False
    return len(rows)


def main() -> None:
    args = parse_args()
    validate_live_approval(args)
    instance_lock = acquire_lock()
    mode = "LIVE" if args.live else ("OPTION_PAPER" if args.option_paper else "PAPER")
    ist = ZoneInfo(TIMEZONE)

    master = download_instrument_master(INSTRUMENT_MASTER_PATH)
    catalog = EquityCatalog(master)
    store = SQLiteStore(DATABASE_PATH)
    session = AngelSession()
    print("Logging into Angel One; credentials and tokens are suppressed.")
    session.login()
    notifier = NotificationService()
    broker = EquityBroker(store, "LIVE" if args.live else "PAPER", session.smart_api)
    option_executor = None
    if args.option_paper:
        option_catalog = OptionCatalog(master)
        option_executor = OptionPaperExecutor(
            option_catalog,
            SmartApiOptionQuoteProvider(session.smart_api),
            store,
            notify=notifier.send_text,
        )
        if not option_catalog.by_underlying:
            raise RuntimeError("No current NFO stock call options were mapped.")
    system = LiveTradingSystem(catalog, store, broker, option_executor)
    replayed = replay_today(DATABASE_PATH, system, datetime.now(ist).date())
    if replayed:
        print(f"Recovered state from {replayed:,} stored ticks.")
        system.reconcile_option_positions(datetime.now(ist))
    market_data = AngelMarketData(session, catalog, system)

    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    worker = threading.Thread(target=market_data.connect, daemon=True)
    worker.start()
    notifier.send_text(
        f"Scanner930 started in {mode} mode with {STRATEGY_NAME}. Monitoring "
        f"{len(catalog.by_token)} F&O stocks; entries remain strategy-controlled."
    )

    last_report = 0.0
    health_alert_sent = False
    scheduled_stop_reached = False
    forced_exit_done = False
    try:
        while not stop_event.wait(1):
            now = datetime.now(ist)
            system.advance_clock(now)
            if (
                args.force_exit_at is not None
                and not forced_exit_done
                and now.time() >= args.force_exit_at
            ):
                positions_before_exit = system.open_position_count
                system.close_all(now, args.force_exit_reason)
                forced_exit_done = True
                store.flush()
                notifier.send_text(
                    f"Scanner930 {args.force_exit_reason}: requested closure of "
                    f"{positions_before_exit} open option position(s). Final "
                    f"reports will be delivered at {args.stop_at.strftime('%H:%M')} IST."
                )
            if now.time() >= args.stop_at:
                scheduled_stop_reached = True
                break
            if (
                not health_alert_sent
                and now.time() >= datetime.strptime("09:35", "%H:%M").time()
                and (
                    system.last_tick_ts is None
                    or (now - system.last_tick_ts).total_seconds() > 120
                )
            ):
                notifier.send_text(
                    "Scanner930 health warning: no recent underlying tick "
                    "after 09:35 IST. Check broker session/network."
                )
                health_alert_sent = True
            if time.monotonic() - last_report >= REPORT_REFRESH_SECONDS:
                store.save_status(
                    {
                        "trading_date": now.date().isoformat(),
                        "updated_ts": now.isoformat(),
                        "mode": mode,
                        "websocket_status": (
                            "CONNECTED"
                            if market_data.connected.is_set()
                            else "DISCONNECTED"
                        ),
                        "last_tick_ts": (
                            system.last_tick_ts.isoformat()
                            if system.last_tick_ts
                            else None
                        ),
                        "subscribed_stocks": len(catalog.by_token),
                        "open_positions": system.open_position_count,
                        "realized_pnl": system.realized_pnl,
                        "last_error": market_data.last_error,
                    }
                )
                store.flush()
                last_report = time.monotonic()
    finally:
        now = datetime.now(ist)
        exit_reason = args.stop_reason if scheduled_stop_reached else "SERVICE_SHUTDOWN"
        system.close_all(now, exit_reason)
        market_data.close()
        broker.close()
        store.flush()
        export_reports(DATABASE_PATH, REPORT_DIR)
        if args.option_paper:
            summary = option_daily_message(DATABASE_PATH, now.date())
            notifier.send_report(
                f"Scanner930 option paper report {now.date().isoformat()}",
                summary,
                [
                    REPORT_DIR / "Scanner930_Dashboard.xlsx",
                    REPORT_DIR / "OptionTradeBook.csv",
                    REPORT_DIR / "OptionDailySummary.csv",
                ],
            )
        notifier.close()
        notification_error = notifier.last_error
        store.close()
        instance_lock.close()
        print("9:30 scanner stopped; open paper/live positions were squared off.")
        if notification_error:
            raise RuntimeError(
                "One or more Telegram/email notifications failed; see the "
                "sanitized notification error above."
            )


if __name__ == "__main__":
    main()
