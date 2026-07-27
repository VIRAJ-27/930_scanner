from __future__ import annotations

from datetime import datetime
from typing import Callable

from .models import Candle


def floor_time(timestamp: datetime, minutes: int) -> datetime:
    minute = timestamp.minute - timestamp.minute % minutes
    return timestamp.replace(minute=minute, second=0, microsecond=0)


class CandleAggregator:
    """Build chronological OHLCV candles from LTP and cumulative volume."""

    def __init__(
        self,
        symbol: str,
        minutes: int,
        on_complete: Callable[[Candle], None],
    ):
        self.symbol = symbol
        self.minutes = minutes
        self.on_complete = on_complete
        self.current: Candle | None = None
        self.last_cumulative_volume: int | None = None
        self.trading_day = None

    def update(
        self,
        timestamp: datetime,
        price: float,
        cumulative_volume: int | None,
    ) -> None:
        if self.trading_day != timestamp.date():
            self.trading_day = timestamp.date()
            self.last_cumulative_volume = None

        volume_delta = 0
        if cumulative_volume is not None:
            current_volume = int(cumulative_volume)
            if self.last_cumulative_volume is not None:
                volume_delta = max(0, current_volume - self.last_cumulative_volume)
            elif timestamp.hour == 9 and timestamp.minute == 15:
                volume_delta = max(0, current_volume)
            self.last_cumulative_volume = current_volume

        bucket = floor_time(timestamp, self.minutes)
        if self.current is not None and bucket > self.current.start:
            self._complete()

        if self.current is None:
            self.current = Candle(
                symbol=self.symbol,
                minutes=self.minutes,
                start=bucket,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume_delta,
            )
            return

        # Persisted out-of-order packets must not rewrite completed bars.
        if bucket < self.current.start:
            return
        self.current.high = max(self.current.high, price)
        self.current.low = min(self.current.low, price)
        self.current.close = price
        self.current.volume += volume_delta

    def advance_clock(self, now: datetime) -> None:
        if self.current is not None and now >= self.current.completion_time:
            self._complete()

    def _complete(self) -> None:
        candle = self.current
        self.current = None
        if candle is not None:
            self.on_complete(candle)


class SessionVwap:
    """Candle-based session VWAP using HLC3 typical price and volume."""

    def __init__(self):
        self.trading_day = None
        self.cumulative_pv = 0.0
        self.cumulative_volume = 0

    def apply(self, candle: Candle) -> float | None:
        if self.trading_day != candle.start.date():
            self.trading_day = candle.start.date()
            self.cumulative_pv = 0.0
            self.cumulative_volume = 0
        typical_price = (candle.high + candle.low + candle.close) / 3.0
        self.cumulative_pv += typical_price * candle.volume
        self.cumulative_volume += candle.volume
        candle.vwap = (
            self.cumulative_pv / self.cumulative_volume
            if self.cumulative_volume > 0
            else None
        )
        return candle.vwap

