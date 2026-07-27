from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from .config import (
    LAST_TRIGGER_START,
    MAX_SETUPS_PER_STOCK,
    QUANTITY,
    RUNNER_QUANTITY,
    TRIGGER_START,
    TP1_QUANTITY,
    TP1_R_MULTIPLE,
)
from .models import Candle, Position, Setup


class ScannerStrategy:
    """Deterministic first-red Trigger -> early entry/C1 -> C2 state machine."""

    def __init__(self, symbol: str, emit: Callable[..., None]):
        self.symbol = symbol
        self.emit = emit
        self.state = "SEARCH_TRIGGER"
        self.previous_3m: Candle | None = None
        self.latest_completed_3m: Candle | None = None
        self.setup: Setup | None = None
        self.attempts = 0
        self.position: Position | None = None
        self.tp1_ever_hit = False

    @property
    def done(self) -> bool:
        return self.state == "DONE"

    def on_three_minute(self, candle: Candle) -> None:
        self.latest_completed_3m = candle

        if (
            self.state == "POSITION_WAIT_C1_CLOSE"
            and self.position is not None
            and self.position.open_quantity > 0
            and self.setup is not None
        ):
            expected = self.setup.trigger.start + timedelta(minutes=3)
            if candle.start != expected:
                self.emit(
                    candle.completion_time,
                    "C1_DATA_GAP_POSITION",
                    candle.close,
                    {"expected_start": expected, "actual_start": candle.start},
                )
                self.previous_3m = candle
                return
            position = self.position
            self.setup.c1 = candle
            position.awaiting_c1_close = False
            position.c1_low = candle.low
            position.current_sl = max(position.current_sl, candle.low)
            risk = position.entry_price - candle.low
            if risk > 0:
                position.tp1_target = (
                    position.entry_price + TP1_R_MULTIPLE * risk
                )
            self.state = "POSITION"
            self.emit(
                candle.completion_time,
                "C1_FINALIZED_EARLY_ENTRY",
                candle.close,
                {
                    "setup_number": position.setup_number,
                    "c1": candle.details(),
                    "new_sl": position.current_sl,
                    "tp1": position.tp1_target,
                },
            )
            if (
                position.tp1_target is not None
                and candle.high >= position.tp1_target
            ):
                position.tp1_touched = True
                position.tp1_touch_time = candle.completion_time
                position.tp1_due_at_c1_close = True
                self.tp1_ever_hit = True
                self.emit(
                    candle.completion_time,
                    "TP1_REACHED_IN_C1",
                    candle.close,
                    {
                        "trade_id": position.trade_id,
                        "target": position.tp1_target,
                    },
                )
            self.previous_3m = candle
            return

        if self.position is not None and self.position.open_quantity > 0:
            if self.position.tp1_booked:
                old = self.position.current_sl
                self.position.current_sl = max(old, candle.low)
                if self.position.current_sl > old:
                    self.emit(
                        candle.completion_time,
                        "TRAIL_RAISED",
                        candle.close,
                        {"old_sl": old, "new_sl": self.position.current_sl},
                    )
            self.previous_3m = candle
            return

        if self.done:
            self.previous_3m = candle
            return

        if self.state == "WAIT_C1":
            expected = self.setup.trigger.start + timedelta(minutes=3)
            if (
                candle.start == expected
                and candle.green
                and candle.low >= self.setup.trigger.low
            ):
                self.attempts += 1
                self.setup.number = self.attempts
                self.setup.c1 = candle
                self.setup.c2_start = candle.start + timedelta(minutes=3)
                self.state = "WAIT_C2"
                self.emit(
                    candle.completion_time,
                    "C1_VALID",
                    candle.close,
                    {
                        "setup_number": self.attempts,
                        "c1": candle.details(),
                        "trigger": self.setup.trigger.details(),
                    },
                )
            else:
                self.emit(
                    candle.completion_time,
                    "C1_INVALID",
                    candle.close,
                    {"trigger_start": self.setup.trigger.start},
                )
                self.setup = None
                self.state = "DONE"
            self.previous_3m = candle
            return

        if self.state == "WAIT_C2":
            if candle.start == self.setup.c2_start:
                self._fail_setup(
                    candle.completion_time,
                    candle.close,
                    "C2_NO_C1_HIGH_BREAK",
                )
            else:
                self._fail_setup(
                    candle.completion_time,
                    candle.close,
                    "C2_DATA_GAP",
                )
            self.previous_3m = candle
            return

        if self.state == "SEARCH_TRIGGER":
            if candle.start.time() > LAST_TRIGGER_START:
                self.state = "DONE"
            elif candle.start.time() >= TRIGGER_START and candle.red:
                rejection_reasons = self._trigger_rejection_reasons(candle)
                if rejection_reasons:
                    self.emit(
                        candle.completion_time,
                        "TRIGGER_REJECTED_FIRST_RED",
                        candle.close,
                        {
                            "trigger": candle.details(),
                            "outcome": "FIRST_RED_TRIGGER_FAILED",
                            "reasons": rejection_reasons,
                        },
                    )
                    self.state = "DONE"
                else:
                    self.setup = Setup(number=0, trigger=candle)
                    self.state = "WAIT_C1"
                    self.emit(
                        candle.completion_time,
                        "TRIGGER_VALID",
                        candle.close,
                        {"trigger": candle.details()},
                    )

        self.previous_3m = candle

    def on_entry_tick(self, timestamp: datetime, price: float) -> Position | None:
        if self.state == "WAIT_C1" and self.setup is not None:
            trigger = self.setup.trigger
            c1_start = trigger.start + timedelta(minutes=3)
            if floor_minute(timestamp) < c1_start:
                return None
            if timestamp >= c1_start + timedelta(minutes=3):
                return None
            if price < trigger.low:
                self._discard_day(
                    timestamp,
                    price,
                    "C1_BROKE_TRIGGER_LOW_BEFORE_ENTRY",
                )
                return None
            if price <= trigger.high:
                return None
            self.attempts = 1
            self.setup.number = 1
            return self._open_position(
                timestamp=timestamp,
                price=price,
                stop=trigger.low,
                target=None,
                entry_mode="TRIGGER_HIGH_BREAK",
                awaiting_c1_close=True,
            )

        if self.state != "WAIT_C2" or self.setup is None or self.setup.c1 is None:
            return None
        if floor_minute(timestamp) < self.setup.c2_start:
            return None
        if timestamp >= self.setup.c2_start + timedelta(minutes=3):
            return None

        c1 = self.setup.c1
        if price < c1.low:
            self._fail_setup(timestamp, price, "C2_BROKE_C1_LOW_FIRST")
            return None
        if price <= c1.high:
            return None

        risk = price - c1.low
        if risk <= 0:
            self._fail_setup(timestamp, price, "NON_POSITIVE_RISK")
            return None
        return self._open_position(
            timestamp=timestamp,
            price=price,
            stop=c1.low,
            target=price + TP1_R_MULTIPLE * risk,
            entry_mode="C1_HIGH_BREAK",
            awaiting_c1_close=False,
        )

    def _open_position(
        self,
        timestamp: datetime,
        price: float,
        stop: float,
        target: float | None,
        entry_mode: str,
        awaiting_c1_close: bool,
    ) -> Position:
        trade_id = f"{timestamp.date().isoformat()}-{self.symbol}-S{self.attempts}"
        self.position = Position(
            trade_id=trade_id,
            symbol=self.symbol,
            setup_number=self.attempts,
            entry_time=timestamp,
            entry_price=price,
            initial_sl=stop,
            current_sl=stop,
            tp1_target=target,
            open_quantity=QUANTITY,
            entry_mode=entry_mode,
            awaiting_c1_close=awaiting_c1_close,
            c1_low=None if awaiting_c1_close else stop,
        )
        self.state = (
            "POSITION_WAIT_C1_CLOSE" if awaiting_c1_close else "POSITION"
        )
        self.setup.outcome = "ENTRY"
        self.emit(
            timestamp,
            "ENTRY_SIGNAL",
            price,
            {
                "trade_id": trade_id,
                "setup_number": self.attempts,
                "quantity": QUANTITY,
                "entry_mode": entry_mode,
                "sl": stop,
                "tp1": target,
                "outcome": "ENTRY",
            },
        )
        return self.position

    def mark_tp1_booked(self, timestamp: datetime, price: float) -> None:
        position = self.position
        if position is None:
            return
        position.tp1_booked = True
        position.tp1_due_at_c1_close = False
        position.tp1_exit_time = timestamp
        position.tp1_exit_price = price
        position.open_quantity = RUNNER_QUANTITY
        self.tp1_ever_hit = True
        if self.latest_completed_3m is not None:
            position.current_sl = max(
                position.current_sl,
                self.latest_completed_3m.low,
            )

    def mark_closed(
        self,
        timestamp: datetime,
        price: float,
        reason: str,
    ) -> None:
        position = self.position
        if position is None:
            return
        position.final_exit_time = timestamp
        position.final_exit_price = price
        position.final_exit_reason = reason
        position.open_quantity = 0
        self.state = "DONE"
        self.setup = None

    def _trigger_rejection_reasons(self, candle: Candle) -> list[str]:
        previous = self.previous_3m
        reasons = []
        if not (TRIGGER_START <= candle.start.time() <= LAST_TRIGGER_START):
            reasons.append("OUTSIDE_TRIGGER_WINDOW")
        if not candle.red:
            reasons.append("NOT_RED")
        if candle.vwap is None or candle.close <= candle.vwap:
            reasons.append("TRIGGER_NOT_ABOVE_VWAP")
        if (
            previous is None
            or previous.start + timedelta(minutes=3) != candle.start
        ):
            reasons.append("PREVIOUS_CANDLE_NOT_CONTIGUOUS")
            return reasons
        if not previous.green:
            reasons.append("PREVIOUS_CANDLE_NOT_GREEN")
        if previous.vwap is None or previous.close <= previous.vwap:
            reasons.append("PREVIOUS_CANDLE_NOT_ABOVE_VWAP")
        if not (previous.low <= candle.close <= previous.high):
            reasons.append("TRIGGER_CLOSE_OUTSIDE_PREVIOUS_RANGE")
        return reasons

    def _discard_day(
        self,
        timestamp: datetime,
        price: float,
        outcome: str,
    ) -> None:
        self.emit(
            timestamp,
            "SETUP_FAILED",
            price,
            {"setup_number": self.attempts, "outcome": outcome},
        )
        self.setup = None
        self.state = "DONE"

    def _fail_setup(
        self,
        timestamp: datetime,
        price: float,
        outcome: str,
    ) -> None:
        if self.setup is not None:
            self.setup.outcome = outcome
        self.emit(
            timestamp,
            "SETUP_FAILED",
            price,
            {"setup_number": self.attempts, "outcome": outcome},
        )
        self.setup = None
        self.state = (
            "DONE"
            if self.attempts >= MAX_SETUPS_PER_STOCK
            or timestamp.time() > LAST_TRIGGER_START
            else "SEARCH_TRIGGER"
        )


def floor_minute(timestamp: datetime) -> datetime:
    return timestamp.replace(second=0, microsecond=0)
