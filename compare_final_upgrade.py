from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare the prior revised scanner with the final upgrade."
    )
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--final", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    previous = pd.read_csv(args.previous / "MonthlySummary.csv")
    final = pd.read_csv(args.final / "MonthlySummary.csv")
    merged = previous.merge(
        final,
        on="Month",
        how="outer",
        suffixes=("Previous", "Final"),
    ).sort_values("Month")

    comparison = pd.DataFrame(
        {
            "Month": merged["Month"],
            "PreviousTrades": merged["TradesPrevious"],
            "FinalTrades": merged["TradesFinal"],
            "TradesDelta": merged["TradesFinal"] - merged["TradesPrevious"],
            "PreviousWinRate": merged["WinRatePrevious"],
            "FinalWinRate": merged["WinRateFinal"],
            "WinRateDeltaPctPoints": (
                merged["WinRateFinal"] - merged["WinRatePrevious"]
            ),
            "PreviousNetPnL": merged["NetPnLPrevious"],
            "FinalNetPnL": merged["NetPnLFinal"],
            "NetPnLDelta": merged["NetPnLFinal"] - merged["NetPnLPrevious"],
            "PreviousAveragePnL": merged["AveragePnLPrevious"],
            "FinalAveragePnL": merged["AveragePnLFinal"],
            "PreviousAverageR": merged["AverageRPrevious"],
            "FinalAverageR": merged["AverageRFinal"],
            "AverageRDelta": (
                merged["AverageRFinal"] - merged["AverageRPrevious"]
            ),
            "PreviousTP1Trades": merged["TP1TradesPrevious"],
            "FinalTP1Trades": merged["TP1TradesFinal"],
            "PreviousTP2Trades": merged["TP2TradesPrevious"],
            "FinalTP2Trades": merged["TP2TradesFinal"],
        }
    )
    comparison[
        [
            "PreviousWinRate",
            "FinalWinRate",
            "WinRateDeltaPctPoints",
            "PreviousAverageR",
            "FinalAverageR",
            "AverageRDelta",
        ]
    ] = comparison[
        [
            "PreviousWinRate",
            "FinalWinRate",
            "WinRateDeltaPctPoints",
            "PreviousAverageR",
            "FinalAverageR",
            "AverageRDelta",
        ]
    ].round(4)
    comparison[
        [
            "PreviousNetPnL",
            "FinalNetPnL",
            "NetPnLDelta",
            "PreviousAveragePnL",
            "FinalAveragePnL",
        ]
    ] = comparison[
        [
            "PreviousNetPnL",
            "FinalNetPnL",
            "NetPnLDelta",
            "PreviousAveragePnL",
            "FinalAveragePnL",
        ]
    ].round(2)
    comparison.to_csv(args.output / "MonthlyComparison.csv", index=False)

    previous_summary = json.loads(
        (args.previous / "Summary.json").read_text(encoding="utf-8")
    )
    final_summary = json.loads(
        (args.final / "Summary.json").read_text(encoding="utf-8")
    )
    overall = pd.DataFrame(
        [
            {
                "Period": "2026-04-01 to 2026-07-24",
                "PreviousTrades": previous_summary["Trades"],
                "FinalTrades": final_summary["Trades"],
                "TradesDelta": (
                    final_summary["Trades"] - previous_summary["Trades"]
                ),
                "PreviousWinRate": (
                    previous_summary["Winners"] / previous_summary["Trades"]
                ),
                "FinalWinRate": (
                    final_summary["Winners"] / final_summary["Trades"]
                ),
                "WinRateDeltaPctPoints": (
                    final_summary["Winners"] / final_summary["Trades"]
                    - previous_summary["Winners"] / previous_summary["Trades"]
                ),
                "PreviousNetPnL": previous_summary["NetPnL"],
                "FinalNetPnL": final_summary["NetPnL"],
                "NetPnLDelta": (
                    final_summary["NetPnL"] - previous_summary["NetPnL"]
                ),
                "PreviousAveragePnL": previous_summary["AveragePnL"],
                "FinalAveragePnL": final_summary["AveragePnL"],
                "PreviousAverageR": previous_summary["AverageR"],
                "FinalAverageR": final_summary["AverageR"],
                "AverageRDelta": (
                    final_summary["AverageR"] - previous_summary["AverageR"]
                ),
                "FinalTP1Trades": final_summary["TP1Trades"],
                "FinalTP2Trades": final_summary["TP2Trades"],
            }
        ]
    )
    overall[
        [
            "PreviousWinRate",
            "FinalWinRate",
            "WinRateDeltaPctPoints",
            "PreviousAverageR",
            "FinalAverageR",
            "AverageRDelta",
        ]
    ] = overall[
        [
            "PreviousWinRate",
            "FinalWinRate",
            "WinRateDeltaPctPoints",
            "PreviousAverageR",
            "FinalAverageR",
            "AverageRDelta",
        ]
    ].round(4)
    overall[
        [
            "PreviousNetPnL",
            "FinalNetPnL",
            "NetPnLDelta",
            "PreviousAveragePnL",
            "FinalAveragePnL",
        ]
    ] = overall[
        [
            "PreviousNetPnL",
            "FinalNetPnL",
            "NetPnLDelta",
            "PreviousAveragePnL",
            "FinalAveragePnL",
        ]
    ].round(2)
    overall.to_csv(args.output / "OverallComparison.csv", index=False)


if __name__ == "__main__":
    main()
