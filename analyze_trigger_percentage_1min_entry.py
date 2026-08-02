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
    parser = argparse.ArgumentParser(
        description="Compare the large-green B1 one-minute route with V2."
    )
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--like-for-like", type=Path, required=True)
    parser.add_argument("--v2", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def records(frame: pd.DataFrame) -> list[dict]:
    cleaned = frame.drop(columns=["EntryTimeParsed"], errors="ignore").copy()
    cleaned = cleaned.astype(object).where(pd.notna(cleaned), None)
    return cleaned.to_dict(orient="records")


def apply_b1_confirmation(frame: pd.DataFrame, folder: Path) -> pd.DataFrame:
    result = frame.copy()
    audit = pd.read_csv(folder / "SetupAudit.csv")
    confirmed = set(
        map(
            tuple,
            audit.loc[
                audit["EventType"] == "B1_CLOSE_CONFIRMED", ["Date", "Symbol"]
            ].astype(str).values,
        )
    )
    b1_mask = result["EntryMode"] == "B1_HIGH_BREAK"
    result.loc[b1_mask, "B1CloseConfirmed"] = [
        (str(row.Date), str(row.Symbol)) in confirmed
        for row in result.loc[b1_mask].itertuples(index=False)
    ]
    return result


def multi_equity(strategies: list[tuple[str, pd.DataFrame]]) -> pd.DataFrame:
    dates = sorted(
        set().union(*(set(frame["Date"].astype(str)) for _, frame in strategies))
    )
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
            column = f"{label}{suffix}"
            result[column] = result[column].fillna(0)
        result[f"{label}CumulativePnL"] = result[f"{label}PnL"].cumsum()
        result[f"{label}CumulativeNetRR"] = result[f"{label}NetRR"].cumsum()
    return result


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    full = apply_b1_confirmation(
        load_trades(args.new, "B1 1m entry - full current data"), args.new
    )
    like = apply_b1_confirmation(
        load_trades(args.like_for_like, "B1 1m entry - like-for-like"),
        args.like_for_like,
    )
    v2 = load_trades(args.v2, "Trigger Percentage V2")
    current = load_trades(args.current, "Current main Normal/Silver G1")
    prior = load_trades(args.prior, "Prior Normal/Silver G1")

    b1_full = full[full["EntryMode"] == "B1_HIGH_BREAK"].copy()
    standard_full = full[full["EntryMode"] == "G1_HIGH_BREAK"].copy()
    b1_like = like[like["EntryMode"] == "B1_HIGH_BREAK"].copy()
    standard_like = like[like["EntryMode"] == "G1_HIGH_BREAK"].copy()
    c1_v2 = v2[v2["EntryMode"] == "C1_HIGH_BREAK"].copy()

    frames = [
        ("Prior Normal/Silver G1", prior),
        ("Current main Normal/Silver G1", current),
        ("Trigger Percentage V2", v2),
        ("B1 1m entry - like-for-like", like),
        ("B1 1m entry - full current data", full),
        ("New like-for-like - standard G1 route", standard_like),
        ("New like-for-like - special B1 route", b1_like),
        ("New full - standard G1 route", standard_full),
        ("New full - special B1 route", b1_full),
        ("V2 - special C1 route", c1_v2),
    ]
    overall = pd.DataFrame([metric_row(frame, label) for label, frame in frames])
    v2_row = overall.loc[overall["Strategy"] == "Trigger Percentage V2"].iloc[0]
    for metric in [
        "Trades", "Winners", "Losers", "WinRate", "TP1Trades", "TP1Rate",
        "InitialSLTrades", "NetPnL", "NetRR", "AverageRR", "ProfitFactor",
        "MaxDrawdown", "MaxLosingStreak", "PositiveDayRate",
    ]:
        overall[f"ChangeVsV2_{metric}"] = overall[metric] - v2_row[metric]

    monthly = pd.concat(
        [grouped_metrics(frame, "Month", label) for label, frame in frames[:5]],
        ignore_index=True,
    )
    monthly_compare = side_by_side(v2, like, "Month")
    daily_compare = side_by_side(v2, like, "Date")
    stock_compare = side_by_side(v2, like, "Symbol")
    mode = grouped_metrics(full, "EntryMode", "B1 1m entry - full current data")
    tier = grouped_metrics(full, "EntryTier", "B1 1m entry - full current data")
    route_monthly_rows = []
    for (month, mode_name), group in full.groupby(["Month", "EntryMode"]):
        row = metric_row(group, "B1 1m entry - full current data")
        row["Month"] = month
        row["EntryMode"] = mode_name
        route_monthly_rows.append(row)
    route_monthly = pd.DataFrame(route_monthly_rows).sort_values(
        ["Month", "EntryMode"]
    )

    b1_diag = b1_full.copy()
    b1_diag["LargeGreenRangeBucket"] = pd.cut(
        b1_diag["LargeGreenRangePercent"],
        bins=[0.6, 0.75, 1.0, 1.5, float("inf")],
        labels=["0.60%-0.75%", "0.75%-1.00%", "1.00%-1.50%", ">1.50%"],
        right=True,
        include_lowest=False,
    )
    b1_diag["B1Position"] = (
        (
            pd.to_datetime(b1_diag["B1Time"], utc=True)
            - pd.to_datetime(b1_diag["TriggerTime"], utc=True)
            - pd.Timedelta(minutes=3)
        ).dt.total_seconds()
        / 60
        + 1
    ).round().astype("Int64")
    b1_diag["ConfirmationStatus"] = b1_diag["B1CloseConfirmed"].map(
        {True: "CONFIRMED", False: "NOT_CONFIRMED"}
    )
    b1_range = grouped_metrics(
        b1_diag.dropna(subset=["LargeGreenRangeBucket"]),
        "LargeGreenRangeBucket",
        "Special B1 route",
    )
    b1_position = grouped_metrics(
        b1_diag.dropna(subset=["B1Position"]), "B1Position", "Special B1 route"
    )
    confirmation = grouped_metrics(
        b1_diag, "ConfirmationStatus", "Special B1 route"
    )
    exit_reason = grouped_metrics(b1_diag, "ExitReason", "Special B1 route")

    equity_like = multi_equity(
        [("Prior", prior), ("Current", current), ("V2", v2), ("New", like)]
    )
    full_equity = multi_equity([("NewFull", full)])
    reconciliation = trade_reconciliation(v2, like)

    new_audit_dates = set(pd.read_csv(args.new / "SetupAudit.csv")["Date"].astype(str))
    v2_audit_dates = set(pd.read_csv(args.v2 / "SetupAudit.csv")["Date"].astype(str))
    missing_dates = sorted(new_audit_dates - v2_audit_dates)
    coverage_rows = []
    for trading_date in missing_dates:
        group = full[full["Date"] == trading_date]
        coverage_rows.append(
            {
                "Date": trading_date,
                "Trades": len(group),
                "Winners": int((group["GrossPnL"] > 0).sum()),
                "Losers": int((group["GrossPnL"] < 0).sum()),
                "NetPnL": float(group["GrossPnL"].sum()),
                "NetRR": float(group["NetRR"].sum()),
                "Reason": "Raw candles were unavailable in previous reports",
            }
        )
    coverage_difference = pd.DataFrame(coverage_rows)

    outputs = {
        "OverallComparison.csv": overall,
        "MonthlyPerformance.csv": monthly,
        "MonthlyComparisonVsV2.csv": monthly_compare,
        "DailyComparisonVsV2.csv": daily_compare,
        "StockComparisonVsV2.csv": stock_compare,
        "EntryModePerformance.csv": mode,
        "EntryTierPerformance.csv": tier,
        "RouteMonthlyPerformance.csv": route_monthly,
        "B1RangePerformance.csv": b1_range,
        "B1PositionPerformance.csv": b1_position,
        "B1ConfirmationPerformance.csv": confirmation,
        "B1ExitReasonPerformance.csv": exit_reason,
        "LikeForLikeEquityCurve.csv": equity_like,
        "FullDataEquityCurve.csv": full_equity,
        "TradeReconciliationVsV2.csv": reconciliation,
        "DataCoverageDifference.csv": coverage_difference,
        "SpecialRouteComparison.csv": overall[
            overall["Strategy"].isin(
                ["V2 - special C1 route", "New like-for-like - special B1 route"]
            )
        ],
    }
    for filename, frame in outputs.items():
        frame.to_csv(args.output / filename, index=False)

    b1_full.drop(columns=["EntryTimeParsed", "Strategy"]).to_csv(
        args.output / "B1Trades.csv", index=False
    )
    full.drop(columns=["EntryTimeParsed", "Strategy"]).to_csv(
        args.output / "Trades.csv", index=False
    )
    standard_full.drop(columns=["EntryTimeParsed", "Strategy"]).to_csv(
        args.output / "StandardG1Trades.csv", index=False
    )
    like.drop(columns=["EntryTimeParsed", "Strategy"]).to_csv(
        args.output / "LikeForLikeTrades.csv", index=False
    )
    reconciliation[reconciliation["MatchStatus"] == "BASELINE_ONLY"].to_csv(
        args.output / "V2TradesRemovedOrRerouted.csv", index=False
    )
    reconciliation[reconciliation["MatchStatus"] == "NEW_ONLY"].to_csv(
        args.output / "NewB1TradesAdded.csv", index=False
    )

    summary = {
        "Period": "2026-04-01 through 2026-07-31",
        "FullData": overall.loc[
            overall["Strategy"] == "B1 1m entry - full current data"
        ].iloc[0].to_dict(),
        "LikeForLike": overall.loc[
            overall["Strategy"] == "B1 1m entry - like-for-like"
        ].iloc[0].to_dict(),
        "V2": v2_row.to_dict(),
        "CurrentMain": overall.loc[
            overall["Strategy"] == "Current main Normal/Silver G1"
        ].iloc[0].to_dict(),
        "PriorNormalSilver": overall.loc[
            overall["Strategy"] == "Prior Normal/Silver G1"
        ].iloc[0].to_dict(),
        "SpecialB1RouteFull": overall.loc[
            overall["Strategy"] == "New full - special B1 route"
        ].iloc[0].to_dict(),
        "SpecialB1RouteLikeForLike": overall.loc[
            overall["Strategy"] == "New like-for-like - special B1 route"
        ].iloc[0].to_dict(),
        "SpecialC1RouteV2": overall.loc[
            overall["Strategy"] == "V2 - special C1 route"
        ].iloc[0].to_dict(),
        "MissingDatesInPreviousReports": missing_dates,
        "CostsIncluded": False,
    }
    (args.output / "ComparisonSummary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    event_summary = pd.read_csv(args.new / "EventSummary.csv")
    rules = pd.read_csv(args.new / "Rules.csv")
    workbook_data = {
        "overall": records(overall),
        "monthly": records(monthly),
        "routeMonthly": records(route_monthly),
        "entryMode": records(mode),
        "b1Range": records(b1_range),
        "b1Position": records(b1_position),
        "confirmation": records(confirmation),
        "exitReason": records(exit_reason),
        "equity": records(equity_like),
        "fullEquity": records(full_equity),
        "reconciliation": records(reconciliation),
        "trades": records(full),
        "b1Trades": records(b1_full),
        "eventSummary": records(event_summary),
        "rules": records(rules),
        "coverageDifference": records(coverage_difference),
        "summary": summary,
    }
    (args.output / "WorkbookData.json").write_text(
        json.dumps(workbook_data, default=str), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
