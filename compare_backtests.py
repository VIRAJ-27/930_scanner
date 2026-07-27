from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare baseline and revised 9:30 scanner reports."
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--revised", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    baseline = pd.read_csv(args.baseline / "MonthlySummary.csv")
    revised = pd.read_csv(args.revised / "MonthlySummary.csv")
    comparison = baseline.merge(
        revised,
        on="Month",
        how="outer",
        suffixes=("Baseline", "Revised"),
    ).sort_values("Month")

    result = pd.DataFrame(
        {
            "Month": comparison["Month"],
            "BaselineTrades": comparison["TradesBaseline"],
            "RevisedTrades": comparison["TradesRevised"],
            "TradesDelta": (
                comparison["TradesRevised"] - comparison["TradesBaseline"]
            ),
            "BaselineWinRate": comparison["WinRateBaseline"],
            "RevisedWinRate": comparison["WinRateRevised"],
            "WinRateDeltaPctPoints": (
                comparison["WinRateRevised"] - comparison["WinRateBaseline"]
            ),
            "BaselineNetPnL_100Shares": comparison["NetPnLBaseline"],
            "RevisedNetPnL_300Shares": comparison["NetPnLRevised"],
            "RevisedPnL_100ShareEquivalent": comparison["NetPnLRevised"] / 3.0,
            "GrossPnLDelta": (
                comparison["NetPnLRevised"] - comparison["NetPnLBaseline"]
            ),
            "NormalizedPnLDelta": (
                comparison["NetPnLRevised"] / 3.0
                - comparison["NetPnLBaseline"]
            ),
            "BaselineAverageR": comparison["AverageRBaseline"],
            "RevisedAverageR": comparison["AverageRRevised"],
            "AverageRDelta": (
                comparison["AverageRRevised"] - comparison["AverageRBaseline"]
            ),
            "BaselineTP1Trades": comparison["TP1TradesBaseline"],
            "RevisedTP1Trades": comparison["TP1TradesRevised"],
            "RevisedTP2Trades": comparison["TP2Trades"],
        }
    )
    percentage_columns = [
        "BaselineWinRate",
        "RevisedWinRate",
        "WinRateDeltaPctPoints",
        "BaselineAverageR",
        "RevisedAverageR",
        "AverageRDelta",
    ]
    money_columns = [
        "BaselineNetPnL_100Shares",
        "RevisedNetPnL_300Shares",
        "RevisedPnL_100ShareEquivalent",
        "GrossPnLDelta",
        "NormalizedPnLDelta",
    ]
    result[percentage_columns] = result[percentage_columns].round(4)
    result[money_columns] = result[money_columns].round(2)
    result.to_csv(args.output / "MonthlyComparison.csv", index=False)

    baseline_summary = json.loads(
        (args.baseline / "Summary.json").read_text(encoding="utf-8")
    )
    revised_summary = json.loads(
        (args.revised / "Summary.json").read_text(encoding="utf-8")
    )
    overall = pd.DataFrame(
        [
            {
                "Period": "2026-04-01 to 2026-07-24",
                "BaselineTrades": baseline_summary["Trades"],
                "RevisedTrades": revised_summary["Trades"],
                "TradesDelta": (
                    revised_summary["Trades"] - baseline_summary["Trades"]
                ),
                "BaselineWinRate": (
                    baseline_summary["Winners"] / baseline_summary["Trades"]
                ),
                "RevisedWinRate": (
                    revised_summary["Winners"] / revised_summary["Trades"]
                ),
                "BaselineNetPnL_100Shares": baseline_summary["NetPnL"],
                "RevisedNetPnL_300Shares": revised_summary["NetPnL"],
                "RevisedPnL_100ShareEquivalent": (
                    revised_summary["NetPnL"] / 3.0
                ),
                "GrossPnLDelta": (
                    revised_summary["NetPnL"] - baseline_summary["NetPnL"]
                ),
                "NormalizedPnLDelta": (
                    revised_summary["NetPnL"] / 3.0
                    - baseline_summary["NetPnL"]
                ),
                "BaselineAverageR": baseline_summary["AverageR"],
                "RevisedAverageR": revised_summary["AverageR"],
                "AverageRDelta": (
                    revised_summary["AverageR"] - baseline_summary["AverageR"]
                ),
                "RevisedTP1Trades": revised_summary["TP1Trades"],
                "RevisedTP2Trades": revised_summary["TP2Trades"],
            }
        ]
    )
    overall[
        [
            "BaselineWinRate",
            "RevisedWinRate",
            "BaselineAverageR",
            "RevisedAverageR",
            "AverageRDelta",
        ]
    ] = overall[
        [
            "BaselineWinRate",
            "RevisedWinRate",
            "BaselineAverageR",
            "RevisedAverageR",
            "AverageRDelta",
        ]
    ].round(4)
    overall[
        [
            "BaselineNetPnL_100Shares",
            "RevisedNetPnL_300Shares",
            "RevisedPnL_100ShareEquivalent",
            "GrossPnLDelta",
            "NormalizedPnLDelta",
        ]
    ] = overall[
        [
            "BaselineNetPnL_100Shares",
            "RevisedNetPnL_300Shares",
            "RevisedPnL_100ShareEquivalent",
            "GrossPnLDelta",
            "NormalizedPnLDelta",
        ]
    ].round(2)
    overall.to_csv(args.output / "OverallComparison.csv", index=False)


if __name__ == "__main__":
    main()
