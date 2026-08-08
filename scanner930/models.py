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
    g1_window_start: datetime | None = None
    g1_window_end: datetime | None = None
    g1: Candle | None = None
    b1: Candle | None = None
    entry_path: str = "STANDARD_G1"
    large_green_candle: Candle | None = None
    large_green_range_fraction: float | None = None
    b1_window_end: datetime | None = None
    b1_confirmation_end: datetime | None = None
    entry_window_start: datetime | None = None
    entry_window_end: datetime | None = None
    trigger_range_fraction: float | None = None
    ep_fraction: float | None = None
    r1_target: float | None = None
    entry_tier: str | None = None
    ema_3m_rise_fraction: float | None = None
    ema_1m_rise_fraction: float | None = None
    g1_body_fraction: float | None = None
    g1_range_fraction: float | None = None
    b1_range_fraction: float | None = None
    target_r_multiple: float | None = None
    trigger_rvol20_3m: float | None = None
    trigger_same_slot_rvol10: float | None = None
    alpha_entry: bool = False
    beta_entry: bool = False
    gamma_entry: bool = False
    entry_quality: str = "STANDARD"
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
    quantity: int = 100
    tp1_quantity: int = 70
    runner_quantity: int = 30
    open_quantity: int = 100
    entry_mode: str = "G1_HIGH_BREAK"
    entry_tier: str = ""
    ema_3m_rise_fraction: float | None = None
    ema_1m_rise_fraction: float | None = None
    g1_body_fraction: float | None = None
    g1_low: float | None = None
    trigger_high: float | None = None
    r1_target: float | None = None
    r2_target: float | None = None
    b1_high: float | None = None
    b1_low: float | None = None
    b1_confirmation_end: datetime | None = None
    b1_close_confirmed: bool = False
    large_green_range_fraction: float | None = None
    reference_range_fraction: float | None = None
    target_r_multiple: float | None = None
    entry_quality: str = "STANDARD"
    alpha_entry: bool = False
    beta_entry: bool = False
    gamma_entry: bool = False
    trigger_rvol20_3m: float | None = None
    trigger_same_slot_rvol10: float | None = None
    tp1_touched: bool = False
    tp1_touch_time: datetime | None = None
    tp1_minute: datetime | None = None
    tp1_booked: bool = False
    tp1_exit_time: datetime | None = None
    tp1_exit_price: float | None = None
    final_exit_time: datetime | None = None
    final_exit_price: float | None = None
    final_exit_reason: str | None = None
