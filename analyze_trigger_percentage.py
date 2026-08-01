from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    return parser.parse_args()


def read_trades(folder: Path) -> pd.DataFrame:
    candidates = [
        folder / "Trades.csv",
        folder / "trigger first entry.csv",
        folder / "trigger 1 min.csv",
    ]
    path = next((item for item in candidates if item.exists()), None)
    if path is None:
        raise FileNotFoundError(f"No trade CSV found in {folder}")
    frame = pd.read_csv(path)
    if "NetRR" not in frame:
        frame["NetRR"] = pd.to_numeric(frame["RMultiple"], errors="coerce")
    frame["EntryTime"] = pd.to_datetime(frame["EntryTime"])
    frame["GrossPnL"] = pd.to_numeric(frame["GrossPnL"], errors="coerce").fillna(0)
    frame["NetRR"] = pd.to_numeric(frame["NetRR"], errors="coerce").fillna(0)
    return frame.sort_values(["EntryTime", "Symbol"]).reset_index(drop=True)


def equity_curve(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame[["Date", "EntryTime", "Symbol", "GrossPnL", "NetRR"]].copy()
    result.insert(0, "TradeNumber", range(1, len(result) + 1))
    result["CumulativePnL"] = result["GrossPnL"].cumsum()
    result["CumulativeNetRR"] = result["NetRR"].cumsum()
    result["RunningPeakPnL"] = result["CumulativePnL"].cummax().clip(lower=0)
    result["Drawdown"] = result["CumulativePnL"] - result["RunningPeakPnL"]
    return result


def daily_curve(frame: pd.DataFrame) -> pd.DataFrame:
    daily = frame.groupby("Date", as_index=False).agg(
        Trades=("Symbol", "size"),
        DailyPnL=("GrossPnL", "sum"),
        DailyNetRR=("NetRR", "sum"),
    )
    daily["CumulativePnL"] = daily["DailyPnL"].cumsum()
    daily["CumulativeNetRR"] = daily["DailyNetRR"].cumsum()
    daily["RunningPeakPnL"] = daily["CumulativePnL"].cummax().clip(lower=0)
    daily["Drawdown"] = daily["CumulativePnL"] - daily["RunningPeakPnL"]
    return daily


def max_losing_streak(pnl: pd.Series) -> int:
    longest = current = 0
    for value in pnl:
        current = current + 1 if value < 0 else 0
        longest = max(longest, current)
    return longest


def metrics(frame: pd.DataFrame) -> dict:
    pnl = frame["GrossPnL"]
    curve = equity_curve(frame)
    profit = float(pnl[pnl > 0].sum())
    loss = abs(float(pnl[pnl < 0].sum()))
    max_dd = abs(float(curve["Drawdown"].min()))
    return {
        "Trades": len(frame),
        "WinRate": round(float((pnl > 0).mean()), 4),
        "NetPnL": round(float(pnl.sum()), 2),
        "NetRR": round(float(frame["NetRR"].sum()), 4),
        "AverageRR": round(float(frame["NetRR"].mean()), 4),
        "AveragePnL": round(float(pnl.mean()), 2),
        "ProfitFactor": round(profit / loss, 4) if loss else None,
        "MaxDrawdown": round(max_dd, 2),
        "RecoveryFactor": round(float(pnl.sum()) / max_dd, 4) if max_dd else None,
        "TP1Trades": int(frame["TP1ExitTime"].fillna("").astype(bool).sum()),
        "TP1Rate": round(float(frame["TP1ExitTime"].fillna("").astype(bool).mean()), 4),
        "MaxLosingStreak": max_losing_streak(pnl),
    }


def pattern_rows(frame: pd.DataFrame, dimension: str) -> list[dict]:
    rows = []
    for value, group in frame.groupby(dimension, dropna=False):
        pnl = group["GrossPnL"]
        rows.append(
            {
                "Dimension": dimension,
                "Segment": str(value),
                "Trades": len(group),
                "WinRate": round(float((pnl > 0).mean()), 4),
                "TP1Rate": round(float(group["TP1Hit"].mean()), 4),
                "NetPnL": round(float(pnl.sum()), 2),
                "NetRR": round(float(group["NetRR"].sum()), 4),
                "AverageRR": round(float(group["NetRR"].mean()), 4),
                "AverageTriggerRangePercent": round(float(group["TriggerRangePercent"].mean()), 4),
                "AverageRiskPercent": round(float(group["RiskPercent"].mean()), 4),
                "AverageHoldingMinutes": round(float(group["HoldingMinutes"].mean()), 2),
                "RunnerPnL": round(float(group["RunnerPnL"].sum()), 2),
            }
        )
    return rows


def main():
    args = parse_args()
    previous = read_trades(args.previous)
    new = read_trades(args.new)
    new_curve = equity_curve(new)
    new_daily = daily_curve(new)
    new_curve.to_csv(args.new / "EquityCurve.csv", index=False)
    new_daily.to_csv(args.new / "DailyEquityCurve.csv", index=False)

    old_metrics = metrics(previous)
    new_metrics = metrics(new)
    comparison = {
        "Period": "All",
        **{f"Previous{k}": v for k, v in old_metrics.items()},
        **{f"New{k}": v for k, v in new_metrics.items()},
    }
    for key in ["Trades", "WinRate", "NetPnL", "NetRR", "AverageRR", "AveragePnL", "ProfitFactor", "MaxDrawdown", "RecoveryFactor", "TP1Trades", "TP1Rate", "MaxLosingStreak"]:
        old_value = old_metrics.get(key)
        new_value = new_metrics.get(key)
        comparison[f"{key}Delta"] = (
            round(new_value - old_value, 4)
            if old_value is not None and new_value is not None
            else None
        )
    pd.DataFrame([comparison]).to_csv(args.new / "OverallComparison.csv", index=False)

    def monthly(frame):
        rows = []
        for month, group in frame.groupby("Month"):
            row = {"Month": month, **metrics(group)}
            rows.append(row)
        return pd.DataFrame(rows)

    old_monthly = monthly(previous).add_prefix("Previous").rename(columns={"PreviousMonth": "Month"})
    new_monthly = monthly(new).add_prefix("New").rename(columns={"NewMonth": "Month"})
    month_compare = old_monthly.merge(new_monthly, on="Month", how="outer").fillna(0)
    for key in ["Trades", "WinRate", "NetPnL", "NetRR", "AverageRR", "ProfitFactor", "MaxDrawdown", "TP1Rate"]:
        month_compare[f"{key}Delta"] = month_compare[f"New{key}"] - month_compare[f"Previous{key}"]
    month_compare.to_csv(args.new / "MonthlyComparison.csv", index=False)

    new["TP1Hit"] = new["TP1ExitTime"].fillna("").astype(bool)
    new["OutcomeGroup"] = "Other no TP1"
    new.loc[new["ExitReason"] == "INITIAL_SL", "OutcomeGroup"] = "Initial SL"
    new.loc[new["TP1Hit"], "OutcomeGroup"] = "TP1 + Runner"
    new["TriggerRangeBucket"] = pd.cut(
        new["TriggerRangePercent"],
        [-float("inf"), 0.30, 0.50, 0.75, float("inf")],
        labels=["<0.30%", "0.30%-<0.50%", "0.50%-<0.75%", ">=0.75%"],
        right=False,
    )
    new["RiskBucket"] = pd.cut(
        new["RiskPercent"],
        [-float("inf"), 0.25, 0.50, 1.00, float("inf")],
        labels=["<0.25%", "0.25%-<0.50%", "0.50%-<1.00%", ">=1.00%"],
        right=False,
    )
    new["TriggerStart"] = pd.to_datetime(new["TriggerTime"]).dt.strftime("%H:%M")
    dimensions = [
        "OutcomeGroup", "TriggerRangeBucket", "RiskBucket", "TriggerStart",
        "G1DelayCandle", "EntryDelayAfterG1", "TargetDriver",
    ]
    pattern = pd.DataFrame(
        [row for dimension in dimensions for row in pattern_rows(new, dimension)]
    )
    pattern.to_csv(args.new / "PatternAnalysis.csv", index=False)

    stock = new.groupby("Symbol", as_index=False).agg(
        Trades=("Symbol", "size"),
        TP1Trades=("TP1Hit", "sum"),
        InitialSLTrades=("ExitReason", lambda s: int((s == "INITIAL_SL").sum())),
        NetPnL=("GrossPnL", "sum"),
        NetRR=("NetRR", "sum"),
        RunnerPnL=("RunnerPnL", "sum"),
        AverageTriggerRangePercent=("TriggerRangePercent", "mean"),
        AverageRiskPercent=("RiskPercent", "mean"),
        AverageEntryDelay=("EntryDelayAfterG1", "mean"),
    )
    stock["TP1Rate"] = stock["TP1Trades"] / stock["Trades"]
    stock["InitialSLRate"] = stock["InitialSLTrades"] / stock["Trades"]
    stock.sort_values(["RunnerPnL", "TP1Trades"], ascending=False).to_csv(
        args.new / "RunnerStockPatterns.csv", index=False
    )
    stock.sort_values(["InitialSLTrades", "InitialSLRate"], ascending=False).to_csv(
        args.new / "SLStockPatterns.csv", index=False
    )

    def segment(dimension: str, name: str) -> pd.Series:
        return pattern[
            (pattern["Dimension"] == dimension) & (pattern["Segment"] == name)
        ].iloc[0]

    trigger_0930 = segment("TriggerStart", "09:30")
    trigger_0936 = segment("TriggerStart", "09:36")
    g1_first = segment("G1DelayCandle", "1")
    g1_third = segment("G1DelayCandle", "3")
    entry_first = segment("EntryDelayAfterG1", "1")
    entry_second = segment("EntryDelayAfterG1", "2")
    range_mid = segment("TriggerRangeBucket", "0.30%-<0.50%")
    risk_low = segment("RiskBucket", "<0.25%")
    r1 = segment("TargetDriver", "R1")
    r2 = segment("TargetDriver", "R2")
    top_runner = stock.sort_values("RunnerPnL", ascending=False).iloc[0]
    sl_candidates = stock[stock["Trades"] >= 4].sort_values(
        ["InitialSLRate", "InitialSLTrades"], ascending=False
    )
    top_sl = sl_candidates.iloc[0]
    runner_pnl = float(new["RunnerPnL"].sum())
    insights = pd.DataFrame(
        [
            (
                "Later Trigger",
                f"09:36: {int(trigger_0936.Trades)} trades, {trigger_0936.AverageRR:.4f} avg RR; "
                f"09:30: {int(trigger_0930.Trades)} trades, {trigger_0930.AverageRR:.4f} avg RR",
                "Later valid Triggers were stronger in this sample.",
                "Association only; do not add a time filter without an out-of-sample test.",
            ),
            (
                "Later G1",
                f"Third G1 candle: {int(g1_third.Trades)} trades, {g1_third.AverageRR:.4f} avg RR; "
                f"first G1 candle: {int(g1_first.Trades)} trades, {g1_first.AverageRR:.4f} avg RR",
                "Delayed green confirmation had higher average RR but a much smaller sample.",
                "Third-candle sample is only 45 trades.",
            ),
            (
                "Entry Timing",
                f"Second entry candle: {int(entry_second.Trades)} trades, {entry_second.WinRate:.1%} wins, "
                f"{entry_second.AverageRR:.4f} avg RR; first: {entry_first.AverageRR:.4f} avg RR",
                "Waiting beyond the first breakout minute correlated with better normalized outcomes.",
                "Fixed-share rupee P&L was still affected by stock price and outliers.",
            ),
            (
                "Target Driver",
                f"R2: {int(r2.Trades)} trades, {r2.AverageRR:.4f} avg RR; "
                f"R1: {int(r1.Trades)} trades, {r1.AverageRR:.4f} avg RR",
                "Trades capped by the 3R level outperformed percentage-capped R1 trades on average.",
                "R2 was only 184 of 859 trades.",
            ),
            (
                "Trigger Range",
                f"0.30%-<0.50%: {int(range_mid.Trades)} trades, INR {range_mid.NetPnL:,.2f} P&L, "
                f"{range_mid.AverageRR:.4f} avg RR",
                "The middle pre-threshold range produced the strongest rupee contribution.",
                "Rupee P&L is skewed by fixed 100-share sizing.",
            ),
            (
                "Lower Initial Risk",
                f"<0.25% risk: {int(risk_low.Trades)} trades, {risk_low.AverageRR:.4f} avg RR, "
                f"{risk_low.NetRR:.4f} total RR",
                "Tighter entry-to-Trigger-low risk produced the strongest total normalized return.",
                "Execution/slippage sensitivity is highest for tight-risk trades.",
            ),
            (
                "Runner Contribution",
                f"30-share runners contributed INR {runner_pnl:,.2f}; TP1+runner trades contributed "
                f"INR {float(new.loc[new.TP1Hit, 'GrossPnL'].sum()):,.2f}",
                "The runner is economically important and offsets a large part of stop-loss drag.",
                "Runner value depends on bar-based trailing assumptions.",
            ),
            (
                "Stop-Loss Drag",
                f"{int((new.ExitReason == 'INITIAL_SL').sum())} initial-stop trades contributed "
                f"INR {float(new.loc[new.ExitReason == 'INITIAL_SL', 'GrossPnL'].sum()):,.2f}",
                "Initial-stop frequency is the main weakness of the strategy.",
                "Filtering based on this same sample risks overfitting.",
            ),
            (
                "Runner Stock Example",
                f"{top_runner.Symbol}: INR {top_runner.RunnerPnL:,.2f} runner P&L, "
                f"{int(top_runner.TP1Trades)}/{int(top_runner.Trades)} TP1 trades",
                "Some high-priced names dominate fixed-share runner P&L.",
                "Use risk-normalized sizing before presenting scalable capital requirements.",
            ),
            (
                "SL-Prone Stock Example",
                f"{top_sl.Symbol}: {int(top_sl.InitialSLTrades)}/{int(top_sl.Trades)} initial-stop trades",
                "Loss concentration exists at the symbol level.",
                "Per-stock samples are small and not sufficient for exclusions.",
            ),
            (
                "Risk Comparison",
                f"New max drawdown INR {new_metrics['MaxDrawdown']:,.2f} vs previous "
                f"INR {old_metrics['MaxDrawdown']:,.2f}; profit factor {new_metrics['ProfitFactor']:.4f} "
                f"vs {old_metrics['ProfitFactor']:.4f}",
                "The percentage version increased trade count but weakened risk-adjusted quality.",
                "Investor materials must show this adverse comparison, not only gross profit.",
            ),
        ],
        columns=["Pattern", "Evidence", "Interpretation", "Caution"],
    )
    insights.to_csv(args.new / "PatternInsights.csv", index=False)

    investor = pd.DataFrame(
        [
            ("Backtest period", f"{new['Date'].min()} to {new['Date'].max()}", "Available data only"),
            ("Stocks", int(new["Symbol"].nunique()), "Stocks with at least one trade"),
            *[(key, value, "") for key, value in new_metrics.items()],
            ("RunnerPnL", round(float(new["RunnerPnL"].sum()), 2), "Contribution from final 30 shares after TP1"),
            ("InitialSLPnL", round(float(new.loc[new["ExitReason"] == "INITIAL_SL", "GrossPnL"].sum()), 2), "Loss contribution from initial-stop exits"),
            ("BestDay", round(float(new_daily["DailyPnL"].max()), 2), "Gross P&L"),
            ("WorstDay", round(float(new_daily["DailyPnL"].min()), 2), "Gross P&L"),
            ("CostsIncluded", "No", "Brokerage, taxes, fees and slippage excluded"),
            ("LiveValidation", "No", "Historical bar backtest; not a live audited track record"),
        ],
        columns=["Metric", "Value", "Notes"],
    )
    investor.to_csv(args.new / "InvestorMetrics.csv", index=False)


if __name__ == "__main__":
    main()
