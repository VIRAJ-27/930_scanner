from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare next-candle-only G1 entries with Normal/Silver baseline."
    )
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True)
    return parser.parse_args()


def load_trades(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path / "Trades.csv")
    for column in ["GrossPnL", "NetRR", "RunnerPnL"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    frame["EntryTimeParsed"] = pd.to_datetime(frame["EntryTime"], utc=True)
    frame["Date"] = frame["Date"].astype(str)
    frame["Month"] = frame["Date"].str.slice(0, 7)
    return frame.sort_values(["EntryTimeParsed", "Symbol"]).reset_index(drop=True)


def max_drawdown(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    equity = frame["GrossPnL"].cumsum()
    peak = equity.cummax().clip(lower=0.0)
    return float((peak - equity).max())


def max_losing_streak(frame: pd.DataFrame) -> int:
    maximum = current = 0
    for value in frame["GrossPnL"]:
        if value < 0:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum


def metrics(name: str, frame: pd.DataFrame) -> dict:
    pnl = frame["GrossPnL"]
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = float(pnl[pnl < 0].sum())
    tp1 = frame["TP1ExitTime"].fillna("").astype(str).ne("")
    return {
        "Strategy": name,
        "Trades": len(frame),
        "Winners": int((pnl > 0).sum()),
        "Losers": int((pnl < 0).sum()),
        "WinRate": float((pnl > 0).mean()) if len(frame) else 0.0,
        "TP1Trades": int(tp1.sum()),
        "TP1Rate": float(tp1.mean()) if len(frame) else 0.0,
        "InitialSLTrades": int((frame["ExitReason"] == "INITIAL_SL").sum()),
        "NetPnL": float(pnl.sum()),
        "NetRR": float(frame["NetRR"].sum()),
        "AverageRR": float(frame["NetRR"].mean()) if len(frame) else 0.0,
        "ProfitFactor": gross_profit / abs(gross_loss) if gross_loss else None,
        "MaxDrawdown": max_drawdown(frame),
        "MaxLosingStreak": max_losing_streak(frame),
        "RunnerPnL": float(frame["RunnerPnL"].sum()),
    }


def monthly(strategy: str, frame: pd.DataFrame) -> list[dict]:
    return [
        {"Month": month, **metrics(strategy, group)}
        for month, group in frame.groupby("Month", sort=True)
    ]


def clean_records(frame: pd.DataFrame) -> list[dict]:
    clean = frame.drop(columns=["EntryTimeParsed"], errors="ignore")
    return clean.astype(object).where(pd.notna(clean), None).to_dict("records")


def main() -> None:
    args = parse_args()
    new = load_trades(args.new)
    previous = load_trades(args.previous)
    labels = [
        ("Previous Normal + Silver", previous),
        ("Next-candle-only Normal + Silver", new),
        ("New Normal", new[new["EntryTier"] == "NORMAL"]),
        ("New Silver", new[new["EntryTier"] == "SILVER"]),
    ]
    comparison = pd.DataFrame([metrics(name, frame) for name, frame in labels])
    baseline = comparison.iloc[0]
    for column in [
        "Trades", "WinRate", "TP1Rate", "InitialSLTrades", "NetPnL",
        "NetRR", "AverageRR", "ProfitFactor", "MaxDrawdown",
        "MaxLosingStreak", "RunnerPnL",
    ]:
        comparison[f"ChangeVsPrevious_{column}"] = comparison[column] - baseline[column]
    comparison.to_csv(args.new / "OverallComparison.csv", index=False)

    monthly_rows: list[dict] = []
    for name, frame in labels:
        monthly_rows.extend(monthly(name, frame))
    monthly_frame = pd.DataFrame(monthly_rows)
    monthly_frame.to_csv(args.new / "MonthlyComparison.csv", index=False)

    def daily_summary(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
        return frame.groupby("Date", as_index=False).agg(**{
            f"{prefix}Trades": ("Symbol", "count"),
            f"{prefix}PnL": ("GrossPnL", "sum"),
            f"{prefix}NetRR": ("NetRR", "sum"),
            f"{prefix}InitialSL": (
                "ExitReason", lambda values: int((values == "INITIAL_SL").sum())
            ),
        })

    daily = daily_summary(previous, "Previous").merge(
        daily_summary(new, "New"), on="Date", how="outer"
    ).fillna(0).sort_values("Date").reset_index(drop=True)
    daily["PnLChange"] = daily["NewPnL"] - daily["PreviousPnL"]
    daily["NetRRChange"] = daily["NewNetRR"] - daily["PreviousNetRR"]
    daily["InitialSLAvoided"] = daily["PreviousInitialSL"] - daily["NewInitialSL"]
    daily["PreviousCumulativePnL"] = daily["PreviousPnL"].cumsum()
    daily["NewCumulativePnL"] = daily["NewPnL"].cumsum()
    daily["PreviousCumulativeNetRR"] = daily["PreviousNetRR"].cumsum()
    daily["NewCumulativeNetRR"] = daily["NewNetRR"].cumsum()
    daily.to_csv(args.new / "DailyComparison.csv", index=False)
    daily[[
        "Date", "PreviousCumulativePnL", "NewCumulativePnL",
        "PreviousCumulativeNetRR", "NewCumulativeNetRR",
    ]].to_csv(args.new / "EquityCurveComparison.csv", index=False)

    delay = previous.groupby("EntryDelayAfterG1", as_index=False).agg(
        Trades=("Symbol", "count"),
        Winners=("GrossPnL", lambda values: int((values > 0).sum())),
        InitialSLTrades=("ExitReason", lambda values: int((values == "INITIAL_SL").sum())),
        NetPnL=("GrossPnL", "sum"),
        NetRR=("NetRR", "sum"),
        AverageRR=("NetRR", "mean"),
    )
    delay["WinRate"] = delay["Winners"] / delay["Trades"]
    delay.to_csv(args.new / "PreviousEntryDelayPerformance.csv", index=False)
    removed = previous[previous["EntryDelayAfterG1"] > 1].copy()
    removed.drop(columns=["EntryTimeParsed"]).to_csv(
        args.new / "RemovedLaterEntries.csv", index=False
    )
    removed_summary = pd.DataFrame([metrics("Removed delayed entries", removed)])
    removed_summary.to_csv(args.new / "RemovedLaterEntriesSummary.csv", index=False)

    tier_summary = pd.DataFrame([
        metrics("New Normal", new[new["EntryTier"] == "NORMAL"]),
        metrics("New Silver", new[new["EntryTier"] == "SILVER"]),
    ])
    tier_summary.to_csv(args.new / "EntryTierPerformance.csv", index=False)

    previous.drop(columns=["EntryTimeParsed"]).to_csv(
        args.new / "PreviousVersionTrades.csv", index=False
    )
    summary = {
        "Period": "2026-04-01 through 2026-07-31",
        "ChangedRule": (
            "After G1, only the immediately next 1-minute candle may break "
            "G1 high; otherwise discard the stock for the day."
        ),
        "Previous": metrics("Previous Normal + Silver", previous),
        "New": metrics("Next-candle-only Normal + Silver", new),
        "RemovedDelayedEntries": metrics("Removed delayed entries", removed),
        "CostsIncluded": False,
    }
    (args.new / "ComparisonSummary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    workbook_payload = {
        "summary": summary,
        "overall": clean_records(comparison),
        "monthly": clean_records(monthly_frame),
        "daily": clean_records(daily),
        "tiers": clean_records(tier_summary),
        "delays": clean_records(delay),
        "removed_summary": clean_records(removed_summary),
        "trades": clean_records(new),
        "removed_trades": clean_records(removed),
        "stock_summary": clean_records(pd.read_csv(args.new / "StockSummary.csv")),
    }
    (args.new / "WorkbookData.json").write_text(
        json.dumps(workbook_payload, default=str), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
