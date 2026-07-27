from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    trades = pd.read_csv(args.output / "Trades.csv")
    monthly = pd.read_csv(args.output / "MonthlySummary.csv")
    coverage = pd.read_csv(args.output / "Coverage.csv")

    entry = pd.to_numeric(trades["EntryPrice"])
    stop = pd.to_numeric(trades["InitialSL"])
    risk = entry - stop
    target = pd.to_numeric(trades["TP1Target"])
    pnl = pd.to_numeric(trades["GrossPnL"])
    rr = pd.to_numeric(trades["NetRR"])
    trigger_times = pd.to_datetime(trades["TriggerTime"])
    g1_times = pd.to_datetime(trades["G1Time"])
    entry_times = pd.to_datetime(trades["EntryTime"])

    checks = {
        "Trades": len(trades),
        "CoveredStocks": int((coverage["Status"] == "OK").sum()),
        "CoverageErrors": int((coverage["Status"] == "ERROR").sum()),
        "DuplicateStockDays": int(trades.duplicated(["Date", "Symbol"]).sum()),
        "BadQuantity": int((trades["Quantity"] != 100).sum()),
        "BadRisk": int((risk <= 0).sum()),
        "BadG1Range": int((trades["G1RangePercent"] > 0.2 + 1e-9).sum()),
        "BadTarget": int(
            ((target - (entry + 2.2 * risk)).abs() > 1e-6).sum()
        ),
        "BadNetRR": int(((rr - pnl / (risk * 100)).abs() > 5e-5).sum()),
        "BadTriggerStart": int(
            (~trigger_times.dt.strftime("%H:%M").isin(["09:30", "09:33", "09:36"])).sum()
        ),
        "BadEntryMode": int(
            (
                ~trades["EntryMode"].isin(
                    ["G2_G1_HIGH_BREAK", "G3_G1_HIGH_BREAK"]
                )
            ).sum()
        ),
        "BadG2Timing": int(
            (
                (trades["EntryMode"] == "G2_G1_HIGH_BREAK")
                & (entry_times.dt.floor("min") != g1_times + pd.Timedelta(minutes=1))
            ).sum()
        ),
        "BadG3Timing": int(
            (
                (trades["EntryMode"] == "G3_G1_HIGH_BREAK")
                & (entry_times.dt.floor("min") != g1_times + pd.Timedelta(minutes=2))
            ).sum()
        ),
        "MonthlyPnLDelta": round(
            float(monthly["NetPnL"].sum() - trades["GrossPnL"].sum()), 6
        ),
        "MonthlyNetRRDelta": round(
            float(monthly["NetRR"].sum() - trades["NetRR"].sum()), 6
        ),
    }
    failures = {
        key: value
        for key, value in checks.items()
        if key.startswith("Bad")
        and value
        or key in {
            "CoverageErrors",
            "DuplicateStockDays",
            "MonthlyPnLDelta",
            "MonthlyNetRRDelta",
        }
        and value
    }
    for key, value in checks.items():
        print(f"{key}: {value}")
    if failures:
        raise SystemExit(f"Audit failed: {failures}")


if __name__ == "__main__":
    main()
