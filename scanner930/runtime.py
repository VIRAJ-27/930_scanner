from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable

from .broker import EquityBroker
from .candles import CandleAggregator, SessionVwap, floor_time
from .config import (
    MARKET_EXIT,
    QUANTITY,
    RUNNER_QUANTITY,
    TP1_QUANTITY,
)
from .instruments import EquityCatalog, EquityInstrument
from .storage import SQLiteStore
from .strategy import ScannerStrategy


class StockRuntime:
    def __init__(
        self,
        instrument: EquityInstrument,
        store: SQLiteStore,
        broker: EquityBroker,
        should_record: Callable[[], bool],
    ):
        self.instrument = instrument
        self.symbol = instrument.name
        self.store = store
        self.broker = broker
        self.should_record = should_record
        self.lock = threading.RLock()
        self.strategy = ScannerStrategy(self.symbol, self._emit)
        self.one_minute = CandleAggregator(self.symbol, 1, self._on_one_minute)
        self.three_minute = CandleAggregator(self.symbol, 3, self._on_three_minute)
        self.five_minute = CandleAggregator(self.symbol, 5, self._on_five_minute)
        self.vwap = SessionVwap()
        self.trading_day = None
        self.last_price: float | None = None
        self.closed_pnl = 0.0

    @property
    def position_open(self) -> bool:
        position = self.strategy.position
        return position is not None and position.open_quantity > 0

    def reset_day(self, trading_day) -> None:
        self.trading_day = trading_day
        self.strategy = ScannerStrategy(self.symbol, self._emit)
        self.vwap = SessionVwap()
        self.closed_pnl = 0.0

    def on_tick(
        self,
        timestamp: datetime,
        price: float,
        cumulative_volume: int | None,
    ) -> None:
        with self.lock:
            if self.trading_day != timestamp.date():
                self.reset_day(timestamp.date())
            self.last_price = price

            # Higher-timeframe completions run before the 1m close callback.
            self.three_minute.update(timestamp, price, cumulative_volume)
            self.five_minute.update(timestamp, price, cumulative_volume)
            self.one_minute.update(timestamp, price, cumulative_volume)

            if self.position_open:
                self._manage_position_tick(timestamp, price)
                return

            position = self.strategy.on_entry_tick(timestamp, price)
            if position is None:
                return
            if self.should_record():
                self.store.insert_trade(position)
                if position.tp1_touched:
                    self.store.update_tp1_touch(position)
                self.broker.submit(
                    timestamp,
                    position.trade_id,
                    self.instrument,
                    "BUY",
                    QUANTITY,
                    position.entry_mode,
                    price,
                )

    def advance_clock(self, now: datetime) -> None:
        with self.lock:
            self.three_minute.advance_clock(now)
            self.five_minute.advance_clock(now)
            self.one_minute.advance_clock(now)
            if (
                self.position_open
                and now.time() >= MARKET_EXIT
                and self.last_price is not None
            ):
                self._close_position(now, self.last_price, "MARKET_EXIT_1515")

    def _on_three_minute(self, candle) -> None:
        self.vwap.apply(candle)
        if self.should_record():
            self.store.save_candle(candle)
        self.strategy.on_three_minute(candle)

    def _on_five_minute(self, candle) -> None:
        if self.should_record():
            self.store.save_candle(candle)
        self.strategy.on_five_minute(candle)

    def _on_one_minute(self, candle) -> None:
        if self.should_record():
            self.store.save_candle(candle)
        self.strategy.on_one_minute(candle)
        position = self.strategy.position
        if position is None or position.open_quantity <= 0:
            return

        if (
            position.tp1_touched
            and not position.tp1_booked
            and position.tp1_minute == candle.start
        ):
            self._book_tp1(
                candle.completion_time,
                candle.close,
                "TP1_1M_CLOSE",
            )

    def _book_tp1(
        self,
        timestamp: datetime,
        price: float,
        reason: str,
    ) -> None:
        position = self.strategy.position
        if (
            position is None
            or position.open_quantity <= 0
            or position.tp1_booked
        ):
            return
        self.strategy.mark_tp1_booked(timestamp, price)
        if self.should_record():
            self.store.update_tp1(position)
            self.broker.submit(
                timestamp,
                position.trade_id,
                self.instrument,
                "SELL",
                TP1_QUANTITY,
                reason,
                price,
            )
        self._emit(
            timestamp,
            "TP1_EXECUTED",
            price,
            {
                "trade_id": position.trade_id,
                "quantity": TP1_QUANTITY,
                "remaining_quantity": RUNNER_QUANTITY,
                "runner_sl": position.current_sl,
                "reason": reason,
            },
        )

    def _manage_position_tick(self, timestamp: datetime, price: float) -> None:
        position = self.strategy.position
        if position is None:
            return

        # Stop wins over target on the same sequential tick.
        if price <= position.current_sl:
            reason = "RUNNER_TRAIL_SL" if position.tp1_booked else "INITIAL_SL"
            self._close_position(timestamp, price, reason)
            return

        if (
            position.tp1_target is not None
            and not position.tp1_touched
            and price >= position.tp1_target
        ):
            position.tp1_touched = True
            position.tp1_touch_time = timestamp
            position.tp1_minute = floor_time(timestamp, 1)
            self.strategy.tp1_ever_hit = True
            if self.should_record():
                self.store.update_tp1_touch(position)
            self._emit(
                timestamp,
                "TP1_TOUCHED",
                price,
                {
                    "trade_id": position.trade_id,
                    "target": position.tp1_target,
                    "execution_minute": position.tp1_minute,
                },
            )

    def _close_position(
        self,
        timestamp: datetime,
        price: float,
        reason: str,
    ) -> None:
        position = self.strategy.position
        if position is None or position.open_quantity <= 0:
            return
        quantity = position.open_quantity
        tp1_pnl = (
            (position.tp1_exit_price - position.entry_price) * TP1_QUANTITY
            if position.tp1_exit_price is not None
            else 0.0
        )
        self.closed_pnl += (
            tp1_pnl
            + (price - position.entry_price) * quantity
        )
        self.strategy.mark_closed(timestamp, price, reason)
        if self.should_record():
            self.broker.submit(
                timestamp,
                position.trade_id,
                self.instrument,
                "SELL",
                quantity,
                reason,
                price,
            )
            self.store.close_trade(position)
        self._emit(
            timestamp,
            "POSITION_CLOSED",
            price,
            {
                "trade_id": position.trade_id,
                "quantity": quantity,
                "reason": reason,
            },
        )

    def _emit(
        self,
        timestamp: datetime,
        event_type: str,
        price: float | None,
        details: dict | None = None,
    ) -> None:
        if self.should_record():
            self.store.save_event(timestamp, self.symbol, event_type, price, details)
        if event_type in {
            "TRIGGER_VALID",
            "TRIGGER_REJECTED_FIRST_RED",
            "G1_VALID",
            "SETUP_FAILED",
            "ENTRY_SIGNAL",
            "TP1_TOUCHED_AT_ENTRY",
            "TP1_TOUCHED",
            "TP1_EXECUTED",
            "SL_MOVED_TO_G1_LOW",
            "TRAIL_RAISED_5M",
            "POSITION_CLOSED",
        }:
            print(
                f"{timestamp:%H:%M:%S} {self.symbol:<14} "
                f"{event_type:<18} {'' if price is None else f'{price:.2f}'}"
            )


class LiveTradingSystem:
    def __init__(
        self,
        catalog: EquityCatalog,
        store: SQLiteStore,
        broker: EquityBroker,
    ):
        self.catalog = catalog
        self.store = store
        self.broker = broker
        self.replaying = False
        self.last_tick_ts: datetime | None = None
        self.runtimes = {
            token: StockRuntime(
                instrument,
                store,
                broker,
                should_record=lambda: not self.replaying,
            )
            for token, instrument in catalog.by_token.items()
        }

    @property
    def open_position_count(self) -> int:
        return sum(runtime.position_open for runtime in self.runtimes.values())

    @property
    def realized_pnl(self) -> float:
        total = 0.0
        for runtime in self.runtimes.values():
            total += runtime.closed_pnl
            position = runtime.strategy.position
            if (
                position is None
                or position.open_quantity <= 0
            ):
                continue
            if position.tp1_exit_price is not None:
                total += (
                    position.tp1_exit_price - position.entry_price
                ) * TP1_QUANTITY
        return round(total, 2)

    def on_tick(self, tick: dict) -> None:
        timestamp = tick["exchange_ts"]
        self.last_tick_ts = timestamp
        if not self.replaying:
            self.store.save_tick(tick)
        runtime = self.runtimes.get(str(tick["token"]))
        if runtime is not None:
            runtime.on_tick(
                timestamp,
                float(tick["price"]),
                tick.get("cumulative_volume"),
            )

    def advance_clock(self, now: datetime) -> None:
        for runtime in self.runtimes.values():
            runtime.advance_clock(now)

    def close_all(self, now: datetime, reason: str) -> None:
        for runtime in self.runtimes.values():
            if runtime.position_open and runtime.last_price is not None:
                runtime._close_position(now, runtime.last_price, reason)
