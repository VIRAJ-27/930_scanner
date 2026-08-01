from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from .config import (
    EMA_PERIOD,
    ENTRY_SEARCH_CANDLE_COUNT,
    G1_SEARCH_CANDLE_COUNT,
    LAST_TRIGGER_START,
    NORMAL_EMA_3M_LOOKBACK_BARS,
    NORMAL_EMA_3M_MIN_RISE,
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
    """First-red Trigger -> G1 -> percentage-capped TP1 state machine."""

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

    @property
    def done(self) -> bool:
        return self.state == "DONE"

    def on_three_minute(self, candle: Candle) -> None:
        self._append_ema(self.ema20_3m, candle.close)
        position = self.position
        if position is not None and position.open_quantity > 0:
            if (
                self.setup is not None
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
                        )
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
                                "g1_window_start": window_start,
                                "g1_window_end": self.setup.g1_window_end,
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

    def on_one_minute(self, candle: Candle) -> None:
        self._append_ema(self.ema20_1m, candle.close)
        if self.setup is None or self.position is not None or self.done:
            return
        trigger = self.setup.trigger
        if candle.low < trigger.low:
            self._discard_day(
                candle.completion_time,
                candle.low,
                "BROKE_TRIGGER_LOW_BEFORE_ENTRY",
            )
            return

        if self.state == "WAIT_G1":
            start = self.setup.g1_window_start
            end = self.setup.g1_window_end
            if start is None or end is None or candle.start < start:
                return
            if candle.start >= end:
                self._discard_day(
                    candle.completion_time,
                    candle.close,
                    "G1_WINDOW_EXPIRED",
                )
                return
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
            return

        if self.state != "WAIT_ENTRY" or self.setup.g1 is None:
            return
        start = self.setup.entry_window_start
        end = self.setup.entry_window_end
        if start is None or end is None or candle.start < start:
            return
        if candle.start >= end:
            self._discard_day(
                candle.completion_time,
                candle.close,
                "ENTRY_WINDOW_EXPIRED",
            )
            return
        if candle.low < self.setup.g1.low:
            self._discard_day(
                candle.completion_time,
                candle.low,
                "ENTRY_CANDLE_BROKE_G1_LOW",
            )
            return
        if candle.high > self.setup.g1.high:
            self._discard_day(
                candle.completion_time,
                candle.close,
                "MISSED_INTRAMINUTE_G1_HIGH_BREAK",
            )
            return
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
        return self._open_position(timestamp, price, r2_target, target)

    def _open_position(
        self,
        timestamp: datetime,
        price: float,
        r2_target: float,
        target: float,
    ) -> Position:
        setup = self.setup
        if setup is None or setup.g1 is None or setup.r1_target is None:
            raise RuntimeError("Entry attempted without a complete setup.")
        trade_id = f"{timestamp.date().isoformat()}-{self.symbol}-S1"
        self.position = Position(
            trade_id=trade_id,
            symbol=self.symbol,
            setup_number=1,
            entry_time=timestamp,
            entry_price=price,
            initial_sl=setup.trigger.low,
            current_sl=setup.trigger.low,
            tp1_target=target,
            open_quantity=QUANTITY,
            entry_mode="G1_HIGH_BREAK",
            entry_tier=setup.entry_tier or "",
            ema_3m_rise_fraction=setup.ema_3m_rise_fraction,
            ema_1m_rise_fraction=setup.ema_1m_rise_fraction,
            g1_body_fraction=setup.g1_body_fraction,
            g1_low=setup.g1.low,
            trigger_high=setup.trigger.high,
            r1_target=setup.r1_target,
            r2_target=r2_target,
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
                "entry_mode": "G1_HIGH_BREAK",
                "entry_tier": setup.entry_tier,
                "ema_3m_rise_percent": self._percent(
                    setup.ema_3m_rise_fraction
                ),
                "ema_1m_rise_percent": self._percent(
                    setup.ema_1m_rise_fraction
                ),
                "g1_body_percent": self._percent(setup.g1_body_fraction),
                "sl": setup.trigger.low,
                "r1": setup.r1_target,
                "r2": r2_target,
                "tp1": target,
                "target_driver": "R1" if setup.r1_target <= r2_target else "R2",
                "g1": setup.g1.details(),
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
        if position.trigger_high is not None:
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
