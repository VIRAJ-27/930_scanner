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
    parser.add_argument(
        "--supplement",
        type=Path,
        action="append",
        default=[],
        help="Additional data folder; repeat for multiple date supplements.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", default="2026-04-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--end-index", type=int)
    return parser.parse_args()


def load_symbol(symbol, base_data, supplement, start, end):
    paths = [base_data / f"{safe_name(symbol)}_1m.csv"]
    for folder in supplement or []:
        paths.append(folder / f"{safe_name(symbol)}_1m.csv")
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
    result = result[(result["Datetime"] >= start) & (result["Datetime"] <= end)]
    for column in ["Open", "High", "Low", "Close", "Volume"]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.dropna(subset=["Open", "High", "Low", "Close"]).copy()


def make_timeframe(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    working = frame.copy()
    working["Bucket"] = working["Datetime"].dt.floor(f"{minutes}min")
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
    if minutes == 3:
        typical = (result["High"] + result["Low"] + result["Close"]) / 3.0
        dates = result["Datetime"].dt.date
        cumulative_volume = result["Volume"].groupby(dates).cumsum()
        cumulative_pv = (typical * result["Volume"]).groupby(dates).cumsum()
        result["VWAP"] = cumulative_pv / cumulative_volume
        result.loc[cumulative_volume <= 0, "VWAP"] = float("nan")
    else:
        result["VWAP"] = float("nan")
    result["Completion"] = result["Datetime"] + pd.Timedelta(minutes=minutes)
    return result


def make_three_minute(frame: pd.DataFrame) -> pd.DataFrame:
    return make_timeframe(frame, 3)


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
    five_frame: pd.DataFrame | None = None,
    ema20_1m_history: list[float] | None = None,
    ema20_3m_history: list[float] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events = []

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
    initial_ema20_1m = list(ema20_1m_history or [])
    initial_ema20_3m = list(ema20_3m_history or [])
    if ema20_1m_history is not None:
        strategy.ema20_1m = ema20_1m_history
    if ema20_3m_history is not None:
        strategy.ema20_3m = ema20_3m_history
    three = three_frame if three_frame is not None else make_timeframe(minute_frame, 3)
    five = five_frame if five_frame is not None else make_timeframe(minute_frame, 5)
    three_by_end = {
        row.Completion.to_pydatetime(): row for row in three.itertuples(index=False)
    }
    five_by_end = {
        row.Completion.to_pydatetime(): row for row in five.itertuples(index=False)
    }
    trades = []
    active = None

    for minute in minute_frame.itertuples(index=False):
        timestamp = minute.Datetime.to_pydatetime()
        minute_end = timestamp + timedelta(minutes=1)

        three_row = three_by_end.get(timestamp)
        if three_row is not None:
            position = strategy.position
            old_sl = position.current_sl if position and position.open_quantity else None
            strategy.on_three_minute(row_to_candle(symbol, three_row, 3))
            position = strategy.position
            if (
                active is not None
                and position is not None
                and old_sl is not None
                and position.current_sl > old_sl
            ):
                active["G1SLMoveTime"] = iso(timestamp)
                active["Post3mSL"] = position.current_sl

        five_row = five_by_end.get(timestamp)
        if five_row is not None:
            strategy.on_five_minute(row_to_candle(symbol, five_row, 5))

        position = strategy.position
        if position is not None and position.open_quantity > 0:
            if timestamp.time() >= MARKET_EXIT:
                strategy.mark_closed(timestamp, float(minute.Open), "MARKET_EXIT_1515")
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
                and (
                    position.tp1_touched
                    or float(minute.High) >= position.tp1_target
                )
            ):
                book_tp1(strategy, active, timestamp, minute_end, float(minute.Close))
            strategy.on_one_minute(row_to_candle(symbol, minute, 1))
            continue

        if strategy.done:
            break

        entered = False
        setup = strategy.setup
        if setup is not None:
            if (
                strategy.state in {"WAIT_G1", "WAIT_ENTRY"}
                and float(minute.Low) < setup.trigger.low
            ):
                strategy.on_entry_tick(timestamp, setup.trigger.low - TICK_SIZE)
            elif strategy.state == "WAIT_ENTRY" and setup.g1 is not None:
                if float(minute.Low) < setup.g1.low:
                    strategy.on_entry_tick(timestamp, setup.g1.low - TICK_SIZE)
                elif float(minute.High) > setup.g1.high:
                    entry_price = max(
                        float(minute.Open),
                        round(setup.g1.high + TICK_SIZE, 2),
                    )
                    position = strategy.on_entry_tick(timestamp, entry_price)
                    if position is not None:
                        active = new_trade_record(
                            trading_day,
                            position,
                            setup.trigger,
                            setup.g1,
                            setup,
                            timestamp,
                        )
                        entered = True
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
                            position.tp1_touched
                            or float(minute.High) >= position.tp1_target
                        ):
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

    # The strategy stops looking for setups after its one allowed attempt, but
    # continuous EMA history must still consume every completed candle so the
    # next trading session starts from the correct value.
    if ema20_1m_history is not None:
        ema20_1m_history.clear()
        ema20_1m_history.extend(initial_ema20_1m)
        for close in minute_frame["Close"]:
            ScannerStrategy._append_ema(ema20_1m_history, float(close))
    if ema20_3m_history is not None:
        ema20_3m_history.clear()
        ema20_3m_history.extend(initial_ema20_3m)
        for close in three["Close"]:
            ScannerStrategy._append_ema(ema20_3m_history, float(close))
    return trades, events


def book_tp1(strategy, active, touch_time, exit_time, close_price):
    position = strategy.position
    if position is None or active is None or position.tp1_booked:
        return
    if not position.tp1_touched:
        position.tp1_touched = True
        position.tp1_touch_time = touch_time
        position.tp1_minute = touch_time
    strategy.mark_tp1_booked(exit_time, close_price)
    active["TP1TouchTime"] = iso(position.tp1_touch_time)
    active["TP1ExitTime"] = iso(exit_time)
    active["TP1ExitPrice"] = close_price
    active["TP1Quantity"] = TP1_QUANTITY
    active["TP1ExecutionRule"] = "TARGET_TOUCH_1M_CLOSE"
    active["FinalQuantity"] = RUNNER_QUANTITY


def new_trade_record(trading_day, position, trigger, g1, setup, entry_minute):
    g1_delay = int((g1.start - trigger.completion_time).total_seconds() / 60) + 1
    entry_delay = int(
        (entry_minute - (g1.start + timedelta(minutes=1))).total_seconds() / 60
    ) + 1
    return {
        "Date": trading_day.isoformat(),
        "Month": trading_day.strftime("%Y-%m"),
        "Symbol": position.symbol,
        "SetupNumber": 1,
        "EntryMode": position.entry_mode,
        "EntryTier": position.entry_tier,
        "EMA3mRisePercent": (
            None
            if position.ema_3m_rise_fraction is None
            else position.ema_3m_rise_fraction * 100
        ),
        "EMA1mRisePercent": (
            None
            if position.ema_1m_rise_fraction is None
            else position.ema_1m_rise_fraction * 100
        ),
        "G1BodyPercent": (
            None
            if position.g1_body_fraction is None
            else position.g1_body_fraction * 100
        ),
        **candle_fields("Trigger", trigger),
        "TriggerRangePercent": setup.trigger_range_fraction * 100,
        "EPPercent": setup.ep_fraction * 100,
        "R1Target": position.r1_target,
        **candle_fields("G1", g1),
        "G1DelayCandle": g1_delay,
        "EntryDelayAfterG1": entry_delay,
        "EntryMinute": iso(entry_minute),
        "EntryTime": iso(position.entry_time),
        "EntryPrice": position.entry_price,
        "Quantity": QUANTITY,
        "InitialSL": position.initial_sl,
        "G1Low": g1.low,
        "G1SLMoveTime": "",
        "Post3mSL": None,
        "RiskPerShare": position.entry_price - position.initial_sl,
        "RiskPercent": (position.entry_price - position.initial_sl) / position.entry_price * 100,
        "R2Target": position.r2_target,
        "TP1Target": position.tp1_target,
        "TargetDriver": "R1" if position.r1_target <= position.r2_target else "R2",
        "TP1TouchTime": "",
        "TP1ExitTime": "",
        "TP1ExitPrice": None,
        "TP1Quantity": 0,
        "TP1ExecutionRule": "",
        "FinalExitTime": "",
        "FinalExitPrice": None,
        "FinalQuantity": QUANTITY,
        "ExitReason": "",
        "TP1PnL": None,
        "RunnerPnL": None,
        "GrossPnL": None,
        "NetRR": None,
        "RMultiple": None,
        "HoldingMinutes": None,
    }


def finalize_record(record, position):
    if record is None:
        raise ValueError("Position closed without a trade record.")
    result = dict(record)
    result["FinalExitTime"] = iso(position.final_exit_time)
    result["FinalExitPrice"] = position.final_exit_price
    final_quantity = RUNNER_QUANTITY if position.tp1_booked else QUANTITY
    result["FinalQuantity"] = final_quantity
    result["ExitReason"] = position.final_exit_reason
    tp1_pnl = (
        (position.tp1_exit_price - position.entry_price) * TP1_QUANTITY
        if position.tp1_exit_price is not None
        else 0.0
    )
    runner_pnl = (
        (position.final_exit_price - position.entry_price) * final_quantity
        if position.tp1_booked
        else 0.0
    )
    final_pnl = (
        runner_pnl
        if position.tp1_booked
        else (position.final_exit_price - position.entry_price) * QUANTITY
    )
    result["TP1PnL"] = round(tp1_pnl, 2)
    result["RunnerPnL"] = round(runner_pnl, 2)
    result["GrossPnL"] = round(tp1_pnl + final_pnl, 2)
    initial_risk = float(result["RiskPerShare"]) * QUANTITY
    net_rr = result["GrossPnL"] / initial_risk if initial_risk > 0 else None
    result["NetRR"] = round(net_rr, 4) if net_rr is not None else None
    result["RMultiple"] = result["NetRR"]
    result["HoldingMinutes"] = round(
        (position.final_exit_time - position.entry_time).total_seconds() / 60, 2
    )
    return result


def candle_fields(prefix, candle):
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
    for value, group in trades.groupby(key, dropna=False):
        pnl = group["GrossPnL"]
        rr = group["NetRR"]
        rows.append(
            {
                key: value,
                "Trades": len(group),
                "Winners": int((pnl > 0).sum()),
                "Losers": int((pnl < 0).sum()),
                "Flat": int((pnl == 0).sum()),
                "WinRate": round(float((pnl > 0).mean()), 4),
                "GrossProfit": round(float(pnl[pnl > 0].sum()), 2),
                "GrossLoss": round(float(pnl[pnl < 0].sum()), 2),
                "NetPnL": round(float(pnl.sum()), 2),
                "NetRR": round(float(rr.sum()), 4),
                "AveragePnL": round(float(pnl.mean()), 2),
                "AverageRR": round(float(rr.mean()), 4),
                "TP1Trades": int(group["TP1ExitTime"].fillna("").astype(bool).sum()),
                "TP1Rate": round(float(group["TP1ExitTime"].fillna("").astype(bool).mean()), 4),
                "InitialSLTrades": int((group["ExitReason"] == "INITIAL_SL").sum()),
                "RunnerPnL": round(float(group["RunnerPnL"].sum()), 2),
                "BestTrade": round(float(pnl.max()), 2),
                "WorstTrade": round(float(pnl.min()), 2),
            }
        )
    return pd.DataFrame(rows).sort_values(key).reset_index(drop=True)


def main():
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(args.base_data / "FO_Universe.csv", dtype=str).fillna("")
    universe = universe.drop_duplicates("Symbol").sort_values("Symbol")
    universe = universe.iloc[args.start_index : args.end_index].copy()
    start = pd.Timestamp(args.start, tz="Asia/Kolkata")
    end = pd.Timestamp(args.end, tz="Asia/Kolkata") + pd.Timedelta(hours=23, minutes=59)
    all_trades, all_events, coverage = [], [], []

    for sequence, (_, row) in enumerate(universe.iterrows(), start=1):
        symbol = str(row["Symbol"]).strip().upper()
        try:
            minute_all = load_symbol(
                symbol,
                args.base_data,
                args.supplement,
                pd.Timestamp("1900-01-01", tz="Asia/Kolkata"),
                end,
            )
            minute = minute_all[minute_all["Datetime"] >= start].copy()
            if minute.empty:
                coverage.append({"Symbol": symbol, "Status": "NO_DATA"})
                continue
            three_with_history = make_timeframe(minute_all, 3)
            three_all = three_with_history[
                three_with_history["Datetime"] >= start
            ].copy()
            five_all = make_timeframe(minute, 5)
            three_groups = {d: g.copy() for d, g in three_all.groupby(three_all["Datetime"].dt.date)}
            five_groups = {d: g.copy() for d, g in five_all.groupby(five_all["Datetime"].dt.date)}
            ema20_1m_history: list[float] = []
            ema20_3m_history: list[float] = []
            for close in minute_all.loc[
                minute_all["Datetime"] < start,
                "Close",
            ]:
                ScannerStrategy._append_ema(ema20_1m_history, float(close))
            for close in three_with_history.loc[
                three_with_history["Completion"] <= start,
                "Close",
            ]:
                ScannerStrategy._append_ema(ema20_3m_history, float(close))
            symbol_trades, symbol_events = [], []
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
                    five_groups.get(trading_day),
                    ema20_1m_history,
                    ema20_3m_history,
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
            print(f"[{sequence}/{len(universe)}] {symbol}: {len(symbol_trades)} trades", flush=True)
        except Exception as error:
            coverage.append({"Symbol": symbol, "Status": "ERROR", "Error": f"{type(error).__name__}: {error}"})

    trades = pd.DataFrame(all_trades)
    events = pd.DataFrame(all_events)
    coverage_frame = pd.DataFrame(coverage)
    if not trades.empty:
        trades = trades.sort_values(["EntryTime", "Symbol"]).reset_index(drop=True)
    if not events.empty:
        events = events.sort_values(["EventTime", "Symbol"]).reset_index(drop=True)
    summarize(trades, "Month").to_csv(args.output / "MonthlySummary.csv", index=False)
    summarize(trades, "EntryTier").to_csv(
        args.output / "EntryTierSummary.csv",
        index=False,
    )
    summarize(trades, "Date").to_csv(args.output / "DailySummary.csv", index=False)
    summarize(trades, "Symbol").to_csv(args.output / "StockSummary.csv", index=False)
    trades.to_csv(args.output / "Trades.csv", index=False)
    events.to_csv(args.output / "SetupAudit.csv", index=False)
    coverage_frame.to_csv(args.output / "Coverage.csv", index=False)

    valid = coverage_frame[coverage_frame["Status"] == "OK"]
    pnl = trades.get("GrossPnL", pd.Series(dtype=float))
    rr = trades.get("NetRR", pd.Series(dtype=float))
    summary = {
        "RequestedStart": args.start,
        "RequestedEnd": args.end,
        "ActualDataStart": valid["FirstCandle"].min() if not valid.empty else None,
        "ActualDataEnd": valid["LastCandle"].max() if not valid.empty else None,
        "UniverseStocks": int(len(universe)),
        "CoveredStocks": int(len(valid)),
        "Trades": int(len(trades)),
        "Winners": int((pnl > 0).sum()),
        "Losers": int((pnl < 0).sum()),
        "WinRate": round(float((pnl > 0).mean()), 4) if len(pnl) else 0,
        "NetPnL": round(float(pnl.sum()), 2),
        "NetRR": round(float(rr.sum()), 4),
        "AverageRR": round(float(rr.mean()), 4) if len(rr) else 0,
        "TP1Trades": int(trades["TP1ExitTime"].fillna("").astype(bool).sum()) if len(trades) else 0,
        "NormalTrades": int((trades.get("EntryTier", "") == "NORMAL").sum()) if len(trades) else 0,
        "SilverTrades": int((trades.get("EntryTier", "") == "SILVER").sum()) if len(trades) else 0,
        "Notes": [
            "HLC3 session VWAP resets at 09:15 IST.",
            "The first red 3m Trigger among 09:30, 09:33 and 09:36 uses the unchanged validation rules.",
            "G1 is the first green candle in the next three 1m candles.",
            "Entry is a strict G1-high break in any of the next three 1m candles.",
            "Silver: completed 1m EMA20 rises at least 0.116% over five candles and G1 body is at least 57.9% of range.",
            "Normal: when Silver fails, completed 3m EMA20 rises at least 0.01% over two candles.",
            "Silver has precedence; entries passing neither tier are discarded.",
            "Trigger/G1-low breaks before entry discard the stock.",
            "EP is Trigger range x1.4 below 0.50%, otherwise range +0.10 percentage points.",
            "TP1 is min(R1 from Trigger high, R2 at 3R from entry/Trigger low).",
            "100 shares: 70 exit at TP1 close; 30 trail after SL moves to Trigger high.",
            "After entry, a completed 3m close above Trigger high moves SL to G1 low.",
            "After TP1, completed 5m candle lows trail monotonically.",
            "Stop-side failure wins when both sides occur in one 1m OHLC bar.",
            "No brokerage, taxes, fees or slippage are deducted.",
        ],
    }
    (args.output / "Summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)


def iso(value):
    return value.isoformat() if value is not None else ""


def safe_name(symbol):
    result = symbol
    for character in '<>:"/\\|?*':
        result = result.replace(character, "_")
    return result


if __name__ == "__main__":
    main()
