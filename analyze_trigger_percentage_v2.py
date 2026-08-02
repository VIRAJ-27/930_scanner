from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare large-green C1 results with Normal/Silver G1."
    )
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--prior", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def load_trades(folder: Path, strategy: str) -> pd.DataFrame:
    frame = pd.read_csv(folder / "Trades.csv")
    frame["Strategy"] = strategy
    frame["EntryTimeParsed"] = pd.to_datetime(frame["EntryTime"], utc=True)
    frame["Date"] = frame["Date"].astype(str)
    frame["Month"] = frame["Month"].astype(str)
    for column in ["GrossPnL", "NetRR", "TP1PnL", "RunnerPnL"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    return frame.sort_values(["EntryTimeParsed", "Symbol"]).reset_index(drop=True)


def max_losing_streak(frame: pd.DataFrame) -> tuple[int, str, str]:
    best_count = 0
    best_start = ""
    best_end = ""
    count = 0
    start = ""
    for row in frame.itertuples(index=False):
        if row.GrossPnL < 0:
            if count == 0:
                start = row.EntryTime
            count += 1
            if count > best_count:
                best_count = count
                best_start = start
                best_end = row.EntryTime
        else:
            count = 0
            start = ""
    return best_count, best_start, best_end


def metric_row(frame: pd.DataFrame, strategy: str) -> dict:
    pnl = frame["GrossPnL"]
    rr = frame["NetRR"]
    ordered = frame.sort_values(["EntryTimeParsed", "Symbol"])
    equity = ordered["GrossPnL"].cumsum()
    peaks = pd.concat([pd.Series([0.0]), equity], ignore_index=True).cummax().iloc[1:]
    drawdown = peaks.to_numpy() - equity.to_numpy()
    streak, streak_start, streak_end = max_losing_streak(ordered)
    daily = frame.groupby("Date", as_index=False).agg(NetPnL=("GrossPnL", "sum"))
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = float(pnl[pnl < 0].sum())
    return {
        "Strategy": strategy,
        "Trades": int(len(frame)),
        "Winners": int((pnl > 0).sum()),
        "Losers": int((pnl < 0).sum()),
        "WinRate": float((pnl > 0).mean()) if len(frame) else 0.0,
        "TP1Trades": int(frame["TP1ExitTime"].fillna("").astype(bool).sum()),
        "TP1Rate": float(frame["TP1ExitTime"].fillna("").astype(bool).mean()) if len(frame) else 0.0,
        "InitialSLTrades": int((frame["ExitReason"] == "INITIAL_SL").sum()),
        "NetPnL": float(pnl.sum()),
        "NetRR": float(rr.sum()),
        "AveragePnL": float(pnl.mean()) if len(frame) else 0.0,
        "AverageRR": float(rr.mean()) if len(frame) else 0.0,
        "ProfitFactor": gross_profit / abs(gross_loss) if gross_loss else None,
        "MaxDrawdown": float(drawdown.max()) if len(drawdown) else 0.0,
        "MaxLosingStreak": streak,
        "LosingStreakStart": streak_start,
        "LosingStreakEnd": streak_end,
        "PositiveDays": int((daily["NetPnL"] > 0).sum()),
        "NegativeDays": int((daily["NetPnL"] < 0).sum()),
        "PositiveDayRate": float((daily["NetPnL"] > 0).mean()) if len(daily) else 0.0,
        "BestTrade": float(pnl.max()) if len(frame) else 0.0,
        "WorstTrade": float(pnl.min()) if len(frame) else 0.0,
        "BestDay": float(daily["NetPnL"].max()) if len(daily) else 0.0,
        "WorstDay": float(daily["NetPnL"].min()) if len(daily) else 0.0,
    }


def grouped_metrics(frame: pd.DataFrame, key: str, strategy: str) -> pd.DataFrame:
    rows = []
    for value, group in frame.groupby(key, dropna=False):
        row = metric_row(group, strategy)
        row[key] = value
        rows.append(row)
    return pd.DataFrame(rows)


def side_by_side(
    baseline: pd.DataFrame,
    new: pd.DataFrame,
    key: str,
) -> pd.DataFrame:
    base = grouped_metrics(baseline, key, "Baseline").drop(columns="Strategy")
    updated = grouped_metrics(new, key, "New").drop(columns="Strategy")
    result = base.merge(updated, on=key, how="outer", suffixes=("_Baseline", "_New"))
    for metric in [
        "Trades",
        "Winners",
        "Losers",
        "WinRate",
        "TP1Rate",
        "InitialSLTrades",
        "NetPnL",
        "NetRR",
        "AverageRR",
        "ProfitFactor",
        "MaxDrawdown",
        "MaxLosingStreak",
    ]:
        old = f"{metric}_Baseline"
        new_col = f"{metric}_New"
        if old in result and new_col in result:
            result[f"{metric}_Change"] = result[new_col].fillna(0) - result[old].fillna(0)
    return result.sort_values(key).reset_index(drop=True)


def equity_curve(baseline: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    dates = sorted(set(baseline["Date"]) | set(new["Date"]))
    result = pd.DataFrame({"Date": dates})
    for label, frame in [("Baseline", baseline), ("New", new)]:
        daily = frame.groupby("Date", as_index=False).agg(
            **{
                f"{label}Trades": ("Symbol", "size"),
                f"{label}PnL": ("GrossPnL", "sum"),
                f"{label}NetRR": ("NetRR", "sum"),
            }
        )
        result = result.merge(daily, on="Date", how="left")
        for column in [f"{label}Trades", f"{label}PnL", f"{label}NetRR"]:
            result[column] = result[column].fillna(0)
        result[f"{label}CumulativePnL"] = result[f"{label}PnL"].cumsum()
        result[f"{label}CumulativeNetRR"] = result[f"{label}NetRR"].cumsum()
        result[f"{label}PnLPeak"] = result[f"{label}CumulativePnL"].cummax().clip(lower=0)
        result[f"{label}Drawdown"] = (
            result[f"{label}PnLPeak"] - result[f"{label}CumulativePnL"]
        )
    result["PnLChange"] = result["NewPnL"] - result["BaselinePnL"]
    result["CumulativePnLChange"] = (
        result["NewCumulativePnL"] - result["BaselineCumulativePnL"]
    )
    result["NetRRChange"] = result["NewNetRR"] - result["BaselineNetRR"]
    return result


def trade_reconciliation(baseline: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Date",
        "Symbol",
        "EntryTime",
        "EntryMode",
        "EntryTier",
        "EntryPrice",
        "InitialSL",
        "TP1Target",
        "ExitReason",
        "GrossPnL",
        "NetRR",
    ]
    base = baseline[columns].copy().add_prefix("Baseline_")
    base = base.rename(columns={"Baseline_Date": "Date", "Baseline_Symbol": "Symbol"})
    updated = new[columns].copy().add_prefix("New_")
    updated = updated.rename(columns={"New_Date": "Date", "New_Symbol": "Symbol"})
    result = base.merge(updated, on=["Date", "Symbol"], how="outer", indicator=True)
    result["MatchStatus"] = result["_merge"].map(
        {"left_only": "BASELINE_ONLY", "right_only": "NEW_ONLY", "both": "BOTH"}
    )
    result["PnLChange"] = result["New_GrossPnL"].fillna(0) - result["Baseline_GrossPnL"].fillna(0)
    result["NetRRChange"] = result["New_NetRR"].fillna(0) - result["Baseline_NetRR"].fillna(0)
    return result.drop(columns="_merge").sort_values(["Date", "Symbol"])


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    baseline = load_trades(args.baseline, "Normal/Silver G1 baseline")
    prior = (
        load_trades(args.prior, "Prior Normal/Silver G1")
        if args.prior is not None
        else None
    )
    updated = load_trades(args.new, "Large-green C1 upgrade")

    c1 = updated[updated["EntryMode"] == "C1_HIGH_BREAK"].copy()
    standard = updated[updated["EntryMode"] == "G1_HIGH_BREAK"].copy()
    overall_rows = []
    if prior is not None:
        overall_rows.append(metric_row(prior, "Prior Normal/Silver G1"))
    overall_rows.extend(
        [
            metric_row(baseline, "Current main Normal/Silver G1"),
            metric_row(updated, "Large-green C1 upgrade"),
            metric_row(standard, "New version - standard G1 route"),
            metric_row(c1, "New version - special C1 route"),
        ]
    )
    overall = pd.DataFrame(overall_rows)
    base_row = overall.loc[
        overall["Strategy"] == "Current main Normal/Silver G1"
    ].iloc[0]
    for metric in [
        "Trades",
        "Winners",
        "Losers",
        "WinRate",
        "TP1Trades",
        "TP1Rate",
        "InitialSLTrades",
        "NetPnL",
        "NetRR",
        "AverageRR",
        "ProfitFactor",
        "MaxDrawdown",
        "MaxLosingStreak",
        "PositiveDayRate",
    ]:
        overall[f"ChangeVsBaseline_{metric}"] = overall[metric] - base_row[metric]

    monthly = side_by_side(baseline, updated, "Month")
    daily = side_by_side(baseline, updated, "Date")
    stock = side_by_side(baseline, updated, "Symbol")
    mode = grouped_metrics(updated, "EntryMode", "Large-green C1 upgrade")
    tier = grouped_metrics(updated, "EntryTier", "Large-green C1 upgrade")
    route_monthly_rows = []
    for (month, entry_mode), group in updated.groupby(["Month", "EntryMode"]):
        row = metric_row(group, "Large-green C1 upgrade")
        row["Month"] = month
        row["EntryMode"] = entry_mode
        route_monthly_rows.append(row)
    route_monthly = pd.DataFrame(route_monthly_rows).sort_values(
        ["Month", "EntryMode"]
    )
    three_strategy_monthly = pd.concat(
        [
            grouped_metrics(prior, "Month", "Prior Normal/Silver G1")
            if prior is not None
            else pd.DataFrame(),
            grouped_metrics(baseline, "Month", "Current main Normal/Silver G1"),
            grouped_metrics(updated, "Month", "Large-green C1 upgrade"),
        ],
        ignore_index=True,
    )

    c1_diagnostics = c1.copy()
    c1_diagnostics["LargeGreenRangeBucket"] = pd.cut(
        c1_diagnostics["LargeGreenRangePercent"],
        bins=[0.6, 0.75, 1.0, 1.5, float("inf")],
        labels=["0.60%-0.75%", "0.75%-1.00%", "1.00%-1.50%", ">1.50%"],
        right=True,
        include_lowest=False,
    )
    c1_diagnostics["C1Candidate"] = (
        (
            pd.to_datetime(c1_diagnostics["C1Time"], utc=True)
            - pd.to_datetime(c1_diagnostics["TriggerTime"], utc=True)
            - pd.Timedelta(minutes=3)
        ).dt.total_seconds()
        / 180
        + 1
    ).round().astype("Int64").map({1: "FIRST", 2: "SECOND"})
    c1_range = grouped_metrics(
        c1_diagnostics.dropna(subset=["LargeGreenRangeBucket"]),
        "LargeGreenRangeBucket",
        "Special C1 route",
    )
    c1_candidate = grouped_metrics(
        c1_diagnostics.dropna(subset=["C1Candidate"]),
        "C1Candidate",
        "Special C1 route",
    )
    curve = equity_curve(baseline, updated)
    if prior is not None:
        prior_daily = prior.groupby("Date", as_index=False).agg(
            PriorTrades=("Symbol", "size"),
            PriorPnL=("GrossPnL", "sum"),
            PriorNetRR=("NetRR", "sum"),
        )
        curve = curve.merge(prior_daily, on="Date", how="left")
        for column in ["PriorTrades", "PriorPnL", "PriorNetRR"]:
            curve[column] = curve[column].fillna(0)
        curve["PriorCumulativePnL"] = curve["PriorPnL"].cumsum()
        curve["PriorCumulativeNetRR"] = curve["PriorNetRR"].cumsum()
    reconciliation = trade_reconciliation(baseline, updated)

    overall.to_csv(args.output / "OverallComparison.csv", index=False)
    monthly.to_csv(args.output / "MonthlyComparison.csv", index=False)
    daily.to_csv(args.output / "DailyComparison.csv", index=False)
    stock.to_csv(args.output / "StockComparison.csv", index=False)
    mode.to_csv(args.output / "EntryModePerformance.csv", index=False)
    tier.to_csv(args.output / "EntryTierPerformance.csv", index=False)
    route_monthly.to_csv(args.output / "RouteMonthlyPerformance.csv", index=False)
    three_strategy_monthly.to_csv(
        args.output / "ThreeStrategyMonthly.csv", index=False
    )
    c1_range.to_csv(args.output / "C1RangePerformance.csv", index=False)
    c1_candidate.to_csv(args.output / "C1CandidatePerformance.csv", index=False)
    curve.to_csv(args.output / "EquityCurveComparison.csv", index=False)
    reconciliation.to_csv(args.output / "TradeReconciliation.csv", index=False)
    reconciliation[reconciliation["MatchStatus"] == "BASELINE_ONLY"].to_csv(
        args.output / "BaselineTradesRemovedOrRerouted.csv", index=False
    )
    reconciliation[
        (reconciliation["MatchStatus"] == "BOTH")
        & (reconciliation["New_EntryMode"] == "C1_HIGH_BREAK")
    ].to_csv(args.output / "TradesReroutedToC1.csv", index=False)
    c1.drop(columns=["EntryTimeParsed", "Strategy"]).to_csv(
        args.output / "C1Trades.csv", index=False
    )
    standard.drop(columns=["EntryTimeParsed", "Strategy"]).to_csv(
        args.output / "StandardG1Trades.csv", index=False
    )

    summary = {
        "Period": "2026-04-01 through 2026-07-31",
        "Prior": (
            overall.loc[overall["Strategy"] == "Prior Normal/Silver G1"].iloc[0].to_dict()
            if prior is not None
            else None
        ),
        "Baseline": base_row.to_dict(),
        "New": overall.loc[overall["Strategy"] == "Large-green C1 upgrade"].iloc[0].to_dict(),
        "StandardG1Route": overall.loc[overall["Strategy"] == "New version - standard G1 route"].iloc[0].to_dict(),
        "SpecialC1Route": overall.loc[overall["Strategy"] == "New version - special C1 route"].iloc[0].to_dict(),
        "Reconciliation": {
            key: int(value)
            for key, value in reconciliation["MatchStatus"].value_counts().items()
        },
        "CostsIncluded": False,
    }
    (args.output / "ComparisonSummary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    event_summary_path = args.new / "EventSummary.csv"
    event_summary = (
        pd.read_csv(event_summary_path)
        if event_summary_path.exists()
        else pd.DataFrame()
    )
    rules_path = args.new / "Rules.csv"
    rules = pd.read_csv(rules_path) if rules_path.exists() else pd.DataFrame()

    def records(frame: pd.DataFrame) -> list[dict]:
        cleaned = frame.copy()
        cleaned = cleaned.drop(
            columns=["EntryTimeParsed"], errors="ignore"
        )
        cleaned = cleaned.astype(object).where(pd.notna(cleaned), None)
        return cleaned.to_dict(orient="records")

    workbook_data = {
        "overall": records(overall),
        "monthly": records(three_strategy_monthly),
        "routeMonthly": records(route_monthly),
        "entryMode": records(mode),
        "c1Range": records(c1_range),
        "c1Candidate": records(c1_candidate),
        "equity": records(curve),
        "reconciliation": records(reconciliation),
        "trades": records(updated),
        "c1Trades": records(c1),
        "eventSummary": records(event_summary),
        "rules": records(rules),
        "summary": summary,
    }
    (args.output / "WorkbookData.json").write_text(
        json.dumps(workbook_data, default=str), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
