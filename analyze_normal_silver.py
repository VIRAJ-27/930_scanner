from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare Normal/Silver tiers with Trigger Percentage baseline."
    )
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--previous-through-jul24", type=Path, required=True)
    parser.add_argument("--previous-jul25-31", type=Path, required=True)
    return parser.parse_args()


def load_trades(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
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


def main() -> None:
    args = parse_args()
    new = load_trades(args.new / "Trades.csv")
    previous_early = load_trades(args.previous_through_jul24)
    previous_late = load_trades(args.previous_jul25_31)
    previous = (
        pd.concat([previous_early, previous_late], ignore_index=True)
        .drop_duplicates(["Date", "Symbol", "EntryTime"], keep="last")
        .sort_values(["EntryTimeParsed", "Symbol"])
        .reset_index(drop=True)
    )

    silver = new[new["EntryTier"] == "SILVER"].copy()
    normal = new[new["EntryTier"] == "NORMAL"].copy()
    comparison = pd.DataFrame(
        [
            metrics("Previous Trigger Percentage", previous),
            metrics("Normal + Silver Combined", new),
            metrics("Normal", normal),
            metrics("Silver", silver),
        ]
    )
    baseline = comparison.iloc[0]
    for column in ["Trades", "WinRate", "TP1Rate", "InitialSLTrades", "NetPnL", "NetRR", "AverageRR", "MaxDrawdown", "MaxLosingStreak"]:
        comparison[f"ChangeVsPrevious_{column}"] = comparison[column] - baseline[column]
    comparison.to_csv(args.new / "OverallComparison.csv", index=False)

    monthly_rows = []
    for name, frame in [
        ("Previous Trigger Percentage", previous),
        ("Normal + Silver Combined", new),
        ("Normal", normal),
        ("Silver", silver),
    ]:
        monthly_rows.extend(monthly(name, frame))
    pd.DataFrame(monthly_rows).to_csv(args.new / "MonthlyComparison.csv", index=False)

    previous_daily = previous.groupby("Date", as_index=False).agg(
        PreviousTrades=("Symbol", "count"),
        PreviousPnL=("GrossPnL", "sum"),
        PreviousNetRR=("NetRR", "sum"),
        PreviousInitialSL=("ExitReason", lambda values: int((values == "INITIAL_SL").sum())),
    )
    new_daily = new.groupby("Date", as_index=False).agg(
        CombinedTrades=("Symbol", "count"),
        CombinedPnL=("GrossPnL", "sum"),
        CombinedNetRR=("NetRR", "sum"),
        CombinedInitialSL=("ExitReason", lambda values: int((values == "INITIAL_SL").sum())),
        NormalTrades=("EntryTier", lambda values: int((values == "NORMAL").sum())),
        SilverTrades=("EntryTier", lambda values: int((values == "SILVER").sum())),
    )
    daily = previous_daily.merge(new_daily, on="Date", how="outer").fillna(0)
    daily = daily.sort_values("Date").reset_index(drop=True)
    daily["PnLChange"] = daily["CombinedPnL"] - daily["PreviousPnL"]
    daily["NetRRChange"] = daily["CombinedNetRR"] - daily["PreviousNetRR"]
    daily["InitialSLAvoided"] = daily["PreviousInitialSL"] - daily["CombinedInitialSL"]
    daily["PreviousCumulativePnL"] = daily["PreviousPnL"].cumsum()
    daily["CombinedCumulativePnL"] = daily["CombinedPnL"].cumsum()
    daily["PreviousCumulativeNetRR"] = daily["PreviousNetRR"].cumsum()
    daily["CombinedCumulativeNetRR"] = daily["CombinedNetRR"].cumsum()
    daily.to_csv(args.new / "DailyComparison.csv", index=False)
    daily[[
        "Date",
        "PreviousCumulativePnL",
        "CombinedCumulativePnL",
        "PreviousCumulativeNetRR",
        "CombinedCumulativeNetRR",
    ]].to_csv(args.new / "EquityCurveComparison.csv", index=False)

    tier_summary = pd.DataFrame(
        [metrics("Normal", normal), metrics("Silver", silver)]
    )
    tier_summary.to_csv(args.new / "EntryTierPerformance.csv", index=False)

    normal_pass = new["EMA3mRisePercent"] >= 0.01
    silver_pass = new["EntryTier"] == "SILVER"
    overlap_groups = [
        ("Normal criterion standalone", new[normal_pass]),
        ("Silver criterion standalone", new[silver_pass]),
        ("Both criteria", new[normal_pass & silver_pass]),
        ("Normal only", new[normal_pass & ~silver_pass]),
        ("Silver only", new[~normal_pass & silver_pass]),
    ]
    overlap = pd.DataFrame([metrics(name, group) for name, group in overlap_groups])
    overlap.to_csv(args.new / "FilterOverlapPerformance.csv", index=False)

    stress_days = daily[daily["Date"].isin(["2026-04-10", "2026-04-13"])].copy()
    stress_days.to_csv(args.new / "StressDayComparison.csv", index=False)

    export_previous = previous.drop(columns=["EntryTimeParsed"])
    export_previous.to_csv(args.new / "PreviousVersionTrades.csv", index=False)

    audit_path = args.new / "SetupAudit.csv"
    if audit_path.exists():
        audit = pd.read_csv(audit_path)
        failed = audit[
            (audit["EventType"] == "SETUP_FAILED")
            & (audit["Outcome"] == "ENTRY_QUALITY_FILTERS_FAILED")
        ].copy()
        failed.to_csv(args.new / "QualityFilterRejectedSetups.csv", index=False)

    summary = {
        "Period": "2026-04-01 through 2026-07-31",
        "Rules": {
            "Silver": "1m EMA20 rise >= 0.116% over five completed candles and G1 body >= 57.9% of range",
            "Normal": "If not Silver, 3m EMA20 rise >= 0.01% over two completed candles",
            "Precedence": "Silver when both pass; discard when neither passes",
        },
        "Previous": metrics("Previous Trigger Percentage", previous),
        "Combined": metrics("Normal + Silver Combined", new),
        "Normal": metrics("Normal", normal),
        "Silver": metrics("Silver", silver),
        "CostsIncluded": False,
    }
    (args.new / "ComparisonSummary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    workbook_payload = {
        "overall": comparison.astype(object).where(pd.notna(comparison), None).to_dict("records"),
        "monthly": pd.DataFrame(monthly_rows).astype(object).where(pd.notna(pd.DataFrame(monthly_rows)), None).to_dict("records"),
        "daily": daily.astype(object).where(pd.notna(daily), None).to_dict("records"),
        "tiers": tier_summary.astype(object).where(pd.notna(tier_summary), None).to_dict("records"),
        "overlap": overlap.astype(object).where(pd.notna(overlap), None).to_dict("records"),
        "stress_days": stress_days.astype(object).where(pd.notna(stress_days), None).to_dict("records"),
        "trades": new.drop(columns=["EntryTimeParsed"]).astype(object).where(pd.notna(new.drop(columns=["EntryTimeParsed"])), None).to_dict("records"),
        "previous_trades": export_previous.astype(object).where(pd.notna(export_previous), None).to_dict("records"),
        "stock_summary": pd.read_csv(args.new / "StockSummary.csv").astype(object).where(
            pd.notna(pd.read_csv(args.new / "StockSummary.csv")), None
        ).to_dict("records"),
        "quality_rejections": (
            failed.astype(object).where(pd.notna(failed), None).to_dict("records")
            if audit_path.exists()
            else []
        ),
        "summary": summary,
    }
    (args.new / "WorkbookData.json").write_text(
        json.dumps(workbook_payload, default=str),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
