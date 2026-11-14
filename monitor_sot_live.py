#!/usr/bin/env python3
"""Monitor SOT SETUP with Angel One candles and send Telegram signal alerts.

The monitor sends setup and entry notifications only. It does not place orders.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import subprocess
import sys
import time as sleep_time
from datetime import date, datetime, time, timedelta
from pathlib import Path
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from sot_core import load_universe

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
SCAN_START = time(9, 30)
TRIGGER_END = time(12, 0)
MONITOR_END = time(12, 18)  # final poll has complete 12:12–12:15 entry minutes
CSV_FIELDS = ("Datetime", "Open", "High", "Low", "Close", "Volume")


def args_parser() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--universe-csv", type=Path, default=Path("LiveData/SOT_FO_Universe.csv")
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, default=Path("SOT_LiveReports"))
    parser.add_argument("--request-delay", type=float, default=0.75)
    parser.add_argument("--cycle-seconds", type=int, default=180)
    parser.add_argument("--history-days", type=int, default=7,
                        help="calendar days requested once to warm up the continuous 3-minute EMA50")
    parser.add_argument("--limit", type=int, help="limit symbols for a connection check")
    return parser.parse_args()


def as_ist(value: object) -> datetime:
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp.replace(tzinfo=IST) if stamp.tzinfo is None else stamp.astimezone(IST)


def completed_boundary(now: datetime) -> datetime:
    anchor = now.replace(hour=9, minute=15, second=0, microsecond=0)
    if now < anchor:
        return anchor
    minutes = int((now - anchor).total_seconds() // 60)
    return anchor + timedelta(minutes=(minutes // 3) * 3)


def rows_from_api(candles: list[list[object]], today: date, boundary: datetime,
                  history_start: date) -> dict[str, list[str]]:
    parsed: dict[str, list[str]] = {}
    for candle in candles:
        if not candle or len(candle) < 6:
            continue
        stamp = as_ist(candle[0])
        if stamp.date() < history_start or stamp.date() > today:
            continue
        if not MARKET_OPEN <= stamp.time().replace(tzinfo=None) < time(15, 30):
            continue
        # Keep only complete 3-minute intervals from the current session.
        if stamp.date() == today and stamp.replace(second=0, microsecond=0) >= boundary:
            continue
        key = stamp.strftime("%Y-%m-%d %H:%M:%S%z")
        parsed[key] = [key, *(str(value) for value in candle[1:6])]
    return parsed


def read_bars(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not set(CSV_FIELDS).issubset(reader.fieldnames or []):
            return {}
        rows: dict[str, list[str]] = {}
        for row in reader:
            stamp = as_ist(row["Datetime"])
            key = stamp.strftime("%Y-%m-%d %H:%M:%S%z")
            rows[key] = [key, *(row[name] for name in CSV_FIELDS[1:])]
        return rows


def write_bars(path: Path, rows: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_FIELDS)
        writer.writerows(rows[key] for key in sorted(rows))


def write_records(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        fieldnames = list(dict.fromkeys(key for row in rows for key in row))
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def telegram_send(token: str, chat_id: str, message: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": message, "disable_web_page_preview": True}).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        raise RuntimeError(type(error).__name__) from None
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected the message")


def read_records(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def setup_message(row: dict[str, str]) -> str:
    filt = "pre-09:45 filters bypassed" if row.get("early_filters_bypassed") == "True" else "09:45+ VWAP/range filters passed"
    if row.get("status") == "TRIGGER_HIGH_BROKE_BEFORE_ENTRY":
        entry_state = "Entry setup invalidated when trigger high broke before entry."
    elif row.get("entry_time"):
        entry_state = f"Entry break already occurred at {row['entry_time']} ({row['entry_price']})."
    else:
        entry_state = "Waiting for the entry-level break within the next three 3-minute candles."
    return (
        "SOT SETUP VALID\n"
        f"Symbol: {row['symbol']}\n"
        f"Trigger candle: {row['trigger_time']}\n"
        f"Active candle: {row['active_time']}\n"
        f"Trigger label: {row.get('trigger_label') or 'none'}\n"
        f"Trigger high / initial stop: {row['trigger_high']}\n"
        f"Combined-low entry level: {row['combined_low']}\n"
        f"Active-close distance to 09:18 high: {row.get('active_range_to_0918_pct', '')}%\n"
        f"Filter: {filt}\n"
        f"Status: {entry_state}\n"
        "Signal only — no order was placed."
    )


def entry_message(row: dict[str, str]) -> str:
    return (
        "SOT ENTRY TRIGGERED\n"
        f"Symbol: {row['symbol']}\n"
        f"Entry time: {row['entry_time']}\n"
        f"Entry price: {row['entry_price']}\n"
        f"Initial stop: {row['initial_stop']}\n"
        f"Target at entry: {row['target_price']}\n"
        f"Target rule: EMA50 updates after each completed 3-minute candle, capped at the fixed 0.50% level ({row['target_050_price']})\n"
        "Signal only — no order was placed."
    )


def send_once(kind: str, row: dict[str, str], token: str, chat_id: str,
              sent: set[str], event_rows: list[dict[str, str]], failures: list[dict[str, str]]) -> None:
    key = f"{kind}|{row['date']}|{row['symbol']}|{row['trigger_time']}"
    if key in sent:
        return
    message = setup_message(row) if kind == "SETUP" else entry_message(row)
    try:
        telegram_send(token, chat_id, message)
    except RuntimeError as error:
        failures.append({"event": key, "error": str(error)})
        print(f"Telegram send failed for {row['symbol']} {kind}: {error}", flush=True)
        return
    sent.add(key)
    event_rows.append({"event_type": kind, "symbol": row["symbol"], "date": row["date"],
                       "trigger_time": row["trigger_time"], "entry_time": row.get("entry_time", ""),
                       "entry_price": row.get("entry_price", ""), "sent_at_ist": datetime.now(IST).isoformat()})
    print(f"Telegram {kind} alert sent: {row['symbol']} {row['trigger_time']}", flush=True)


def main() -> None:
    args = args_parser()
    repo = Path(__file__).resolve().parent
    os.chdir(repo)
    try:
        from dotenv import load_dotenv
        import pyotp
        import logzero
        from SmartApi import SmartConnect
    except ImportError as error:
        raise SystemExit(f"Angel One dependencies unavailable: {error.name}") from None
    if args.env_file.is_file():
        load_dotenv(args.env_file, override=False)
    required = ("API_KEY", "CLIENT_CODE", "PIN", "TOTP_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise SystemExit("Missing required environment values: " + ", ".join(missing))

    logzero.loglevel(logging.CRITICAL)
    api = SmartConnect(api_key=os.environ["API_KEY"])
    login = api.generateSession(os.environ["CLIENT_CODE"], os.environ["PIN"],
                                pyotp.TOTP(os.environ["TOTP_SECRET"]).now())
    if not login or not login.get("status"):
        raise SystemExit("Angel One login failed; response details suppressed.")

    symbols = load_universe(args.universe_csv)
    if args.limit:
        symbols = symbols[:args.limit]
    instrument_rows: dict[str, tuple[str, str]] = {}
    with args.universe_csv.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            instrument_rows[row["Symbol"].strip().upper()] = (row["Equity Token"].strip(), row["Exchange"].strip())
    symbols = [symbol for symbol in symbols if symbol in instrument_rows]

    started = datetime.now(IST)
    today = started.date()
    report_only = started.time() > MONITOR_END
    history_start = today - timedelta(days=max(args.history_days, 3))
    boundary = completed_boundary(started)
    run_dir = args.output / today.isoformat()
    data_dir = Path(os.environ["SOT_WORK_DIR"]) if os.environ.get("SOT_WORK_DIR") else Path(tempfile.gettempdir()) / "sot_live_work" / today.isoformat()
    input_dir = data_dir / "data"
    engine_dir = data_dir / "engine"
    input_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    request_end = started.strftime("%Y-%m-%d %H:%M")
    fetch_failures: dict[str, str] = {}
    for index, symbol in enumerate(symbols, 1):
        token, exchange = instrument_rows[symbol]
        path = input_dir / f"{symbol}_1m.csv"
        try:
            response = api.getCandleData({
                "exchange": exchange, "symboltoken": token, "interval": "ONE_MINUTE",
                "fromdate": f"{history_start.isoformat()} 09:15", "todate": request_end,
            })
            if not response or not response.get("status"):
                raise RuntimeError("Angel One returned an unsuccessful candle response")
            rows = rows_from_api(response.get("data") or [], today, boundary, history_start)
            write_bars(path, rows)
        except Exception as error:
            fetch_failures[symbol] = type(error).__name__
        if index < len(symbols):
            sleep_time.sleep(max(0.0, args.request_delay))

    sent: set[str] = set()
    alerts_path = run_dir / "alerts.csv"
    event_rows: list[dict[str, str]] = read_records(alerts_path)
    sent.update(f"{row['event_type']}|{row['date']}|{row['symbol']}|{row['trigger_time']}" for row in event_rows)
    telegram_failures: list[dict[str, str]] = []
    failure_path = run_dir / "failures.csv"
    write_records(failure_path, [{"symbol": symbol, "error": error} for symbol, error in fetch_failures.items()])
    print(f"SOT live monitor initialized: {len(symbols)} symbols; history warm-up starts {history_start}; "
          f"initial data failures: {len(fetch_failures)}.", flush=True)

    scan_start_dt = datetime.combine(today, SCAN_START, IST)
    if not report_only and datetime.now(IST) < scan_start_dt:
        wait_seconds = (scan_start_dt - datetime.now(IST)).total_seconds()
        print(f"Waiting until {scan_start_dt:%H:%M} IST to scan triggers.", flush=True)
        sleep_time.sleep(max(0, wait_seconds))

    try:
        while report_only or datetime.now(IST).time() <= MONITOR_END:
            cycle_started = datetime.now(IST)
            if cycle_started.time() < SCAN_START:
                sleep_time.sleep(max(1, (datetime.combine(today, SCAN_START, IST) - cycle_started).total_seconds()))
                continue
            boundary = completed_boundary(cycle_started)
            cycle_failures: list[dict[str, str]] = []
            for index, symbol in enumerate(symbols, 1):
                token, exchange = instrument_rows[symbol]
                path = input_dir / f"{symbol}_1m.csv"
                try:
                    response = api.getCandleData({
                        "exchange": exchange, "symboltoken": token, "interval": "ONE_MINUTE",
                        "fromdate": f"{today.isoformat()} 09:15", "todate": cycle_started.strftime("%Y-%m-%d %H:%M"),
                    })
                    if not response or not response.get("status"):
                        raise RuntimeError("Angel One returned an unsuccessful candle response")
                    current_rows = rows_from_api(response.get("data") or [], today, boundary, today)
                    previous = read_bars(path)
                    for key, row in current_rows.items():
                        previous[key] = row
                    write_bars(path, previous)
                    fetch_failures.pop(symbol, None)
                except Exception as error:
                    error_name = type(error).__name__
                    cycle_failures.append({"symbol": symbol, "error": error_name})
                    fetch_failures[symbol] = error_name
                if index < len(symbols):
                    sleep_time.sleep(max(0.0, args.request_delay))

            write_records(failure_path, [{"symbol": symbol, "error": error} for symbol, error in fetch_failures.items()]
                          + telegram_failures)
            engine_dir.mkdir(parents=True, exist_ok=True)
            command = [sys.executable, str(repo / "sot_setup_backtest.py"), "--data", str(input_dir),
                       "--universe-csv", str(args.universe_csv), "--output", str(engine_dir),
                       "--start", today.isoformat(), "--end", today.isoformat(), "--quiet"]
            result = subprocess.run(command, cwd=repo, capture_output=True, text=True)
            if result.returncode != 0:
                print("SOT evaluation failed this cycle; details follow.", flush=True)
                print(result.stderr[-3000:], flush=True)
            else:
                trades = read_records(engine_dir / "Trades.csv")
                audits = read_records(engine_dir / "SetupAudit.csv")
                snapshot_trades: list[dict[str, str]] = []
                pending_entries = 0
                for row in trades:
                    snapshot_row = dict(row)
                    if row.get("exit_reason") == "EOD":
                        snapshot_row["observed_outcome"] = "NO_STOP_OR_TARGET_OBSERVED_BY_LATEST_COMPLETE_BAR"
                        snapshot_row["exit_reason"] = ""
                        snapshot_row["status"] = "ENTRY_TRIGGERED_NO_EXIT_OBSERVED"
                    elif row.get("exit_reason") == "STOP":
                        snapshot_row["observed_outcome"] = "STOP_HIT"
                    elif row.get("exit_reason") == "TARGET_MIN_0.50_OR_EMA50":
                        snapshot_row["observed_outcome"] = "TARGET_HIT"
                    else:
                        snapshot_row["observed_outcome"] = ""
                    if row.get("status") == "NO_ENTRY_WITHIN_THREE_CANDLES":
                        try:
                            deadline = datetime.fromisoformat(row["active_time"]) + timedelta(minutes=12)
                            if cycle_started < deadline:
                                snapshot_row["status"] = "ENTRY_WINDOW_STILL_OPEN"
                                pending_entries += 1
                        except (KeyError, ValueError):
                            pass
                    snapshot_trades.append(snapshot_row)
                write_records(run_dir / "setups_latest.csv", snapshot_trades)
                write_records(run_dir / "setup_audit_latest.csv", audits)
                coverage = read_records(engine_dir / "UniverseCoverage.csv")
                write_records(run_dir / "coverage_latest.csv", coverage)
                entries = [row for row in snapshot_trades if row.get("entry_time")]
                write_records(run_dir / "entries_latest.csv", entries)
                if not report_only:
                    for row in trades:
                        send_once("SETUP", row, os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"],
                                  sent, event_rows, telegram_failures)
                        if row.get("entry_time") and row.get("target_price"):
                            send_once("ENTRY", row, os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"],
                                      sent, event_rows, telegram_failures)
                write_records(alerts_path, event_rows)
                write_records(failure_path, [{"symbol": symbol, "error": error} for symbol, error in fetch_failures.items()]
                              + telegram_failures)
                summary = {
                    "snapshot_time_ist": cycle_started.isoformat(),
                    "complete_3m_candle_boundary_ist": boundary.isoformat(),
                    "universe_symbols": len(symbols),
                    "symbols_with_data": sum(item.get("data_available") == "True" for item in coverage),
                    "symbols_excluded_price_cap": sum(item.get("price_cap_excluded") == "True" for item in coverage),
                    "trigger_rule_passes": sum(int(item.get("trigger_rule_passes", "0") or 0) for item in coverage),
                    "valid_active_setups": len(trades),
                    "entry_triggers": len(entries),
                    "target_hits_observed": sum(row.get("observed_outcome") == "TARGET_HIT" for row in entries),
                    "stop_hits_observed": sum(row.get("observed_outcome") == "STOP_HIT" for row in entries),
                    "entries_without_exit_observed": sum(row.get("observed_outcome") == "NO_STOP_OR_TARGET_OBSERVED_BY_LATEST_COMPLETE_BAR" for row in entries),
                    "setups_waiting_for_entry": pending_entries,
                    "symbols_with_current_cycle_api_errors": len(cycle_failures),
                }
                (run_dir / "summary_latest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
                print(f"Cycle {cycle_started:%H:%M:%S} IST: triggers={len(trades)}, entries={len(entries)}, "
                      f"API errors={len(cycle_failures)}.", flush=True)

            elapsed = (datetime.now(IST) - cycle_started).total_seconds()
            if report_only:
                break
            sleep_time.sleep(max(1, args.cycle_seconds - elapsed))
    finally:
        try:
            api.terminateSession(os.environ["CLIENT_CODE"])
        except Exception:
            pass
        write_records(alerts_path, event_rows)
        write_records(failure_path, [{"symbol": symbol, "error": error} for symbol, error in fetch_failures.items()]
                      + telegram_failures)
    try:
        summary = json.loads((run_dir / "summary_latest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        summary = {}
    report_message = (
        f"SOT SETUP daily report — {today.isoformat()}\n"
        f"Snapshot: {summary.get('snapshot_time_ist', 'not available')}\n"
        f"Universe: {summary.get('universe_symbols', len(symbols))} stocks; "
        f"price-cap exclusions: {summary.get('symbols_excluded_price_cap', 'not available')}\n"
        f"Valid setups: {summary.get('valid_active_setups', 'not available')}\n"
        f"Entry triggers: {summary.get('entry_triggers', 'not available')}\n"
        f"Targets observed: {summary.get('target_hits_observed', 'not available')}; "
        f"stops observed: {summary.get('stop_hits_observed', 'not available')}\n"
        f"Waiting for entry: {summary.get('setups_waiting_for_entry', 'not available')}\n"
        "Signal-only run; no orders were placed."
    )
    from scanner930.notifications import NotificationService

    notifier = NotificationService()
    notifier.send_report(
        f"SOT SETUP daily report {today.isoformat()}",
        report_message,
        [
            run_dir / "setups_latest.csv",
            run_dir / "entries_latest.csv",
            run_dir / "setup_audit_latest.csv",
            run_dir / "coverage_latest.csv",
            run_dir / "alerts.csv",
            run_dir / "failures.csv",
            run_dir / "summary_latest.json",
        ],
    )
    notifier.close()
    if notifier.last_error:
        print("Daily report delivery error:", notifier.last_error, flush=True)
    print(f"SOT monitor ended at {datetime.now(IST):%Y-%m-%d %H:%M:%S %Z}; reports: {run_dir.resolve()}")


if __name__ == "__main__":
    main()

