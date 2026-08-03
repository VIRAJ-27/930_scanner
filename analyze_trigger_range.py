from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from analyze_trigger_percentage_v2 import (
    grouped_metrics,
    load_trades,
    metric_row,
    side_by_side,
    trade_reconciliation,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Trigger Range strategy results.")
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--b1", type=Path, required=True)
    parser.add_argument("--b1-full", type=Path, required=True)
    parser.add_argument("--v2", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def records(frame: pd.DataFrame) -> list[dict]:
    clean = frame.drop(columns=["EntryTimeParsed"], errors="ignore").copy()
    clean = clean.astype(object).where(pd.notna(clean), None)
    return clean.to_dict(orient="records")


def multi_equity(strategies: list[tuple[str, pd.DataFrame]]) -> pd.DataFrame:
    dates = sorted(set().union(*(set(frame["Date"].astype(str)) for _, frame in strategies)))
    result = pd.DataFrame({"Date": dates})
    for label, frame in strategies:
        daily = frame.groupby("Date", as_index=False).agg(
            **{
                f"{label}Trades": ("Symbol", "size"),
                f"{label}PnL": ("GrossPnL", "sum"),
                f"{label}NetRR": ("NetRR", "sum"),
            }
        )
        result = result.merge(daily, on="Date", how="left")
        for suffix in ["Trades", "PnL", "NetRR"]:
            result[f"{label}{suffix}"] = result[f"{label}{suffix}"].fillna(0)
        result[f"{label}CumulativePnL"] = result[f"{label}PnL"].cumsum()
        result[f"{label}CumulativeNetRR"] = result[f"{label}NetRR"].cumsum()
    return result


def add_change_columns(overall: pd.DataFrame, baseline_name: str, prefix: str) -> None:
    baseline = overall.loc[overall["Strategy"] == baseline_name].iloc[0]
    for metric in [
        "Trades", "Winners", "Losers", "WinRate", "TP1Trades", "TP1Rate",
        "InitialSLTrades", "NetPnL", "NetRR", "AverageRR", "ProfitFactor",
        "MaxDrawdown", "MaxLosingStreak", "PositiveDayRate",
    ]:
        overall[f"{prefix}_{metric}"] = overall[metric] - baseline[metric]


def range_band(frame: pd.DataFrame) -> pd.Series:
    values = pd.to_numeric(frame["ReferenceRangePercent"], errors="coerce")
    result = pd.Series("", index=frame.index, dtype=object)
    b1 = frame["EntryMode"] == "B1_HIGH_BREAK"
    result.loc[b1 & (values < 0.10)] = "B1 <0.10% / 3R"
    result.loc[b1 & (values >= 0.10) & (values <= 0.35)] = "B1 0.10%-0.35% / 2R"
    result.loc[b1 & (values > 0.35)] = "B1 >0.35% / 1.2R"
    g1 = frame["EntryMode"] == "G1_HIGH_BREAK"
    result.loc[g1 & (values < 0.08)] = "G1 <0.08% / 5R"
    result.loc[g1 & (values >= 0.08) & (values <= 0.30)] = "G1 0.08%-0.30% / 1.3R"
    result.loc[g1 & (values > 0.30)] = "G1 >0.30% / 1.5R"
    return result


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    new = load_trades(args.new, "Trigger Range")
    b1 = load_trades(args.b1, "Previous B1 1m - comparable coverage")
    b1_full = load_trades(args.b1_full, "Previous B1 1m - prior full report")
    v2 = load_trades(args.v2, "Trigger Percentage V2")
    current = load_trades(args.current, "Current main Normal/Silver")
    prior = load_trades(args.prior, "Prior Normal/Silver")

    new["RangeTargetBand"] = range_band(new)
    new_b1 = new[new["EntryMode"] == "B1_HIGH_BREAK"].copy()
    new_standard = new[new["EntryMode"] == "G1_HIGH_BREAK"].copy()

    strategies = [
        ("Prior Normal/Silver", prior),
        ("Current main Normal/Silver", current),
        ("Trigger Percentage V2", v2),
        ("Previous B1 1m - comparable coverage", b1),
        ("Trigger Range", new),
        ("Trigger Range - standard G1", new_standard),
        ("Trigger Range - B1", new_b1),
    ]
    overall = pd.DataFrame([metric_row(frame, label) for label, frame in strategies])
    add_change_columns(overall, "Previous B1 1m - comparable coverage", "ChangeVsB1")
    add_change_columns(overall, "Current main Normal/Silver", "ChangeVsMain")

    monthly = pd.concat(
        [grouped_metrics(frame, "Month", label) for label, frame in strategies[:5]],
        ignore_index=True,
    )
    monthly_vs_b1 = side_by_side(b1, new, "Month")
    daily_vs_b1 = side_by_side(b1, new, "Date")
    stock_vs_b1 = side_by_side(b1, new, "Symbol")
    reconciliation = trade_reconciliation(b1, new)
    equity = multi_equity(
        [("Prior", prior), ("Main", current), ("V2", v2), ("B1", b1), ("New", new)]
    )
    entry_mode = grouped_metrics(new, "EntryMode", "Trigger Range")
    entry_tier = grouped_metrics(new, "EntryTier", "Trigger Range")
    target_band = grouped_metrics(new, "RangeTargetBand", "Trigger Range")
    target_driver = grouped_metrics(new, "TargetDriver", "Trigger Range")

    trigger = new.copy()
    trigger["TriggerRangeBand"] = pd.cut(
        trigger["TriggerRangePercent"],
        bins=[-float("inf"), 0.20, 0.50, 0.60],
        labels=["<0.20% / EP x2", "0.20%-<0.50% / EP x1.4", "0.50%-0.60% / +0.10pp"],
        right=True,
        include_lowest=True,
    )
    trigger_range = grouped_metrics(trigger, "TriggerRangeBand", "Trigger Range")

    route_monthly_rows = []
    for (month, mode), group in new.groupby(["Month", "EntryMode"]):
        row = metric_row(group, "Trigger Range")
        row["Month"] = month
        row["EntryMode"] = mode
        route_monthly_rows.append(row)
    route_monthly = pd.DataFrame(route_monthly_rows).sort_values(["Month", "EntryMode"])

    audit = pd.read_csv(args.new / "SetupAudit.csv")
    rejection_summary = (
        audit.assign(Outcome=audit["Outcome"].fillna(""))
        .groupby(["EventType", "Outcome"], dropna=False)
        .size()
        .reset_index(name="Count")
        .sort_values(["EventType", "Outcome"])
    )
    reason_counts: dict[str, int] = {}
    for raw in audit.loc[
        audit["EventType"] == "TRIGGER_REJECTED_FIRST_RED", "Details"
    ].dropna():
        try:
            for reason in json.loads(raw).get("reasons", []):
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    trigger_rejection_reasons = pd.DataFrame(
        [
            {"Reason": reason, "Count": count}
            for reason, count in sorted(
                reason_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ]
    )
    coverage = pd.DataFrame(
        [
            {
                "Dataset": "Current raw candle files / Trigger Range",
                "Trades": len(new),
                "ExcludedDates": "2026-04-28, 2026-05-26, 2026-06-23",
                "Note": "No raw candles for these dates exist in the currently available 210-stock source files.",
            },
            {
                "Dataset": "Previous B1 comparable report",
                "Trades": len(b1),
                "ExcludedDates": "2026-04-28, 2026-05-26, 2026-06-23",
                "Note": "Used as the primary B1 comparison because it matches currently available coverage.",
            },
            {
                "Dataset": "Previous B1 prior full report",
                "Trades": len(b1_full),
                "ExcludedDates": "None in prior exported report",
                "Note": "Shown for reference only; its extra-date raw candle files are no longer available locally.",
            },
        ]
    )

    outputs = {
        "OverallComparison.csv": overall,
        "MonthlyPerformance.csv": monthly,
        "MonthlyComparisonVsB1.csv": monthly_vs_b1,
        "DailyComparisonVsB1.csv": daily_vs_b1,
        "StockComparisonVsB1.csv": stock_vs_b1,
        "TradeReconciliationVsB1.csv": reconciliation,
        "EquityCurveComparison.csv": equity,
        "EntryModePerformance.csv": entry_mode,
        "EntryTierPerformance.csv": entry_tier,
        "TargetBandPerformance.csv": target_band,
        "TargetDriverPerformance.csv": target_driver,
        "TriggerRangePerformance.csv": trigger_range,
        "RouteMonthlyPerformance.csv": route_monthly,
        "RejectionSummary.csv": rejection_summary,
        "TriggerRejectionReasons.csv": trigger_rejection_reasons,
        "DataCoverage.csv": coverage,
    }
    for filename, frame in outputs.items():
        frame.to_csv(args.output / filename, index=False)

    new.drop(columns=["EntryTimeParsed", "Strategy"], errors="ignore").to_csv(
        args.output / "Trades.csv", index=False
    )
    new_b1.drop(columns=["EntryTimeParsed", "Strategy"], errors="ignore").to_csv(
        args.output / "B1Trades.csv", index=False
    )
    new_standard.drop(columns=["EntryTimeParsed", "Strategy"], errors="ignore").to_csv(
        args.output / "StandardG1Trades.csv", index=False
    )
    reconciliation[reconciliation["MatchStatus"] == "BASELINE_ONLY"].to_csv(
        args.output / "PreviousB1TradesRemovedOrRerouted.csv", index=False
    )
    reconciliation[reconciliation["MatchStatus"] == "NEW_ONLY"].to_csv(
        args.output / "TriggerRangeNewTrades.csv", index=False
    )

    summary = {
        "Period": "2026-04-01 through 2026-07-31",
        "PrimaryComparisonCoverage": "Currently available raw-data dates",
        "TriggerRange": overall.loc[overall["Strategy"] == "Trigger Range"].iloc[0].to_dict(),
        "PreviousB1Comparable": overall.loc[
            overall["Strategy"] == "Previous B1 1m - comparable coverage"
        ].iloc[0].to_dict(),
        "PreviousB1PriorFullReport": metric_row(
            b1_full, "Previous B1 1m - prior full report"
        ),
        "TriggerPercentageV2": overall.loc[
            overall["Strategy"] == "Trigger Percentage V2"
        ].iloc[0].to_dict(),
        "CurrentMain": overall.loc[
            overall["Strategy"] == "Current main Normal/Silver"
        ].iloc[0].to_dict(),
        "PriorNormalSilver": overall.loc[
            overall["Strategy"] == "Prior Normal/Silver"
        ].iloc[0].to_dict(),
        "TargetDriverCounts": new["TargetDriver"].value_counts().to_dict(),
        "ReconciliationVsB1": {
            str(key): int(value)
            for key, value in reconciliation["MatchStatus"].value_counts().items()
        },
        "UnavailableRawDates": ["2026-04-28", "2026-05-26", "2026-06-23"],
        "CostsIncluded": False,
    }
    (args.output / "ComparisonSummary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    rules = pd.read_csv(args.new / "Rules.csv")
    workbook_data = {
        "overall": records(overall),
        "monthly": records(monthly),
        "monthlyVsB1": records(monthly_vs_b1),
        "routeMonthly": records(route_monthly),
        "entryMode": records(entry_mode),
        "entryTier": records(entry_tier),
        "targetBand": records(target_band),
        "targetDriver": records(target_driver),
        "triggerRange": records(trigger_range),
        "equity": records(equity),
        "reconciliation": records(reconciliation),
        "trades": records(new),
        "b1Trades": records(new_b1),
        "rejectionSummary": records(rejection_summary),
        "triggerRejectionReasons": records(trigger_rejection_reasons),
        "coverage": records(coverage),
        "rules": records(rules),
        "summary": summary,
    }
    (args.output / "WorkbookData.json").write_text(
        json.dumps(workbook_data, default=str), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
