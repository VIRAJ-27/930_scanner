"""Shared, SOT-only data helpers. No imports from the 09:30 scanner project."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
EMA_PERIOD = 50


@dataclass
class Bar:
    symbol: str
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def load_minute_bars(path: Path, symbol: str) -> list[Bar]:
    bars: list[Bar] = []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"Datetime", "Open", "High", "Low", "Close", "Volume"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"{path}: expected columns {sorted(required)}")
        for row in reader:
            stamp = datetime.fromisoformat(row["Datetime"].strip())
            stamp = stamp.replace(tzinfo=IST) if stamp.tzinfo is None else stamp.astimezone(IST)
            if time(9, 15) <= stamp.time() < time(15, 30):
                bars.append(Bar(symbol, stamp, *(float(row[key]) for key in ("Open", "High", "Low", "Close", "Volume"))))
    bars.sort(key=lambda bar: bar.start)
    return bars


def aggregate_three_minute(minutes: list[Bar]) -> list[Bar]:
    grouped: dict[datetime, list[Bar]] = {}
    for bar in minutes:
        stamp = bar.start
        offset = stamp.hour * 60 + stamp.minute - (9 * 60 + 15)
        if offset < 0:
            continue
        start_minute = 9 * 60 + 15 + (offset // 3) * 3
        start = stamp.replace(hour=start_minute // 60, minute=start_minute % 60, second=0, microsecond=0)
        grouped.setdefault(start, []).append(bar)

    result: list[Bar] = []
    for start, bars in sorted(grouped.items()):
        if len(bars) != 3:
            continue
        bars.sort(key=lambda item: item.start)
        result.append(Bar(bars[0].symbol, start, bars[0].open,
                          max(item.high for item in bars), min(item.low for item in bars),
                          bars[-1].close, sum(item.volume for item in bars)))
    return result


def ema_series(bars: list[Bar]) -> list[float]:
    alpha = 2.0 / (EMA_PERIOD + 1.0)
    values: list[float] = []
    for bar in bars:
        values.append(bar.close if not values else alpha * bar.close + (1 - alpha) * values[-1])
    return values


def vwap_series(bars: list[Bar]) -> list[float]:
    values: list[float] = []
    current_day = None
    cumulative_value = cumulative_volume = 0.0
    for bar in bars:
        if bar.start.date() != current_day:
            current_day = bar.start.date()
            cumulative_value = cumulative_volume = 0.0
        typical_price = (bar.high + bar.low + bar.close) / 3.0
        cumulative_value += typical_price * bar.volume
        cumulative_volume += bar.volume
        values.append(cumulative_value / cumulative_volume if cumulative_volume else bar.close)
    return values


def load_universe(path: Path) -> list[str]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if "Symbol" not in (reader.fieldnames or []):
            raise SystemExit(f"{path} must contain a Symbol column")
        return sorted({row["Symbol"].strip().upper() for row in reader if row.get("Symbol", "").strip()})

