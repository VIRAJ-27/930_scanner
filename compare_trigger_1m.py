from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare Trigger 1-Minute Entry with the previous version."
    )
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    return parser.parse_args()


def normalized_trades(path: Path, version: str) -> pd.DataFrame:
    candidates = [
        path / "Trades.csv",
        path / "trigger first entry.csv",
        path / "first red.csv",
    ]
    trade_path = next((candidate for candidate in candidates if candidate.exists()), None)
    if trade_path is None:
        raise FileNotFoundError(f"No trade CSV found in {path}")
    frame = pd.read_csv(trade_path)
    frame["Month"] = frame["Month"].astype(str)
    if "NetRR" not in frame:
        frame["NetRR"] = pd.to_numeric(frame["RMultiple"], errors="coerce")
    frame["Version"] = version
    return frame


def aggregate(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for month, group in frame.groupby("Month"):
        pnl = pd.to_numeric(group["GrossPnL"], errors="coerce").fillna(0)
        rr = pd.to_numeric(group["NetRR"], errors="coerce").fillna(0)
        rows.append(
            {
                "Month": month,
                "Trades": len(group),
                "Winners": int((pnl > 0).sum()),
                "WinRate": round(float((pnl > 0).mean()), 4),
                "NetPnL": round(float(pnl.sum()), 2),
                "NetRR": round(float(rr.sum()), 4),
                "AverageRR": round(float(rr.mean()), 4),
                "TP1Trades": int(
                    group["TP1ExitTime"].fillna("").astype(bool).sum()
                ),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    previous = normalized_trades(args.previous, "Previous")
    new = normalized_trades(args.new, "Trigger1m")
    old_monthly = aggregate(previous).add_prefix("Previous")
    old_monthly = old_monthly.rename(columns={"PreviousMonth": "Month"})
    new_monthly = aggregate(new).add_prefix("New")
    new_monthly = new_monthly.rename(columns={"NewMonth": "Month"})
    comparison = old_monthly.merge(new_monthly, on="Month", how="outer").fillna(0)
    comparison["TradesDelta"] = (
        comparison["NewTrades"] - comparison["PreviousTrades"]
    )
    comparison["WinRateDeltaPctPoints"] = (
        comparison["NewWinRate"] - comparison["PreviousWinRate"]
    )
    comparison["NetPnLDelta"] = (
        comparison["NewNetPnL"] - comparison["PreviousNetPnL"]
    )
    comparison["NetRRDelta"] = (
        comparison["NewNetRR"] - comparison["PreviousNetRR"]
    )
    comparison["AverageRRDelta"] = (
        comparison["NewAverageRR"] - comparison["PreviousAverageRR"]
    )
    comparison = comparison[
        [
            "Month",
            "PreviousTrades",
            "NewTrades",
            "TradesDelta",
            "PreviousWinRate",
            "NewWinRate",
            "WinRateDeltaPctPoints",
            "PreviousNetPnL",
            "NewNetPnL",
            "NetPnLDelta",
            "PreviousNetRR",
            "NewNetRR",
            "NetRRDelta",
            "PreviousAverageRR",
            "NewAverageRR",
            "AverageRRDelta",
            "PreviousTP1Trades",
            "NewTP1Trades",
        ]
    ].sort_values("Month")
    comparison.to_csv(args.new / "MonthlyComparison.csv", index=False)

    def overall(frame: pd.DataFrame) -> dict:
        pnl = pd.to_numeric(frame["GrossPnL"], errors="coerce").fillna(0)
        rr = pd.to_numeric(frame["NetRR"], errors="coerce").fillna(0)
        return {
            "Trades": len(frame),
            "WinRate": round(float((pnl > 0).mean()), 4),
            "NetPnL": round(float(pnl.sum()), 2),
            "NetRR": round(float(rr.sum()), 4),
            "AverageRR": round(float(rr.mean()), 4),
            "TP1Trades": int(frame["TP1ExitTime"].fillna("").astype(bool).sum()),
        }

    old = overall(previous)
    revised = overall(new)
    overall_row = {
        "Period": "All",
        **{f"Previous{key}": value for key, value in old.items()},
        **{f"New{key}": value for key, value in revised.items()},
        "TradesDelta": revised["Trades"] - old["Trades"],
        "WinRateDeltaPctPoints": revised["WinRate"] - old["WinRate"],
        "NetPnLDelta": revised["NetPnL"] - old["NetPnL"],
        "NetRRDelta": revised["NetRR"] - old["NetRR"],
        "AverageRRDelta": revised["AverageRR"] - old["AverageRR"],
    }
    pd.DataFrame([overall_row]).to_csv(
        args.new / "OverallComparison.csv", index=False
    )


if __name__ == "__main__":
    main()
