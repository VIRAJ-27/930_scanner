from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare saved first-red results with trigger-entry results."
    )
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    previous = pd.read_csv(args.previous / "MonthlySummary.csv")
    new = pd.read_csv(args.new / "MonthlySummary.csv")
    merged = previous.merge(
        new,
        on="Month",
        how="outer",
        suffixes=("Previous", "New"),
    ).sort_values("Month")

    comparison = pd.DataFrame(
        {
            "Month": merged["Month"],
            "PreviousTrades": merged["TradesPrevious"],
            "NewTrades": merged["TradesNew"],
            "TradesDelta": merged["TradesNew"] - merged["TradesPrevious"],
            "PreviousWinRate": merged["WinRatePrevious"],
            "NewWinRate": merged["WinRateNew"],
            "WinRateDeltaPctPoints": (
                merged["WinRateNew"] - merged["WinRatePrevious"]
            ),
            "PreviousNetPnL_300Shares": merged["NetPnLPrevious"],
            "PreviousPnL_100ShareEquivalent": merged["NetPnLPrevious"] / 3.0,
            "NewNetPnL_100Shares": merged["NetPnLNew"],
            "NormalizedPnLDelta": (
                merged["NetPnLNew"] - merged["NetPnLPrevious"] / 3.0
            ),
            "PreviousAverageR": merged["AverageRPrevious"],
            "NewAverageR": merged["AverageRNew"],
            "AverageRDelta": merged["AverageRNew"] - merged["AverageRPrevious"],
            "PreviousTP1Trades": merged["TP1TradesPrevious"],
            "NewTP1Trades": merged["TP1TradesNew"],
            "NewEarlyEntryTrades": merged["EarlyEntryTrades"],
            "NewFallbackEntryTrades": merged["FallbackEntryTrades"],
        }
    )
    comparison[
        [
            "PreviousWinRate",
            "NewWinRate",
            "WinRateDeltaPctPoints",
            "PreviousAverageR",
            "NewAverageR",
            "AverageRDelta",
        ]
    ] = comparison[
        [
            "PreviousWinRate",
            "NewWinRate",
            "WinRateDeltaPctPoints",
            "PreviousAverageR",
            "NewAverageR",
            "AverageRDelta",
        ]
    ].round(4)
    comparison[
        [
            "PreviousNetPnL_300Shares",
            "PreviousPnL_100ShareEquivalent",
            "NewNetPnL_100Shares",
            "NormalizedPnLDelta",
        ]
    ] = comparison[
        [
            "PreviousNetPnL_300Shares",
            "PreviousPnL_100ShareEquivalent",
            "NewNetPnL_100Shares",
            "NormalizedPnLDelta",
        ]
    ].round(2)
    comparison.to_csv(args.output / "MonthlyComparison.csv", index=False)

    previous_summary = json.loads(
        (args.previous / "Summary.json").read_text(encoding="utf-8")
    )
    new_summary = json.loads(
        (args.new / "Summary.json").read_text(encoding="utf-8")
    )
    previous_win_rate = (
        previous_summary["Winners"] / previous_summary["Trades"]
    )
    new_win_rate = new_summary["Winners"] / new_summary["Trades"]
    overall = pd.DataFrame(
        [
            {
                "Period": "2026-04-01 to 2026-07-24",
                "PreviousTrades": previous_summary["Trades"],
                "NewTrades": new_summary["Trades"],
                "TradesDelta": (
                    new_summary["Trades"] - previous_summary["Trades"]
                ),
                "PreviousWinRate": previous_win_rate,
                "NewWinRate": new_win_rate,
                "WinRateDeltaPctPoints": new_win_rate - previous_win_rate,
                "PreviousNetPnL_300Shares": previous_summary["NetPnL"],
                "PreviousPnL_100ShareEquivalent": (
                    previous_summary["NetPnL"] / 3.0
                ),
                "NewNetPnL_100Shares": new_summary["NetPnL"],
                "NormalizedPnLDelta": (
                    new_summary["NetPnL"] - previous_summary["NetPnL"] / 3.0
                ),
                "PreviousAverageR": previous_summary["AverageR"],
                "NewAverageR": new_summary["AverageR"],
                "AverageRDelta": (
                    new_summary["AverageR"] - previous_summary["AverageR"]
                ),
                "NewTP1Trades": new_summary["TP1Trades"],
                "NewEarlyEntryTrades": new_summary["EarlyEntryTrades"],
                "NewFallbackEntryTrades": new_summary["FallbackEntryTrades"],
            }
        ]
    )
    overall[
        [
            "PreviousWinRate",
            "NewWinRate",
            "WinRateDeltaPctPoints",
            "PreviousAverageR",
            "NewAverageR",
            "AverageRDelta",
        ]
    ] = overall[
        [
            "PreviousWinRate",
            "NewWinRate",
            "WinRateDeltaPctPoints",
            "PreviousAverageR",
            "NewAverageR",
            "AverageRDelta",
        ]
    ].round(4)
    overall[
        [
            "PreviousNetPnL_300Shares",
            "PreviousPnL_100ShareEquivalent",
            "NewNetPnL_100Shares",
            "NormalizedPnLDelta",
        ]
    ] = overall[
        [
            "PreviousNetPnL_300Shares",
            "PreviousPnL_100ShareEquivalent",
            "NewNetPnL_100Shares",
            "NormalizedPnLDelta",
        ]
    ].round(2)
    overall.to_csv(args.output / "OverallComparison.csv", index=False)


if __name__ == "__main__":
    main()
