from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from scanner930.config import QUANTITY, RUNNER_QUANTITY, TP1_QUANTITY
from scanner930.models import Candle
from scanner930.strategy import ScannerStrategy


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
    paths = [base_data / f"{safe_name(symbol)}_1m.csv"]
    if supplement:
        paths.append(supplement / f"{safe_name(symbol)}_1m.csv")
    frames = []
    for path in paths:
        if not path.exists():
            continue
        frame = pd.read_csv(
            path,
            usecols=["Datetime", "Open", "High", "Low", "Close", "Volume"],
        )
        if not frame.empty:
            frame["Datetime"] = pd.to_datetime(frame["Datetime"], utc=True)
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    result = (
        pd.concat(frames, ignore_index=True)
        .sort_values("Datetime")
        .drop_duplicates("Datetime", keep="last")
        .reset_index(drop=True)
    )
    result["Datetime"] = result["Datetime"].dt.tz_convert("Asia/Kolkata")
    result = result[
        (result["Datetime"] >= start) & (result["Datetime"] <= end)
    ].copy()
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
    dates = result["Datetime"].dt.date
    cumulative_volume = result["Volume"].groupby(dates).cumsum()
    cumulative_pv = (typical * result["Volume"]).groupby(dates).cumsum()
    result["VWAP"] = cumulative_pv / cumulative_volume
    result.loc[cumulative_volume <= 0, "VWAP"] = float("nan")
    result["Completion"] = result["Datetime"] + pd.Timedelta(minutes=3)
    return result


def row_to_candle(symbol: str, row: Any, minutes: int) -> Candle:
    return Candle(
        symbol=symbol,
        minutes=minutes,
        start=row.Datetime.to_pydatetime(),
        open=float(row.Open),
        high=float(row.High),
        low=float(row.Low),
        close=float(row.Close),
        volume=int(row.Volume),
        vwap=(
            None
            if not hasattr(row, "VWAP") or pd.isna(row.VWAP)
            else float(row.VWAP)
        ),
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
    three = three_frame if three_frame is not None else make_three_minute(minute_frame)
    three_by_end = {
        row.Completion.to_pydatetime(): row for row in three.itertuples(index=False)
    }
    processed: set[datetime] = set()
    trades: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None

    for minute in minute_frame.itertuples(index=False):
        timestamp = minute.Datetime.to_pydatetime()
        minute_end = timestamp + timedelta(minutes=1)

        completed = three_by_end.get(timestamp)
        if completed is not None and timestamp not in processed:
            strategy.on_three_minute(row_to_candle(symbol, completed, 3))
            processed.add(timestamp)

        position = strategy.position
        if position is not None and position.open_quantity > 0:
            if timestamp.time() >= MARKET_EXIT:
                strategy.mark_closed(
                    timestamp,
                    float(minute.Open),
                    "MARKET_EXIT_1515",
                )
                trades.append(finalize_record(active, position))
                active = None
                continue
            if float(minute.Low) <= position.current_sl:
                exit_price = (
                    float(minute.Open)
                    if float(minute.Open) <= position.current_sl
                    else position.current_sl
                )
                reason = "RUNNER_TRAIL_SL" if position.tp1_booked else "INITIAL_SL"
                strategy.mark_closed(timestamp, exit_price, reason)
                trades.append(finalize_record(active, position))
                active = None
                continue
            if (
                not position.tp1_booked
                and position.tp1_target is not None
                and float(minute.High) >= position.tp1_target
            ):
                book_tp1(strategy, active, timestamp, minute_end, float(minute.Close))
            strategy.on_one_minute(row_to_candle(symbol, minute, 1))
            continue

        if strategy.done:
            break

        entered = False
        if strategy.setup is not None:
            if (
                float(minute.Low) < strategy.setup.trigger.low
                and strategy.state in {"WAIT_G1", "WAIT_G2", "WAIT_G3"}
            ):
                strategy.on_entry_tick(
                    timestamp,
                    strategy.setup.trigger.low - TICK_SIZE,
                )
            elif strategy.state in {"WAIT_G2", "WAIT_G3"}:
                g1 = strategy.setup.g1
                if g1 is not None:
                    if float(minute.Low) < g1.low:
                        strategy.on_entry_tick(timestamp, g1.low - TICK_SIZE)
                    elif float(minute.High) > g1.high:
                        entry_price = max(
                            float(minute.Open),
                            round(g1.high + TICK_SIZE, 2),
                        )
                        position = strategy.on_entry_tick(timestamp, entry_price)
                        if position is not None:
                            active = new_trade_record(
                                trading_day,
                                position,
                                strategy.setup.trigger,
                                g1,
                                timestamp,
                            )
                            entered = True
                            if float(minute.Low) <= position.current_sl:
                                exit_price = (
                                    float(minute.Open)
                                    if float(minute.Open) <= position.current_sl
                                    else position.current_sl
                                )
                                strategy.mark_closed(
                                    timestamp,
                                    exit_price,
                                    "INITIAL_SL",
                                )
                                trades.append(finalize_record(active, position))
                                active = None
                            elif float(minute.High) >= position.tp1_target:
                                book_tp1(
                                    strategy,
                                    active,
                                    timestamp,
                                    minute_end,
                                    float(minute.Close),
                                )

        if not entered and not strategy.done:
            strategy.on_one_minute(row_to_candle(symbol, minute, 1))

    position = strategy.position
    if position is not None and position.open_quantity > 0 and active is not None:
        last = minute_frame.iloc[-1]
        exit_time = last["Datetime"].to_pydatetime() + timedelta(minutes=1)
        strategy.mark_closed(exit_time, float(last["Close"]), "DATA_END_EXIT")
        trades.append(finalize_record(active, position))

    return trades, events


def book_tp1(
    strategy: ScannerStrategy,
    active: dict[str, Any] | None,
    touch_time: datetime,
    exit_time: datetime,
    close_price: float,
) -> None:
    position = strategy.position
    if position is None or active is None or position.tp1_booked:
        return
    position.tp1_touched = True
    position.tp1_touch_time = touch_time
    position.tp1_minute = touch_time
    strategy.mark_tp1_booked(exit_time, close_price)
    active["TP1TouchTime"] = iso(touch_time)
    active["TP1ExitTime"] = iso(exit_time)
    active["TP1ExitPrice"] = close_price
    active["TP1Quantity"] = TP1_QUANTITY
    active["TP1ExecutionRule"] = "TARGET_TOUCH_1M_CLOSE"
    active["FinalQuantity"] = RUNNER_QUANTITY


def new_trade_record(
    trading_day: date,
    position,
    trigger: Candle,
    g1: Candle,
    entry_minute: datetime,
) -> dict[str, Any]:
    return {
        "Date": trading_day.isoformat(),
        "Month": trading_day.strftime("%Y-%m"),
        "Symbol": position.symbol,
        "SetupNumber": position.setup_number,
        "EntryMode": position.entry_mode,
        **candle_fields("Trigger", trigger),
        **candle_fields("G1", g1),
        "G1RangePercent": round((g1.high - g1.low) / g1.low * 100, 6),
        "EntryMinute": iso(entry_minute),
        "EntryTime": iso(position.entry_time),
        "EntryPrice": position.entry_price,
        "Quantity": QUANTITY,
        "InitialSL": position.initial_sl,
        "RiskPerShare": position.entry_price - position.initial_sl,
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
        "NetRR": None,
        "RMultiple": None,
        "HoldingMinutes": None,
    }


def finalize_record(record: dict[str, Any] | None, position) -> dict[str, Any]:
    if record is None:
        raise ValueError("Position closed without an active trade record.")
    result = dict(record)
    result["FinalExitTime"] = iso(position.final_exit_time)
    result["FinalExitPrice"] = position.final_exit_price
    result["FinalQuantity"] = RUNNER_QUANTITY if position.tp1_booked else QUANTITY
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
    net_rr = result["GrossPnL"] / initial_risk if initial_risk > 0 else None
    result["NetRR"] = round(net_rr, 4) if net_rr is not None else None
    result["RMultiple"] = result["NetRR"]
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
                "NetRR": round(float(group["NetRR"].sum()), 4),
                "AveragePnL": round(float(pnl.mean()), 2),
                "AverageRR": round(float(group["NetRR"].mean()), 4),
                "BestTrade": round(float(pnl.max()), 2),
                "WorstTrade": round(float(pnl.min()), 2),
                "TP1Trades": int(
                    group["TP1ExitTime"].fillna("").astype(bool).sum()
                ),
                "G2EntryTrades": int(
                    (group["EntryMode"] == "G2_G1_HIGH_BREAK").sum()
                ),
                "G3EntryTrades": int(
                    (group["EntryMode"] == "G3_G1_HIGH_BREAK").sum()
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
    end = pd.Timestamp(args.end, tz="Asia/Kolkata") + pd.Timedelta(
        hours=23, minutes=59
    )
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
            three_all = make_three_minute(minute)
            three_groups = {
                group_day: group.copy()
                for group_day, group in three_all.groupby(
                    three_all["Datetime"].dt.date
                )
            }
            symbol_trades = []
            symbol_events = []
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
            print(f"[{sequence}/{len(universe)}] {symbol}: {len(symbol_trades)} trades")
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
        trades_frame = trades_frame.sort_values(["EntryTime", "Symbol"]).reset_index(
            drop=True
        )
    if not events_frame.empty:
        events_frame = events_frame.sort_values(["EventTime", "Symbol"]).reset_index(
            drop=True
        )

    summarize(trades_frame, "Month").to_csv(
        args.output / "MonthlySummary.csv", index=False
    )
    summarize(trades_frame, "Date").to_csv(
        args.output / "DailySummary.csv", index=False
    )
    summarize(trades_frame, "Symbol").to_csv(
        args.output / "StockSummary.csv", index=False
    )
    trades_frame.to_csv(args.output / "Trades.csv", index=False)
    events_frame.to_csv(args.output / "SetupAudit.csv", index=False)
    coverage_frame.to_csv(args.output / "Coverage.csv", index=False)

    valid = coverage_frame[coverage_frame["Status"] == "OK"]
    pnl = trades_frame.get("GrossPnL", pd.Series(dtype=float))
    rr = trades_frame.get("NetRR", pd.Series(dtype=float))
    summary = {
        "RequestedStart": args.start,
        "RequestedEnd": args.end,
        "ActualDataStart": valid["FirstCandle"].min() if not valid.empty else None,
        "ActualDataEnd": valid["LastCandle"].max() if not valid.empty else None,
        "UniverseStocks": int(len(universe)),
        "CoveredStocks": int(len(valid)),
        "Trades": int(len(trades_frame)),
        "Winners": int((pnl > 0).sum()),
        "Losers": int((pnl < 0).sum()),
        "NetPnL": round(float(pnl.sum()), 2),
        "NetRR": round(float(rr.sum()), 4),
        "AverageRR": round(float(rr.mean()), 4) if len(rr) else 0.0,
        "Notes": [
            "HLC3 session VWAP resets at 09:15 IST.",
            "Only the first red 3m candle among 09:30, 09:33 and 09:36 is tested.",
            "The existing previous-green/VWAP/inside-range Trigger rules remain.",
            "The next four 1m candles are the complete guide window.",
            "The first green guide candle is G1 and its range must be <=0.20%.",
            "G2 must strictly break G1 high, or equal it for one G3 opportunity.",
            "G1/Trigger low breaks before entry discard the stock for the day.",
            "100 shares: 50 exit at 2.2R on target-touch 1m close.",
            "After TP1, runner stop moves to entry and only red completed 3m lows trail it.",
            "Active stop wins when stop and target/entry coexist in one 1m OHLC bar.",
            "No brokerage, taxes, fees or slippage are deducted.",
        ],
    }
    (args.output / "Summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2), flush=True)


def iso(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def safe_name(symbol: str) -> str:
    result = symbol
    for character in '<>:"/\\|?*':
        result = result.replace(character, "_")
    return result


if __name__ == "__main__":
    main()
