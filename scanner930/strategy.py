from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from .config import (
    B1_CONFIRM_CANDLE_COUNT,
    B1_ENTRY_CANDLE_COUNT,
    B1_SEARCH_CANDLE_COUNT,
    B1_TP1_R_MULTIPLE,
    EMA_PERIOD,
    ENTRY_SEARCH_CANDLE_COUNT,
    G1_SEARCH_CANDLE_COUNT,
    LAST_TRIGGER_START,
    LARGE_GREEN_RANGE_THRESHOLD,
    NORMAL_EMA_3M_LOOKBACK_BARS,
    NORMAL_EMA_3M_MIN_RISE,
    PRETRIGGER_SCAN_START,
    QUANTITY,
    RUNNER_QUANTITY,
    SILVER_EMA_1M_LOOKBACK_BARS,
    SILVER_EMA_1M_MIN_RISE,
    SILVER_G1_MIN_BODY_FRACTION,
    TRIGGER_RANGE_ADDEND,
    TRIGGER_RANGE_MULTIPLIER,
    TRIGGER_RANGE_THRESHOLD,
    TRIGGER_START,
    TP1_R_MULTIPLE,
)
from .models import Candle, Position, Setup


class ScannerStrategy:
    """First-red Trigger with standard G1 or large-green B1 entry paths."""

    def __init__(self, symbol: str, emit: Callable[..., None]):
        self.symbol = symbol
        self.emit = emit
        self.state = "SEARCH_TRIGGER"
        self.previous_3m: Candle | None = None
        self.setup: Setup | None = None
        self.attempts = 0
        self.position: Position | None = None
        self.tp1_ever_hit = False
        self.ema20_1m: list[float] = []
        self.ema20_3m: list[float] = []
        self.large_pretrigger_green: Candle | None = None
        self.large_pretrigger_green_range: float | None = None

    @property
    def done(self) -> bool:
        return self.state == "DONE"

    def on_three_minute(self, candle: Candle) -> None:
        self._append_ema(self.ema20_3m, candle.close)
        self._remember_large_pretrigger_green(candle)
        position = self.position
        if position is not None and position.open_quantity > 0:
            if (
                self.setup is not None
                and position.entry_mode == "G1_HIGH_BREAK"
                and self.setup.g1 is not None
                and candle.close > self.setup.trigger.high
            ):
                old = position.current_sl
                position.current_sl = max(old, self.setup.g1.low)
                if position.current_sl > old:
                    self.emit(
                        candle.completion_time,
                        "SL_MOVED_TO_G1_LOW",
                        candle.close,
                        {
                            "old_sl": old,
                            "new_sl": position.current_sl,
                            "trigger_high": self.setup.trigger.high,
                        },
                    )
            self.previous_3m = candle
            return

        if self.done:
            self.previous_3m = candle
            return

        if self.state == "SEARCH_TRIGGER":
            if candle.start.time() > LAST_TRIGGER_START:
                self._discard_day(
                    candle.completion_time,
                    candle.close,
                    "NO_RED_TRIGGER_IN_FIRST_THREE",
                )
            elif candle.start.time() >= TRIGGER_START:
                if candle.red:
                    reasons = self._trigger_rejection_reasons(candle)
                    if reasons:
                        self.emit(
                            candle.completion_time,
                            "TRIGGER_REJECTED_FIRST_RED",
                            candle.close,
                            {
                                "trigger": candle.details(),
                                "outcome": "FIRST_RED_TRIGGER_FAILED",
                                "reasons": reasons,
                            },
                        )
                        self.state = "DONE"
                    else:
                        range_fraction = (candle.high - candle.low) / candle.low
                        ep_fraction = (
                            range_fraction * TRIGGER_RANGE_MULTIPLIER
                            if range_fraction < TRIGGER_RANGE_THRESHOLD
                            else range_fraction + TRIGGER_RANGE_ADDEND
                        )
                        r1_target = candle.high * (1.0 + ep_fraction)
                        window_start = candle.completion_time
                        self.attempts = 1
                        self.setup = Setup(
                            number=1,
                            trigger=candle,
                            g1_window_start=window_start,
                            g1_window_end=window_start
                            + timedelta(minutes=G1_SEARCH_CANDLE_COUNT),
                            trigger_range_fraction=range_fraction,
                            ep_fraction=ep_fraction,
                            r1_target=r1_target,
                            entry_path=(
                                "LARGE_GREEN_B1"
                                if self.large_pretrigger_green is not None
                                else "STANDARD_G1"
                            ),
                            large_green_candle=self.large_pretrigger_green,
                            large_green_range_fraction=(
                                self.large_pretrigger_green_range
                            ),
                        )
                        if self.setup.entry_path == "LARGE_GREEN_B1":
                            self.setup.b1_window_end = window_start + timedelta(
                                minutes=B1_SEARCH_CANDLE_COUNT
                            )
                            self.state = "WAIT_B1"
                        else:
                            self.state = "WAIT_G1"
                        self.emit(
                            candle.completion_time,
                            "TRIGGER_VALID",
                            candle.close,
                            {
                                "trigger": candle.details(),
                                "trigger_range_percent": range_fraction * 100,
                                "ep_percent": ep_fraction * 100,
                                "r1": r1_target,
                                "entry_path": self.setup.entry_path,
                                "large_green_range_percent": self._percent(
                                    self.setup.large_green_range_fraction
                                ),
                                "large_green_candle": (
                                    None
                                    if self.setup.large_green_candle is None
                                    else self.setup.large_green_candle.details()
                                ),
                                "g1_window_start": window_start,
                                "g1_window_end": self.setup.g1_window_end,
                                "b1_window_end": self.setup.b1_window_end,
                            },
                        )
                elif candle.start.time() == LAST_TRIGGER_START:
                    self._discard_day(
                        candle.completion_time,
                        candle.close,
                        "NO_RED_TRIGGER_IN_FIRST_THREE",
                    )
        self.previous_3m = candle

    def on_five_minute(self, candle: Candle) -> None:
        position = self.position
        if (
            position is None
            or position.open_quantity <= 0
            or not position.tp1_booked
        ):
            return
        old = position.current_sl
        position.current_sl = max(old, candle.low)
        if position.current_sl > old:
            self.emit(
                candle.completion_time,
                "TRAIL_RAISED_5M",
                candle.close,
                {"old_sl": old, "new_sl": position.current_sl},
            )

    def on_one_minute(self, candle: Candle) -> str | None:
        self._append_ema(self.ema20_1m, candle.close)
        if self.setup is None or self.done:
            return None
        if self.position is not None:
            return self._monitor_b1_close_confirmation(candle)
        trigger = self.setup.trigger
        if candle.low < trigger.low:
            self._discard_day(
                candle.completion_time,
                candle.low,
                "BROKE_TRIGGER_LOW_BEFORE_ENTRY",
            )
            return None

        if self.state == "WAIT_B1":
            start = trigger.completion_time
            end = self.setup.b1_window_end
            if end is None or candle.start < start:
                return None
            if candle.start >= end:
                self._discard_day(
                    candle.completion_time,
                    candle.close,
                    "NO_1M_CLOSE_ABOVE_TRIGGER_IN_SIX_CANDLES",
                )
                return None
            if candle.close > trigger.high:
                self.setup.b1 = candle
                self.setup.entry_window_start = candle.completion_time
                self.setup.entry_window_end = candle.completion_time + timedelta(
                    minutes=B1_ENTRY_CANDLE_COUNT
                )
                self.setup.b1_confirmation_end = candle.completion_time + timedelta(
                    minutes=B1_CONFIRM_CANDLE_COUNT
                )
                self.state = "WAIT_B1_BREAK"
                self.emit(
                    candle.completion_time,
                    "B1_VALID",
                    candle.close,
                    {
                        "setup_number": 1,
                        "entry_path": "LARGE_GREEN_B1",
                        "b1": candle.details(),
                        "entry_window_start": self.setup.entry_window_start,
                        "entry_window_end": self.setup.entry_window_end,
                        "confirmation_window_end": self.setup.b1_confirmation_end,
                    },
                )
            elif candle.completion_time >= end:
                self._discard_day(
                    candle.completion_time,
                    candle.close,
                    "NO_1M_CLOSE_ABOVE_TRIGGER_IN_SIX_CANDLES",
                )
            return None

        if self.state == "WAIT_B1_BREAK" and self.setup.b1 is not None:
            start = self.setup.entry_window_start
            end = self.setup.entry_window_end
            if start is None or end is None or candle.start < start:
                return None
            if candle.start >= end:
                self._discard_day(
                    candle.completion_time,
                    candle.close,
                    "NO_B1_HIGH_BREAK_IN_NEXT_1M_CANDLE",
                )
                return None
            if candle.high > self.setup.b1.high:
                self._discard_day(
                    candle.completion_time,
                    candle.close,
                    "MISSED_INTRAMINUTE_B1_HIGH_BREAK",
                )
                return None
            if candle.completion_time >= end:
                self._discard_day(
                    candle.completion_time,
                    candle.close,
                    "NO_B1_HIGH_BREAK_IN_NEXT_1M_CANDLE",
                )
            return None

        if self.state == "WAIT_G1":
            start = self.setup.g1_window_start
            end = self.setup.g1_window_end
            if start is None or end is None or candle.start < start:
                return None
            if candle.start >= end:
                self._discard_day(
                    candle.completion_time,
                    candle.close,
                    "G1_WINDOW_EXPIRED",
                )
                return None
            if candle.green:
                self.setup.g1 = candle
                self.setup.entry_window_start = candle.start + timedelta(minutes=1)
                self.setup.entry_window_end = (
                    self.setup.entry_window_start
                    + timedelta(minutes=ENTRY_SEARCH_CANDLE_COUNT)
                )
                self.state = "WAIT_ENTRY"
                self.emit(
                    candle.completion_time,
                    "G1_VALID",
                    candle.close,
                    {
                        "setup_number": 1,
                        "g1": candle.details(),
                        "entry_window_start": self.setup.entry_window_start,
                        "entry_window_end": self.setup.entry_window_end,
                    },
                )
            elif candle.start + timedelta(minutes=1) >= end:
                self._discard_day(
                    candle.completion_time,
                    candle.close,
                    "NO_GREEN_G1_IN_THREE_1M_CANDLES",
                )
            return None

        if self.state != "WAIT_ENTRY" or self.setup.g1 is None:
            return None
        start = self.setup.entry_window_start
        end = self.setup.entry_window_end
        if start is None or end is None or candle.start < start:
            return None
        if candle.start >= end:
            self._discard_day(
                candle.completion_time,
                candle.close,
                "ENTRY_WINDOW_EXPIRED",
            )
            return None
        if candle.low < self.setup.g1.low:
            self._discard_day(
                candle.completion_time,
                candle.low,
                "ENTRY_CANDLE_BROKE_G1_LOW",
            )
            return None
        if candle.high > self.setup.g1.high:
            self._discard_day(
                candle.completion_time,
                candle.close,
                "MISSED_INTRAMINUTE_G1_HIGH_BREAK",
            )
            return None
        if candle.start + timedelta(minutes=1) >= end:
            self._discard_day(
                candle.completion_time,
                candle.close,
                "NO_G1_HIGH_BREAK_IN_NEXT_CANDLE",
            )

    def on_entry_tick(self, timestamp: datetime, price: float) -> Position | None:
        if self.setup is None or self.position is not None or self.done:
            return None
        if price < self.setup.trigger.low:
            self._discard_day(timestamp, price, "BROKE_TRIGGER_LOW_BEFORE_ENTRY")
            return None
        if self.state == "WAIT_B1_BREAK" and self.setup.b1 is not None:
            start = self.setup.entry_window_start
            end = self.setup.entry_window_end
            if start is None or end is None or timestamp < start or timestamp >= end:
                return None
            if price <= self.setup.b1.high:
                return None
            risk = price - self.setup.b1.low
            if risk <= 0:
                self._discard_day(timestamp, price, "NON_POSITIVE_B1_RISK")
                return None
            self.setup.entry_tier = "B1"
            target = price + B1_TP1_R_MULTIPLE * risk
            return self._open_position(
                timestamp,
                price,
                target,
                target,
                initial_sl=self.setup.b1.low,
                entry_mode="B1_HIGH_BREAK",
            )
        if self.state != "WAIT_ENTRY" or self.setup.g1 is None:
            return None
        start = self.setup.entry_window_start
        end = self.setup.entry_window_end
        if start is None or end is None or timestamp < start or timestamp >= end:
            return None
        if price < self.setup.g1.low:
            self._discard_day(timestamp, price, "ENTRY_CANDLE_BROKE_G1_LOW")
            return None
        if price <= self.setup.g1.high:
            return None

        risk = price - self.setup.trigger.low
        if risk <= 0 or self.setup.r1_target is None:
            self._discard_day(timestamp, price, "NON_POSITIVE_RISK")
            return None
        entry_tier, filter_details = self._classify_entry()
        if entry_tier is None:
            self._discard_day(
                timestamp,
                price,
                "ENTRY_QUALITY_FILTERS_FAILED",
                filter_details,
            )
            return None
        self.setup.entry_tier = entry_tier
        r2_target = price + TP1_R_MULTIPLE * risk
        target = min(self.setup.r1_target, r2_target)
        return self._open_position(
            timestamp,
            price,
            r2_target,
            target,
            initial_sl=self.setup.trigger.low,
            entry_mode="G1_HIGH_BREAK",
        )

    def _open_position(
        self,
        timestamp: datetime,
        price: float,
        r2_target: float,
        target: float,
        initial_sl: float,
        entry_mode: str,
    ) -> Position:
        setup = self.setup
        reference = setup.b1 if setup and entry_mode == "B1_HIGH_BREAK" else (
            setup.g1 if setup else None
        )
        if setup is None or reference is None:
            raise RuntimeError("Entry attempted without a complete setup.")
        trade_id = f"{timestamp.date().isoformat()}-{self.symbol}-S1"
        self.position = Position(
            trade_id=trade_id,
            symbol=self.symbol,
            setup_number=1,
            entry_time=timestamp,
            entry_price=price,
            initial_sl=initial_sl,
            current_sl=initial_sl,
            tp1_target=target,
            open_quantity=QUANTITY,
            entry_mode=entry_mode,
            entry_tier=setup.entry_tier or "",
            ema_3m_rise_fraction=setup.ema_3m_rise_fraction,
            ema_1m_rise_fraction=setup.ema_1m_rise_fraction,
            g1_body_fraction=setup.g1_body_fraction,
            g1_low=setup.g1.low if setup.g1 is not None else None,
            trigger_high=setup.trigger.high,
            r1_target=setup.r1_target,
            r2_target=r2_target,
            b1_high=setup.b1.high if setup.b1 is not None else None,
            b1_low=setup.b1.low if setup.b1 is not None else None,
            b1_confirmation_end=setup.b1_confirmation_end,
            large_green_range_fraction=setup.large_green_range_fraction,
        )
        self.state = "POSITION"
        setup.outcome = "ENTRY"
        self.emit(
            timestamp,
            "ENTRY_SIGNAL",
            price,
            {
                "trade_id": trade_id,
                "setup_number": 1,
                "quantity": QUANTITY,
                "entry_mode": entry_mode,
                "entry_path": setup.entry_path,
                "entry_tier": setup.entry_tier,
                "ema_3m_rise_percent": self._percent(
                    setup.ema_3m_rise_fraction
                ),
                "ema_1m_rise_percent": self._percent(
                    setup.ema_1m_rise_fraction
                ),
                "g1_body_percent": self._percent(setup.g1_body_fraction),
                "sl": initial_sl,
                "r1": setup.r1_target,
                "r2": r2_target,
                "tp1": target,
                "target_driver": (
                    "R2_2.2R"
                    if entry_mode == "B1_HIGH_BREAK"
                    else "R1" if setup.r1_target <= r2_target else "R2"
                ),
                "g1": setup.g1.details() if setup.g1 is not None else None,
                "b1": setup.b1.details() if setup.b1 is not None else None,
                "b1_confirmation_end": setup.b1_confirmation_end,
                "large_green_range_percent": self._percent(
                    setup.large_green_range_fraction
                ),
                "outcome": "ENTRY",
            },
        )
        if price >= target:
            self.position.tp1_touched = True
            self.position.tp1_touch_time = timestamp
            self.position.tp1_minute = floor_minute(timestamp)
            self.tp1_ever_hit = True
            self.emit(
                timestamp,
                "TP1_TOUCHED_AT_ENTRY",
                price,
                {"trade_id": trade_id, "target": target},
            )
        return self.position

    def mark_tp1_booked(self, timestamp: datetime, price: float) -> None:
        position = self.position
        if position is None:
            return
        position.tp1_booked = True
        position.tp1_exit_time = timestamp
        position.tp1_exit_price = price
        position.open_quantity = RUNNER_QUANTITY
        if position.entry_mode == "B1_HIGH_BREAK":
            position.current_sl = max(position.current_sl, position.entry_price)
        elif position.trigger_high is not None:
            position.current_sl = max(position.current_sl, position.trigger_high)
        self.tp1_ever_hit = True

    def mark_closed(self, timestamp: datetime, price: float, reason: str) -> None:
        position = self.position
        if position is None:
            return
        position.final_exit_time = timestamp
        position.final_exit_price = price
        position.final_exit_reason = reason
        position.open_quantity = 0
        self.state = "DONE"

    def _trigger_rejection_reasons(self, candle: Candle) -> list[str]:
        previous = self.previous_3m
        reasons = []
        if not (TRIGGER_START <= candle.start.time() <= LAST_TRIGGER_START):
            reasons.append("OUTSIDE_TRIGGER_WINDOW")
        if not candle.red:
            reasons.append("NOT_RED")
        if candle.vwap is None or candle.close <= candle.vwap:
            reasons.append("TRIGGER_NOT_ABOVE_VWAP")
        if previous is None or previous.start + timedelta(minutes=3) != candle.start:
            reasons.append("PREVIOUS_CANDLE_NOT_CONTIGUOUS")
            return reasons
        if not previous.green:
            reasons.append("PREVIOUS_CANDLE_NOT_GREEN")
        if previous.vwap is None or previous.close <= previous.vwap:
            reasons.append("PREVIOUS_CANDLE_NOT_ABOVE_VWAP")
        if not (previous.low <= candle.close <= previous.high):
            reasons.append("TRIGGER_CLOSE_OUTSIDE_PREVIOUS_RANGE")
        return reasons

    def _monitor_b1_close_confirmation(self, candle: Candle) -> str | None:
        position = self.position
        setup = self.setup
        if (
            position is None
            or position.open_quantity <= 0
            or position.entry_mode != "B1_HIGH_BREAK"
            or position.b1_close_confirmed
            or setup is None
            or setup.b1 is None
        ):
            return None
        start = setup.b1.completion_time
        end = position.b1_confirmation_end
        if end is None or candle.start < start or candle.start >= end:
            return None
        if candle.close > setup.b1.high:
            position.b1_close_confirmed = True
            self.emit(
                candle.completion_time,
                "B1_CLOSE_CONFIRMED",
                candle.close,
                {
                    "trade_id": position.trade_id,
                    "b1_high": setup.b1.high,
                    "confirmed_candle": candle.details(),
                },
            )
            return None
        if candle.completion_time >= end:
            self.emit(
                candle.completion_time,
                "B1_CONFIRMATION_FAILED",
                candle.close,
                {
                    "trade_id": position.trade_id,
                    "b1_high": setup.b1.high,
                    "exit_rule": "THIRD_1M_CANDLE_CLOSE",
                },
            )
            return "BE_EXIT_NO_CLOSE_ABOVE_B1_HIGH"
        return None

    def _remember_large_pretrigger_green(self, candle: Candle) -> None:
        if self.state != "SEARCH_TRIGGER":
            return
        if not (PRETRIGGER_SCAN_START <= candle.start.time()):
            return
        if candle.start.time() >= TRIGGER_START and candle.red:
            return
        if not candle.green or candle.low <= 0:
            return
        range_fraction = (candle.high - candle.low) / candle.low
        if range_fraction <= LARGE_GREEN_RANGE_THRESHOLD:
            return
        if (
            self.large_pretrigger_green_range is None
            or range_fraction > self.large_pretrigger_green_range
        ):
            self.large_pretrigger_green = candle
            self.large_pretrigger_green_range = range_fraction

    def _classify_entry(self) -> tuple[str | None, dict]:
        setup = self.setup
        if setup is None or setup.g1 is None:
            return None, {}

        ema_3m_rise = self._ema_rise(
            self.ema20_3m,
            NORMAL_EMA_3M_LOOKBACK_BARS,
        )
        ema_1m_rise = self._ema_rise(
            self.ema20_1m,
            SILVER_EMA_1M_LOOKBACK_BARS,
        )
        g1_range = setup.g1.high - setup.g1.low
        g1_body = (
            abs(setup.g1.close - setup.g1.open) / g1_range
            if g1_range > 0
            else 0.0
        )
        setup.ema_3m_rise_fraction = ema_3m_rise
        setup.ema_1m_rise_fraction = ema_1m_rise
        setup.g1_body_fraction = g1_body

        silver = (
            ema_1m_rise is not None
            and ema_1m_rise >= SILVER_EMA_1M_MIN_RISE
            and g1_body >= SILVER_G1_MIN_BODY_FRACTION
        )
        normal = (
            ema_3m_rise is not None
            and ema_3m_rise >= NORMAL_EMA_3M_MIN_RISE
        )
        details = {
            "setup_number": 1,
            "ema_3m_rise_percent": self._percent(ema_3m_rise),
            "normal_threshold_percent": NORMAL_EMA_3M_MIN_RISE * 100,
            "normal_pass": normal,
            "ema_1m_rise_percent": self._percent(ema_1m_rise),
            "silver_ema_threshold_percent": SILVER_EMA_1M_MIN_RISE * 100,
            "g1_body_percent": g1_body * 100,
            "silver_g1_body_threshold_percent": (
                SILVER_G1_MIN_BODY_FRACTION * 100
            ),
            "silver_pass": silver,
        }
        if silver:
            return "SILVER", details
        if normal:
            return "NORMAL", details
        return None, details

    @staticmethod
    def _append_ema(history: list[float], close: float) -> None:
        alpha = 2.0 / (EMA_PERIOD + 1.0)
        ema = close if not history else alpha * close + (1.0 - alpha) * history[-1]
        history.append(ema)

    @staticmethod
    def _ema_rise(history: list[float], lookback: int) -> float | None:
        if len(history) <= lookback:
            return None
        earlier = history[-1 - lookback]
        if earlier <= 0:
            return None
        return history[-1] / earlier - 1.0

    @staticmethod
    def _percent(value: float | None) -> float | None:
        return None if value is None else value * 100

    def _discard_day(
        self,
        timestamp: datetime,
        price: float,
        outcome: str,
        extra_details: dict | None = None,
    ) -> None:
        if self.done:
            return
        if self.setup is not None:
            self.setup.outcome = outcome
        details = {"setup_number": self.attempts, "outcome": outcome}
        if extra_details:
            details.update(extra_details)
        self.emit(
            timestamp,
            "SETUP_FAILED",
            price,
            details,
        )
        self.state = "DONE"


def floor_minute(timestamp: datetime) -> datetime:
    return timestamp.replace(second=0, microsecond=0)
