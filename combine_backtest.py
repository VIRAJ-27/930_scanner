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
                "Guide window",
                "Only the next four completed 1m candles after Trigger are checked",
            ),
            (
                "G1",
                "First green 1m candle in the guide window; range "
                "(High-Low)/Low must be <=0.20%",
            ),
            (
                "Entry",
                "G2 must strictly break G1 high, or equal it and allow G3 "
                "one strict-break opportunity; G2 below G1 high discards",
            ),
            (
                "TP1",
                "2.2R from entry to G1 low; sell 50 shares at target-touch "
                "1m candle close",
            ),
            (
                "Runner",
                "50 shares; move SL to entry after TP1, then use monotonic "
                "completed red 3m candle lows",
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
        "G2EntryTrades": (
            int((trades["EntryMode"] == "G2_G1_HIGH_BREAK").sum())
            if not trades.empty
            else 0
        ),
        "G3EntryTrades": (
            int((trades["EntryMode"] == "G3_G1_HIGH_BREAK").sum())
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
