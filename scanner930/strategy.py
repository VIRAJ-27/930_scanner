from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from .config import (
    ENTRY_SEARCH_CANDLE_COUNT,
    G1_SEARCH_CANDLE_COUNT,
    LAST_TRIGGER_START,
    QUANTITY,
    RUNNER_QUANTITY,
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

    @property
    def done(self) -> bool:
        return self.state == "DONE"

    def on_three_minute(self, candle: Candle) -> None:
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
                "NO_G1_HIGH_BREAK_IN_THREE_CANDLES",
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

    def _discard_day(self, timestamp: datetime, price: float, outcome: str) -> None:
        if self.done:
            return
        if self.setup is not None:
            self.setup.outcome = outcome
        self.emit(
            timestamp,
            "SETUP_FAILED",
            price,
            {"setup_number": self.attempts, "outcome": outcome},
        )
        self.state = "DONE"


def floor_minute(timestamp: datetime) -> datetime:
    return timestamp.replace(second=0, microsecond=0)
