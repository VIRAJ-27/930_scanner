from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass
class Candle:
    symbol: str
    minutes: int
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    vwap: float | None = None

    @property
    def timeframe(self) -> str:
        return f"{self.minutes}m"

    @property
    def completion_time(self) -> datetime:
        return self.start + timedelta(minutes=self.minutes)

    @property
    def green(self) -> bool:
        return self.close > self.open

    @property
    def red(self) -> bool:
        return self.close < self.open

    def details(self) -> dict[str, Any]:
        result = asdict(self)
        result["completion_time"] = self.completion_time
        result["timeframe"] = self.timeframe
        return result


@dataclass
class Setup:
    number: int
    trigger: Candle
    guide_window_start: datetime | None = None
    guide_window_end: datetime | None = None
    g1: Candle | None = None
    g2_start: datetime | None = None
    g3_start: datetime | None = None
    outcome: str = ""


@dataclass
class Position:
    trade_id: str
    symbol: str
    setup_number: int
    entry_time: datetime
    entry_price: float
    initial_sl: float
    current_sl: float
    tp1_target: float | None
    open_quantity: int = 100
    entry_mode: str = "G2_G1_HIGH_BREAK"
    g1_low: float | None = None
    tp1_touched: bool = False
    tp1_touch_time: datetime | None = None
    tp1_minute: datetime | None = None
    tp1_booked: bool = False
    tp1_exit_time: datetime | None = None
    tp1_exit_price: float | None = None
    final_exit_time: datetime | None = None
    final_exit_price: float | None = None
    final_exit_reason: str | None = None
