from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from backtest import summarize


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Combine scanner backtest chunks.")
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--requested-start", default="2026-04-01")
    parser.add_argument("--requested-end", default="2026-07-26")
    return parser.parse_args()


def read_all(chunks: list[Path], filename: str) -> pd.DataFrame:
    frames = []
    for chunk in chunks:
        path = chunk / filename
        if path.exists() and path.stat().st_size > 0:
            try:
                frames.append(pd.read_csv(path))
            except pd.errors.EmptyDataError:
                continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> None:
    args = parse_args()
    chunks = sorted(path for path in args.chunks.glob("chunk_*") if path.is_dir())
    if not chunks:
        raise FileNotFoundError("No chunk directories found.")
    args.output.mkdir(parents=True, exist_ok=True)

    trades = read_all(chunks, "Trades.csv")
    events = read_all(chunks, "SetupAudit.csv")
    coverage = read_all(chunks, "Coverage.csv")
    if not trades.empty:
        trades = trades.sort_values(["EntryTime", "Symbol"]).reset_index(drop=True)
    if not events.empty:
        events = events.sort_values(["EventTime", "Symbol"]).reset_index(drop=True)
    if not coverage.empty:
        coverage = coverage.sort_values("Symbol").reset_index(drop=True)

    monthly = summarize(trades, "Month")
    daily = summarize(trades, "Date")
    stock = summarize(trades, "Symbol")
    trades.to_csv(args.output / "Trades.csv", index=False)
    events.to_csv(args.output / "SetupAudit.csv", index=False)
    setup_report = events[
        events["EventType"].isin(
            ["G1_VALID", "SETUP_FAILED", "ENTRY_SIGNAL"]
        )
    ].copy()
    setup_report.to_csv(args.output / "SetupReport.csv", index=False)
    event_summary = (
        events.assign(Outcome=events["Outcome"].fillna(""))
        .groupby(["EventType", "Outcome"], dropna=False)
        .size()
        .reset_index(name="Count")
        .sort_values(["EventType", "Outcome"])
    )
    event_summary.to_csv(args.output / "EventSummary.csv", index=False)
    monthly.to_csv(args.output / "MonthlySummary.csv", index=False)
    daily.to_csv(args.output / "DailySummary.csv", index=False)
    stock.to_csv(args.output / "StockSummary.csv", index=False)
    coverage.to_csv(args.output / "Coverage.csv", index=False)

    valid = coverage[coverage["Status"] == "OK"]
    actual_start = valid["FirstCandle"].min() if not valid.empty else None
    actual_end = valid["LastCandle"].max() if not valid.empty else None
    rules = pd.DataFrame(
        [
            ("Universe", "All 210 NSE stock F&O underlyings in FO_Universe.csv"),
            ("Period requested", f"{args.requested_start} through {args.requested_end}"),
            ("Actual data coverage", f"{actual_start} through {actual_end}"),
            ("Candle alignment", "3m candles aligned to 09:15 IST"),
            ("VWAP source", "HLC3 = (High + Low + Close) / 3"),
            ("VWAP reset", "Every trading session at 09:15 IST"),
            (
                "Trigger",
                "First red 3m candle among 09:30, 09:33 and 09:36 is the only candidate; "
                "must close above VWAP and inside previous green 3m range, "
                "otherwise discard stock for the day",
            ),
            (
                "G1 window",
                "First green candle in the next three completed 1m candles after Trigger",
            ),
            (
                "G1",
                "Trigger-low break before/during G1 discards the stock",
            ),
            (
                "Entry",
                "Strict G1-high break in any of the next three 1m candles; "
                "G1/Trigger-low break first discards",
            ),
            (
                "EP and R1",
                "Trigger range <0.50% uses range x1.4; otherwise adds 0.10 "
                "percentage points; R1 is EP% above Trigger high",
            ),
            (
                "TP1",
                "Minimum of R1 and entry + 3x(entry-Trigger low); sell 70 "
                "shares at target-touch 1m candle close",
            ),
            (
                "Runner",
                "30 shares; move SL to Trigger high after TP1, then use "
                "monotonic completed 5m candle lows",
            ),
            (
                "Pre-TP1 stop upgrade",
                "After entry, a completed 3m close above Trigger high moves "
                "SL from Trigger low to G1 low",
            ),
            ("Maximum setups", "One setup per stock/day"),
            ("Market exit", "15:15 IST"),
            ("Costs", "Brokerage, taxes, fees and slippage excluded"),
        ],
        columns=["Rule", "Value"],
    )
    rules.to_csv(args.output / "Rules.csv", index=False)

    pnl = trades.get("GrossPnL", pd.Series(dtype=float))
    summary = {
        "RequestedStart": args.requested_start,
        "RequestedEnd": args.requested_end,
        "ActualDataStart": actual_start,
        "ActualDataEnd": actual_end,
        "UniverseStocks": int(len(coverage)),
        "CoveredStocks": int(len(valid)),
        "Trades": int(len(trades)),
        "Winners": int((pnl > 0).sum()),
        "Losers": int((pnl < 0).sum()),
        "WinRate": round(float((pnl > 0).mean()), 4) if len(pnl) else 0.0,
        "NetPnL": round(float(pnl.sum()), 2),
        "AveragePnL": round(float(pnl.mean()), 2) if len(pnl) else 0.0,
        "NetRR": (
            round(float(trades["NetRR"].sum()), 4)
            if not trades.empty
            else 0.0
        ),
        "AverageRR": (
            round(float(trades["NetRR"].mean()), 4)
            if not trades.empty
            else 0.0
        ),
        "TP1Trades": (
            int(trades["TP1ExitTime"].fillna("").astype(bool).sum())
            if not trades.empty
            else 0
        ),
        "R1TargetTrades": (
            int((trades["TargetDriver"] == "R1").sum())
            if not trades.empty
            else 0
        ),
        "R2TargetTrades": (
            int((trades["TargetDriver"] == "R2").sum())
            if not trades.empty
            else 0
        ),
        "InitialSLTrades": (
            int((trades["ExitReason"] == "INITIAL_SL").sum())
            if not trades.empty
            else 0
        ),
    }
    (args.output / "Summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
