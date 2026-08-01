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
    trigger_high = pd.to_numeric(trades["TriggerHigh"])
    trigger_low = pd.to_numeric(trades["TriggerLow"])
    trigger_range = (trigger_high - trigger_low) / trigger_low * 100
    expected_ep = trigger_range.where(
        trigger_range >= 0.50, trigger_range * 1.4
    )
    expected_ep = expected_ep.where(trigger_range < 0.50, trigger_range + 0.10)
    expected_r1 = trigger_high * (1 + expected_ep / 100)
    expected_r2 = entry + 3 * risk
    expected_target = pd.concat([expected_r1, expected_r2], axis=1).min(axis=1)
    pnl = pd.to_numeric(trades["GrossPnL"])
    rr = pd.to_numeric(trades["NetRR"])
    tp1_hit = trades["TP1ExitTime"].fillna("").astype(bool)
    tp1_exit = pd.to_numeric(trades["TP1ExitPrice"], errors="coerce")
    final_exit = pd.to_numeric(trades["FinalExitPrice"])
    expected_tp1_pnl = (tp1_exit - entry).fillna(0) * 70
    expected_runner_pnl = (final_exit - entry).where(tp1_hit, 0) * 30
    expected_gross = expected_tp1_pnl + (final_exit - entry) * trades["FinalQuantity"]

    checks = {
        "Trades": len(trades),
        "CoveredStocks": int((coverage["Status"] == "OK").sum()),
        "CoverageErrors": int((coverage["Status"] == "ERROR").sum()),
        "DuplicateStockDays": int(trades.duplicated(["Date", "Symbol"]).sum()),
        "BadQuantity": int((trades["Quantity"] != 100).sum()),
        "BadInitialSL": int(((stop - trigger_low).abs() > 1e-8).sum()),
        "BadRisk": int((risk <= 0).sum()),
        "BadTriggerRange": int(
            ((trades["TriggerRangePercent"] - trigger_range).abs() > 1e-8).sum()
        ),
        "BadEP": int(((trades["EPPercent"] - expected_ep).abs() > 1e-8).sum()),
        "BadR1": int(((trades["R1Target"] - expected_r1).abs() > 1e-6).sum()),
        "BadR2": int(((trades["R2Target"] - expected_r2).abs() > 1e-6).sum()),
        "BadTP1": int(((trades["TP1Target"] - expected_target).abs() > 1e-6).sum()),
        "BadNetRR": int(((rr - pnl / (risk * 100)).abs() > 5e-5).sum()),
        "BadG1Delay": int((~trades["G1DelayCandle"].isin([1, 2, 3])).sum()),
        "BadEntryDelay": int((~trades["EntryDelayAfterG1"].isin([1, 2, 3])).sum()),
        "BadG1Low": int((trades["G1Low"] < trigger_low).sum()),
        "BadTP1Quantity": int((~trades["TP1Quantity"].isin([0, 70])).sum()),
        "BadFinalQuantity": int((~trades["FinalQuantity"].isin([30, 100])).sum()),
        "BadTP1PnL": int(((trades["TP1PnL"] - expected_tp1_pnl).abs() > 0.011).sum()),
        "BadRunnerPnL": int(((trades["RunnerPnL"] - expected_runner_pnl).abs() > 0.011).sum()),
        "BadGrossPnL": int(((pnl - expected_gross).abs() > 0.011).sum()),
        "BadTargetDriver": int(
            (
                trades["TargetDriver"]
                != expected_r1.le(expected_r2).map({True: "R1", False: "R2"})
            ).sum()
        ),
        "BadPost3mSL": int(
            (
                trades["Post3mSL"].notna()
                & ((trades["Post3mSL"] - trades["G1Low"]).abs() > 1e-8)
            ).sum()
        ),
        "MonthlyPnLDelta": round(float(monthly["NetPnL"].sum() - pnl.sum()), 6),
        "MonthlyNetRRDelta": round(float(monthly["NetRR"].sum() - rr.sum()), 6),
    }
    failures = {
        key: value
        for key, value in checks.items()
        if value
        and (
            key.startswith("Bad")
            or key in {
                "CoverageErrors",
                "DuplicateStockDays",
                "MonthlyPnLDelta",
                "MonthlyNetRRDelta",
            }
        )
    }
    for key, value in checks.items():
        print(f"{key}: {value}")
    if failures:
        raise SystemExit(f"Audit failed: {failures}")


if __name__ == "__main__":
    main()
