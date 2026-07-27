from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from scanner930.models import Candle
from scanner930.strategy import ScannerStrategy
from scanner930.config import (
    QUANTITY,
    RUNNER_QUANTITY,
    TP1_QUANTITY,
)


TICK_SIZE = 0.05
MARKET_EXIT = time(15, 15)
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest the 9:30 scanner.")
    parser.add_argument("--base-data", type=Path, required=True)
    parser.add_argument("--supplement", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", default="2026-04-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--end-index", type=int)
    return parser.parse_args()


def load_symbol(
    symbol: str,
    base_data: Path,
    supplement: Path | None,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    safe = safe_name(symbol)
    paths = [base_data / f"{safe}_1m.csv"]
    if supplement:
        paths.append(supplement / f"{safe}_1m.csv")
    frames = []
    for path in paths:
        if not path.exists():
            continue
        frame = pd.read_csv(
            path,
            usecols=["Datetime", "Open", "High", "Low", "Close", "Volume"],
        )
        if frame.empty:
            continue
        frame["Datetime"] = pd.to_datetime(frame["Datetime"], utc=True)
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True)
    result = (
        result.sort_values("Datetime")
        .drop_duplicates("Datetime", keep="last")
        .reset_index(drop=True)
    )
    result["Datetime"] = result["Datetime"].dt.tz_convert("Asia/Kolkata")
    mask = (result["Datetime"] >= start) & (result["Datetime"] <= end)
    result = result.loc[mask].copy()
    for column in ["Open", "High", "Low", "Close", "Volume"]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.dropna(subset=["Open", "High", "Low", "Close"])


def make_three_minute(frame: pd.DataFrame) -> pd.DataFrame:
    working = frame.copy()
    working["Bucket"] = working["Datetime"].dt.floor("3min")
    result = (
        working.groupby("Bucket", sort=True)
        .agg(
            Open=("Open", "first"),
            High=("High", "max"),
            Low=("Low", "min"),
            Close=("Close", "last"),
            Volume=("Volume", "sum"),
            MinuteCount=("Close", "count"),
        )
        .dropna(subset=["Open", "High", "Low", "Close"])
        .reset_index()
        .rename(columns={"Bucket": "Datetime"})
    )
    typical = (result["High"] + result["Low"] + result["Close"]) / 3.0
    trading_dates = result["Datetime"].dt.date
    cumulative_volume = result["Volume"].groupby(trading_dates).cumsum()
    cumulative_pv = (typical * result["Volume"]).groupby(trading_dates).cumsum()
    result["VWAP"] = cumulative_pv / cumulative_volume
    result.loc[cumulative_volume <= 0, "VWAP"] = float("nan")
    result["Completion"] = result["Datetime"] + pd.Timedelta(minutes=3)
    return result


def row_to_candle(symbol: str, row: Any) -> Candle:
    return Candle(
        symbol=symbol,
        minutes=3,
        start=row.Datetime.to_pydatetime(),
        open=float(row.Open),
        high=float(row.High),
        low=float(row.Low),
        close=float(row.Close),
        volume=int(row.Volume),
        vwap=None if pd.isna(row.VWAP) else float(row.VWAP),
    )


def backtest_day(
    symbol: str,
    trading_day: date,
    minute_frame: pd.DataFrame,
    three_frame: pd.DataFrame | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []

    def emit(ts, event_type, price, details=None):
        details = details or {}
        events.append(
            {
                "Date": trading_day.isoformat(),
                "Symbol": symbol,
                "EventTime": iso(ts),
                "EventType": event_type,
                "Price": price,
                "SetupNumber": details.get("setup_number"),
                "Outcome": details.get("outcome"),
                "Details": json.dumps(details, default=str, sort_keys=True),
            }
        )

    strategy = ScannerStrategy(symbol, emit)
    three = (
        three_frame
        if three_frame is not None
        else make_three_minute(minute_frame)
    )
    three_by_end = {
        row.Completion.to_pydatetime(): row
        for row in three.itertuples(index=False)
    }
    processed: set[datetime] = set()
    trades: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None

    def process_completion(moment: datetime) -> Any | None:
        if moment in processed:
            return None
        row = three_by_end.get(moment)
        if row is None:
            return None
        strategy.on_three_minute(row_to_candle(symbol, row))
        processed.add(moment)
        return row

    for minute in minute_frame.itertuples(index=False):
        timestamp = minute.Datetime.to_pydatetime()
        minute_end = timestamp + timedelta(minutes=1)
        completed_row = process_completion(timestamp)

        position = strategy.position
        if (
            completed_row is not None
            and position is not None
            and active is not None
            and position.entry_mode == "TRIGGER_HIGH_BREAK"
            and position.c1_low is not None
            and not active.get("C1Time")
        ):
            c1 = row_to_candle(symbol, completed_row)
            active.update(candle_fields("C1", c1))
            active["PostC1SL"] = position.current_sl
            active["RiskPerShare"] = position.entry_price - position.c1_low
            active["TP1Target"] = position.tp1_target
            if position.tp1_due_at_c1_close and not position.tp1_booked:
                book_tp1_at_c1_close(
                    strategy,
                    active,
                    timestamp,
                    float(completed_row.Close),
                )

        position = strategy.position
        if (
            (position is None or position.open_quantity <= 0)
            and (
                strategy.done
                or (
                    strategy.state == "SEARCH_TRIGGER"
                    and timestamp.time() > time(10, 0)
                )
            )
        ):
            break
        if position is not None and position.open_quantity > 0:
            if timestamp.time() >= MARKET_EXIT:
                exit_price = float(minute.Open)
                active = close_trade(
                    active,
                    strategy,
                    timestamp,
                    exit_price,
                    "MARKET_EXIT_1515",
                    trades,
                )
                continue
            active = manage_position_minute(
                strategy,
                active,
                minute,
                timestamp,
                minute_end,
                three_by_end,
                processed,
                symbol,
            )
            if strategy.position and strategy.position.open_quantity <= 0:
                trades.append(finalize_record(active, strategy.position))
                active = None
            continue

        if strategy.state == "WAIT_C1" and strategy.setup is not None:
            trigger = strategy.setup.trigger
            low_break = float(minute.Low) < trigger.low
            high_break = float(minute.High) > trigger.high
            if low_break:
                strategy.on_entry_tick(timestamp, trigger.low - TICK_SIZE)
                continue
            if not high_break:
                continue
            entry_price = max(
                float(minute.Open),
                round(trigger.high + TICK_SIZE, 2),
            )
            position = strategy.on_entry_tick(timestamp, entry_price)
            if position is None:
                continue
            active = new_trade_record(
                trading_day,
                position,
                trigger,
                None,
                timestamp,
            )
            if float(minute.Low) <= position.current_sl:
                exit_price = (
                    float(minute.Open)
                    if float(minute.Open) <= position.current_sl
                    else position.current_sl
                )
                strategy.mark_closed(timestamp, exit_price, "INITIAL_SL")
                trades.append(finalize_record(active, position))
                active = None
            continue

        if strategy.state != "WAIT_C2" or strategy.setup is None:
            continue
        c1 = strategy.setup.c1
        if c1 is None:
            continue
        low_break = float(minute.Low) < c1.low
        high_break = float(minute.High) > c1.high
        if low_break:
            strategy.on_entry_tick(timestamp, c1.low - TICK_SIZE)
            continue
        if not high_break:
            continue
        entry_price = max(
            float(minute.Open),
            round(c1.high + TICK_SIZE, 2),
        )
        position = strategy.on_entry_tick(timestamp, entry_price)
        if position is None:
            continue
        trigger = strategy.setup.trigger
        active = new_trade_record(
            trading_day,
            position,
            trigger,
            c1,
            timestamp,
        )

        if float(minute.Low) <= position.current_sl:
            exit_price = (
                float(minute.Open)
                if float(minute.Open) <= position.current_sl
                else position.current_sl
            )
            strategy.mark_closed(timestamp, exit_price, "INITIAL_SL")
            trades.append(finalize_record(active, position))
            active = None
        elif (
            position.tp1_target is not None
            and float(minute.High) >= position.tp1_target
        ):
            book_tp1_minute(
                strategy,
                active,
                minute,
                timestamp,
                minute_end,
            )

    position = strategy.position
    if position is not None and position.open_quantity > 0 and active is not None:
        last = minute_frame.iloc[-1]
        exit_time = last["Datetime"].to_pydatetime() + timedelta(minutes=1)
        strategy.mark_closed(exit_time, float(last["Close"]), "DATA_END_EXIT")
        trades.append(finalize_record(active, position))

    return trades, events


def manage_position_minute(
    strategy: ScannerStrategy,
    active: dict[str, Any] | None,
    minute: Any,
    timestamp: datetime,
    minute_end: datetime,
    three_by_end: dict[datetime, Any],
    processed: set[datetime],
    symbol: str,
) -> dict[str, Any] | None:
    position = strategy.position
    if position is None or active is None:
        return active
    if float(minute.Low) <= position.current_sl:
        exit_price = (
            float(minute.Open)
            if float(minute.Open) <= position.current_sl
            else position.current_sl
        )
        reason = "RUNNER_TRAIL_SL" if position.tp1_booked else "INITIAL_SL"
        strategy.mark_closed(timestamp, exit_price, reason)
        return active
    if (
        position.tp1_target is not None
        and not position.tp1_touched
        and float(minute.High) >= position.tp1_target
    ):
        book_tp1_minute(
            strategy,
            active,
            minute,
            timestamp,
            minute_end,
        )
    return active


def book_tp1_minute(
    strategy: ScannerStrategy,
    active: dict[str, Any],
    minute: Any,
    timestamp: datetime,
    minute_end: datetime,
) -> None:
    position = strategy.position
    if (
        position is None
        or position.tp1_target is None
        or position.tp1_booked
    ):
        return
    position.tp1_touched = True
    position.tp1_touch_time = timestamp
    position.tp1_minute = timestamp.replace(second=0, microsecond=0)
    strategy.tp1_ever_hit = True
    strategy.mark_tp1_booked(minute_end, float(minute.Close))
    active["TP1TouchTime"] = iso(timestamp)
    active["TP1ExitTime"] = iso(minute_end)
    active["TP1ExitPrice"] = float(minute.Close)
    active["TP1Quantity"] = TP1_QUANTITY
    active["TP1ExecutionRule"] = "TARGET_TOUCH_1M_CLOSE"
    active["FinalQuantity"] = RUNNER_QUANTITY


def book_tp1_at_c1_close(
    strategy: ScannerStrategy,
    active: dict[str, Any],
    timestamp: datetime,
    price: float,
) -> None:
    position = strategy.position
    if position is None or position.tp1_booked:
        return
    strategy.mark_tp1_booked(timestamp, price)
    active["TP1TouchTime"] = iso(timestamp)
    active["TP1ExitTime"] = iso(timestamp)
    active["TP1ExitPrice"] = price
    active["TP1Quantity"] = TP1_QUANTITY
    active["TP1ExecutionRule"] = "C1_CLOSE_ALREADY_REACHED_1_5R"
    active["FinalQuantity"] = RUNNER_QUANTITY


def new_trade_record(
    trading_day: date,
    position,
    trigger: Candle,
    c1: Candle | None,
    entry_minute: datetime,
) -> dict[str, Any]:
    blank_c1 = {
        "C1Time": "",
        "C1Open": None,
        "C1High": None,
        "C1Low": None,
        "C1Close": None,
        "C1Volume": None,
        "C1VWAP": None,
    }
    return {
        "Date": trading_day.isoformat(),
        "Month": trading_day.strftime("%Y-%m"),
        "Symbol": position.symbol,
        "SetupNumber": position.setup_number,
        "EntryMode": position.entry_mode,
        **candle_fields("Trigger", trigger),
        **(candle_fields("C1", c1) if c1 is not None else blank_c1),
        "C2Minute": (
            iso(entry_minute)
            if position.entry_mode == "C1_HIGH_BREAK"
            else ""
        ),
        "EntryTime": iso(entry_minute),
        "EntryPrice": position.entry_price,
        "Quantity": QUANTITY,
        "InitialSL": position.initial_sl,
        "PostC1SL": c1.low if c1 is not None else None,
        "RiskPerShare": (
            position.entry_price - c1.low
            if c1 is not None
            else position.entry_price - position.initial_sl
        ),
        "TP1Target": position.tp1_target,
        "TP1TouchTime": "",
        "TP1ExitTime": "",
        "TP1ExitPrice": None,
        "TP1Quantity": 0,
        "TP1ExecutionRule": "",
        "FinalExitTime": "",
        "FinalExitPrice": None,
        "FinalQuantity": QUANTITY,
        "ExitReason": "",
        "GrossPnL": None,
        "RMultiple": None,
        "HoldingMinutes": None,
    }


def close_trade(
    active: dict[str, Any] | None,
    strategy: ScannerStrategy,
    timestamp: datetime,
    price: float,
    reason: str,
    trades: list[dict[str, Any]],
) -> None:
    position = strategy.position
    if position is None or active is None:
        return None
    strategy.mark_closed(timestamp, price, reason)
    trades.append(finalize_record(active, position))
    return None


def finalize_record(
    record: dict[str, Any],
    position,
) -> dict[str, Any]:
    result = dict(record)
    result["FinalExitTime"] = iso(position.final_exit_time)
    result["FinalExitPrice"] = position.final_exit_price
    result["FinalQuantity"] = (
        RUNNER_QUANTITY if position.tp1_booked else QUANTITY
    )
    result["ExitReason"] = position.final_exit_reason
    tp1_pnl = (
        (position.tp1_exit_price - position.entry_price) * TP1_QUANTITY
        if position.tp1_exit_price is not None
        else 0.0
    )
    final_pnl = (
        (position.final_exit_price - position.entry_price)
        * result["FinalQuantity"]
    )
    result["GrossPnL"] = round(tp1_pnl + final_pnl, 2)
    initial_risk = float(result["RiskPerShare"]) * QUANTITY
    result["RMultiple"] = (
        round(result["GrossPnL"] / initial_risk, 4)
        if initial_risk > 0
        else None
    )
    result["HoldingMinutes"] = round(
        (position.final_exit_time - position.entry_time).total_seconds() / 60,
        2,
    )
    return result


def candle_fields(prefix: str, candle: Candle) -> dict[str, Any]:
    return {
        f"{prefix}Time": iso(candle.start),
        f"{prefix}Open": candle.open,
        f"{prefix}High": candle.high,
        f"{prefix}Low": candle.low,
        f"{prefix}Close": candle.close,
        f"{prefix}Volume": candle.volume,
        f"{prefix}VWAP": candle.vwap,
    }


def summarize(trades: pd.DataFrame, key: str) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    for group_value, group in trades.groupby(key, dropna=False):
        pnl = group["GrossPnL"]
        rows.append(
            {
                key: group_value,
                "Trades": len(group),
                "Winners": int((pnl > 0).sum()),
                "Losers": int((pnl < 0).sum()),
                "Flat": int((pnl == 0).sum()),
                "WinRate": round(float((pnl > 0).mean()), 4),
                "GrossProfit": round(float(pnl[pnl > 0].sum()), 2),
                "GrossLoss": round(float(pnl[pnl < 0].sum()), 2),
                "NetPnL": round(float(pnl.sum()), 2),
                "AveragePnL": round(float(pnl.mean()), 2),
                "AverageR": round(float(group["RMultiple"].mean()), 4),
                "BestTrade": round(float(pnl.max()), 2),
                "WorstTrade": round(float(pnl.min()), 2),
                "TP1Trades": int(
                    group["TP1ExitTime"].fillna("").astype(bool).sum()
                ),
                "EarlyEntryTrades": int(
                    (group["EntryMode"] == "TRIGGER_HIGH_BREAK").sum()
                ),
                "FallbackEntryTrades": int(
                    (group["EntryMode"] == "C1_HIGH_BREAK").sum()
                ),
                "InitialSLTrades": int((group["ExitReason"] == "INITIAL_SL").sum()),
            }
        )
    return pd.DataFrame(rows).sort_values(key).reset_index(drop=True)


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(args.base_data / "FO_Universe.csv", dtype=str).fillna("")
    universe = universe.drop_duplicates("Symbol").sort_values("Symbol")
    universe = universe.iloc[args.start_index : args.end_index].copy()
    start = pd.Timestamp(args.start, tz="Asia/Kolkata")
    end = pd.Timestamp(args.end, tz="Asia/Kolkata") + pd.Timedelta(hours=23, minutes=59)
    all_trades: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []

    for sequence, (_, row) in enumerate(universe.iterrows(), start=1):
        symbol = str(row["Symbol"]).strip().upper()
        try:
            minute = load_symbol(symbol, args.base_data, args.supplement, start, end)
            if minute.empty:
                coverage.append({"Symbol": symbol, "Status": "NO_DATA"})
                continue
            symbol_trades = []
            symbol_events = []
            three_all = make_three_minute(minute)
            three_groups = {
                group_day: group.copy()
                for group_day, group in three_all.groupby(
                    three_all["Datetime"].dt.date
                )
            }
            for trading_day, day_frame in minute.groupby(minute["Datetime"].dt.date):
                day_frame = day_frame[
                    (day_frame["Datetime"].dt.time >= time(9, 15))
                    & (day_frame["Datetime"].dt.time <= time(15, 30))
                ].copy()
                trades, events = backtest_day(
                    symbol,
                    trading_day,
                    day_frame,
                    three_groups.get(trading_day),
                )
                symbol_trades.extend(trades)
                symbol_events.extend(events)
            all_trades.extend(symbol_trades)
            all_events.extend(symbol_events)
            coverage.append(
                {
                    "Symbol": symbol,
                    "Status": "OK",
                    "Rows": len(minute),
                    "TradingDays": minute["Datetime"].dt.date.nunique(),
                    "FirstCandle": iso(minute["Datetime"].min().to_pydatetime()),
                    "LastCandle": iso(minute["Datetime"].max().to_pydatetime()),
                    "Trades": len(symbol_trades),
                }
            )
            print(
                f"[{sequence}/{len(universe)}] {symbol}: "
                f"{len(symbol_trades)} trades",
                flush=True,
            )
        except Exception as error:
            coverage.append(
                {
                    "Symbol": symbol,
                    "Status": "ERROR",
                    "Error": f"{type(error).__name__}: {error}",
                }
            )

    trades_frame = pd.DataFrame(all_trades)
    events_frame = pd.DataFrame(all_events)
    coverage_frame = pd.DataFrame(coverage)
    if not trades_frame.empty:
        trades_frame = trades_frame.sort_values(["EntryTime", "Symbol"]).reset_index(drop=True)
    if not events_frame.empty:
        events_frame = events_frame.sort_values(["EventTime", "Symbol"]).reset_index(drop=True)

    monthly = summarize(trades_frame, "Month")
    daily = summarize(trades_frame, "Date")
    stock = summarize(trades_frame, "Symbol")
    trades_frame.to_csv(args.output / "Trades.csv", index=False)
    events_frame.to_csv(args.output / "SetupAudit.csv", index=False)
    monthly.to_csv(args.output / "MonthlySummary.csv", index=False)
    daily.to_csv(args.output / "DailySummary.csv", index=False)
    stock.to_csv(args.output / "StockSummary.csv", index=False)
    coverage_frame.to_csv(args.output / "Coverage.csv", index=False)

    actual_start = coverage_frame.loc[
        coverage_frame["Status"] == "OK", "FirstCandle"
    ].min()
    actual_end = coverage_frame.loc[
        coverage_frame["Status"] == "OK", "LastCandle"
    ].max()
    result_summary = {
        "RequestedStart": args.start,
        "RequestedEnd": args.end,
        "ActualDataStart": actual_start,
        "ActualDataEnd": actual_end,
        "UniverseStocks": int(len(universe)),
        "CoveredStocks": int((coverage_frame["Status"] == "OK").sum()),
        "Trades": int(len(trades_frame)),
        "Winners": int((trades_frame.get("GrossPnL", pd.Series(dtype=float)) > 0).sum()),
        "NetPnL": round(float(trades_frame.get("GrossPnL", pd.Series(dtype=float)).sum()), 2),
        "Notes": [
            "HLC3 session VWAP resets at 09:15 IST.",
            "Trigger starts are limited to 09:30 through 09:51 IST.",
            "The first red 3m candle is the only trigger candidate per stock/day.",
            "A failed first-red trigger or invalid C1 discards the stock for the day.",
            "Trigger close must be within the previous green 3m candle range.",
            "Only one counted valid-C1 setup is allowed per stock/day.",
            "Trigger-high break during C1 enters immediately with Trigger low as SL.",
            "Fallback entry uses max(C2 minute open, C1 high plus one tick).",
            "At C1 close SL moves up to C1 low and the 1.5R target is calculated.",
            "If C1 already reached the calculated 1.5R, 50 shares exit at C1 close.",
            "Without an early entry, valid C1 falls back to the normal C2 entry.",
            "100 shares: 50 exit at 1.5R and 50 trail by completed 3m lows.",
            "Active stop wins over entry/target when both occur in one 1m OHLC bar.",
            "No brokerage, taxes, fees or slippage are deducted.",
        ],
    }
    (args.output / "Summary.json").write_text(
        json.dumps(result_summary, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result_summary, indent=2), flush=True)


def iso(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def safe_name(symbol: str) -> str:
    result = symbol
    for character in '<>:"/\\|?*':
        result = result.replace(character, "_")
    return result


if __name__ == "__main__":
    main()
