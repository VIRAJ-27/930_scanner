from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from .config import (
    G1_MAX_RANGE_FRACTION,
    GUIDE_CANDLE_COUNT,
    LAST_TRIGGER_START,
    QUANTITY,
    RUNNER_QUANTITY,
    TRIGGER_START,
    TP1_R_MULTIPLE,
)
from .models import Candle, Position, Setup


class ScannerStrategy:
    """First-red 3m Trigger followed by a four-candle 1m guide sequence."""

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

        if self.position is not None and self.position.open_quantity > 0:
            if self.position.tp1_booked and candle.red:
                old = self.position.current_sl
                self.position.current_sl = max(old, candle.low)
                if self.position.current_sl > old:
                    self.emit(
                        candle.completion_time,
                        "TRAIL_RAISED_RED_3M",
                        candle.close,
                        {"old_sl": old, "new_sl": self.position.current_sl},
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
                        window_start = candle.completion_time
                        self.attempts = 1
                        self.setup = Setup(
                            number=1,
                            trigger=candle,
                            guide_window_start=window_start,
                            guide_window_end=window_start
                            + timedelta(minutes=GUIDE_CANDLE_COUNT),
                        )
                        self.state = "WAIT_G1"
                        self.emit(
                            candle.completion_time,
                            "TRIGGER_VALID",
                            candle.close,
                            {
                                "trigger": candle.details(),
                                "guide_window_start": window_start,
                                "guide_window_end": self.setup.guide_window_end,
                            },
                        )
                elif candle.start.time() == LAST_TRIGGER_START:
                    self._discard_day(
                        candle.completion_time,
                        candle.close,
                        "NO_RED_TRIGGER_IN_FIRST_THREE",
                    )

        self.previous_3m = candle

    def on_one_minute(self, candle: Candle) -> None:
        if self.setup is None or self.position is not None or self.done:
            return
        window_start = self.setup.guide_window_start
        window_end = self.setup.guide_window_end
        if window_start is None or window_end is None:
            return
        if candle.start < window_start:
            return
        if candle.start >= window_end:
            self._discard_day(
                candle.completion_time,
                candle.close,
                "FOUR_1M_WINDOW_EXPIRED",
            )
            return
        if candle.low < self.setup.trigger.low:
            self._discard_day(
                candle.completion_time,
                candle.low,
                "GUIDE_BROKE_TRIGGER_LOW",
            )
            return

        if self.state == "WAIT_G1":
            if candle.green:
                range_fraction = (
                    (candle.high - candle.low) / candle.low
                    if candle.low > 0
                    else float("inf")
                )
                if range_fraction > G1_MAX_RANGE_FRACTION:
                    self._discard_day(
                        candle.completion_time,
                        candle.close,
                        "G1_RANGE_ABOVE_0_20_PERCENT",
                    )
                    return
                self.setup.g1 = candle
                self.setup.g2_start = candle.start + timedelta(minutes=1)
                if self.setup.g2_start >= window_end:
                    self._discard_day(
                        candle.completion_time,
                        candle.close,
                        "G1_TOO_LATE_FOR_G2",
                    )
                    return
                self.state = "WAIT_G2"
                self.emit(
                    candle.completion_time,
                    "G1_VALID",
                    candle.close,
                    {
                        "setup_number": 1,
                        "g1": candle.details(),
                        "range_fraction": range_fraction,
                        "range_percent": range_fraction * 100,
                    },
                )
            elif candle.start + timedelta(minutes=1) >= window_end:
                self._discard_day(
                    candle.completion_time,
                    candle.close,
                    "NO_GREEN_G1_IN_FOUR_1M_CANDLES",
                )
            return

        g1 = self.setup.g1
        if g1 is None:
            return
        if candle.low < g1.low:
            self._discard_day(
                candle.completion_time,
                candle.low,
                f"{self.state}_BROKE_G1_LOW",
            )
            return

        if self.state == "WAIT_G2":
            if candle.start != self.setup.g2_start:
                self._discard_day(
                    candle.completion_time,
                    candle.close,
                    "G2_DATA_GAP",
                )
            elif candle.high == g1.high:
                self.setup.g3_start = candle.start + timedelta(minutes=1)
                if self.setup.g3_start >= window_end:
                    self._discard_day(
                        candle.completion_time,
                        candle.close,
                        "G2_EQUAL_HIGH_NO_ROOM_FOR_G3",
                    )
                else:
                    self.state = "WAIT_G3"
                    self.emit(
                        candle.completion_time,
                        "G2_EQUAL_G1_HIGH",
                        candle.close,
                        {"g1_high": g1.high, "g2": candle.details()},
                    )
            elif candle.high < g1.high:
                self._discard_day(
                    candle.completion_time,
                    candle.close,
                    "G2_DID_NOT_REACH_G1_HIGH",
                )
            else:
                self._discard_day(
                    candle.completion_time,
                    candle.close,
                    "MISSED_G2_INTRAMINUTE_BREAK",
                )
            return

        if self.state == "WAIT_G3":
            outcome = (
                "G3_DATA_GAP"
                if candle.start != self.setup.g3_start
                else "G3_DID_NOT_BREAK_G1_HIGH"
            )
            self._discard_day(candle.completion_time, candle.close, outcome)

    def on_entry_tick(self, timestamp: datetime, price: float) -> Position | None:
        if self.setup is None or self.position is not None or self.done:
            return None
        window_start = self.setup.guide_window_start
        window_end = self.setup.guide_window_end
        if (
            window_start is None
            or window_end is None
            or timestamp < window_start
            or timestamp >= window_end
        ):
            return None
        if price < self.setup.trigger.low:
            self._discard_day(timestamp, price, "GUIDE_BROKE_TRIGGER_LOW")
            return None
        if self.state not in {"WAIT_G2", "WAIT_G3"}:
            return None
        g1 = self.setup.g1
        expected_start = (
            self.setup.g2_start if self.state == "WAIT_G2" else self.setup.g3_start
        )
        if g1 is None or expected_start is None:
            return None
        minute = floor_minute(timestamp)
        if minute != expected_start:
            return None
        if price < g1.low:
            self._discard_day(
                timestamp,
                price,
                f"{self.state}_BROKE_G1_LOW",
            )
            return None
        if price <= g1.high:
            return None

        risk = price - g1.low
        if risk <= 0:
            self._discard_day(timestamp, price, "NON_POSITIVE_RISK")
            return None
        entry_mode = (
            "G2_G1_HIGH_BREAK" if self.state == "WAIT_G2" else "G3_G1_HIGH_BREAK"
        )
        return self._open_position(
            timestamp,
            price,
            g1.low,
            price + TP1_R_MULTIPLE * risk,
            entry_mode,
        )

    def _open_position(
        self,
        timestamp: datetime,
        price: float,
        stop: float,
        target: float,
        entry_mode: str,
    ) -> Position:
        trade_id = f"{timestamp.date().isoformat()}-{self.symbol}-S1"
        self.position = Position(
            trade_id=trade_id,
            symbol=self.symbol,
            setup_number=1,
            entry_time=timestamp,
            entry_price=price,
            initial_sl=stop,
            current_sl=stop,
            tp1_target=target,
            open_quantity=QUANTITY,
            entry_mode=entry_mode,
            g1_low=stop,
        )
        self.state = "POSITION"
        self.setup.outcome = "ENTRY"
        self.emit(
            timestamp,
            "ENTRY_SIGNAL",
            price,
            {
                "trade_id": trade_id,
                "setup_number": 1,
                "quantity": QUANTITY,
                "entry_mode": entry_mode,
                "sl": stop,
                "tp1": target,
                "g1": self.setup.g1.details() if self.setup.g1 else None,
                "outcome": "ENTRY",
            },
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
        position.current_sl = max(position.current_sl, position.entry_price)
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
