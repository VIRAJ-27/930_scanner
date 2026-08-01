from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .config import (
    OPTION_MAX_QUOTE_AGE_SECONDS,
    OPTION_MAX_SPREAD_FRACTION,
    OPTION_PAPER_LOTS,
    OPTION_TP1_FRACTION,
    OPTION_WIDE_STRIKE_MIN_GAP,
    OPTION_WIDE_STRIKE_UPPER_THRESHOLD,
    TIMEZONE,
)


@dataclass(frozen=True)
class OptionInstrument:
    underlying: str
    trading_symbol: str
    token: str
    expiry: date
    strike: float
    lot_size: int
    option_type: str = "CE"
    exchange: str = "NFO"


@dataclass(frozen=True)
class OptionQuote:
    token: str
    quote_time: datetime
    ltp: float
    bid: float
    ask: float

    @property
    def spread_fraction(self) -> float:
        midpoint = (self.bid + self.ask) / 2.0
        return (self.ask - self.bid) / midpoint if midpoint > 0 else float("inf")


@dataclass
class OptionPaperPosition:
    trade_id: str
    underlying: str
    entry_tier: str
    instrument: OptionInstrument
    paper_lots: int
    paper_quantity: float
    entry_time: datetime
    underlying_entry: float
    option_entry_price: float
    entry_quote: OptionQuote
    tp1_quantity: float
    remaining_quantity: float
    tp1_exit_time: datetime | None = None
    tp1_option_price: float | None = None
    final_exit_time: datetime | None = None
    final_option_price: float | None = None
    final_exit_reason: str | None = None


class OptionCatalog:
    """Current-month stock call options parsed from Angel's instrument master."""

    def __init__(self, master: list[dict[str, Any]]):
        self.by_underlying: dict[str, list[OptionInstrument]] = {}
        self.by_token: dict[str, OptionInstrument] = {}
        for item in master:
            if str(item.get("exch_seg", "")).strip().upper() != "NFO":
                continue
            if str(item.get("instrumenttype", "")).strip().upper() != "OPTSTK":
                continue
            symbol = str(item.get("symbol", "")).strip().upper()
            if not symbol.endswith("CE"):
                continue
            underlying = str(item.get("name", "")).strip().upper()
            token = str(item.get("token", "")).strip()
            expiry = _parse_expiry(item.get("expiry"))
            strike = _parse_master_strike(item.get("strike"))
            lot_size = _positive_int(item.get("lotsize"))
            if not all((underlying, token, expiry, strike > 0, lot_size > 0)):
                continue
            instrument = OptionInstrument(
                underlying=underlying,
                trading_symbol=symbol,
                token=token,
                expiry=expiry,
                strike=strike,
                lot_size=lot_size,
            )
            self.by_token[token] = instrument
            self.by_underlying.setdefault(underlying, []).append(instrument)
        for instruments in self.by_underlying.values():
            instruments.sort(key=lambda item: (item.expiry, item.strike))

    def select_ce(
        self,
        underlying: str,
        stock_price: float,
        trading_day: date,
    ) -> OptionInstrument | None:
        available = [
            item
            for item in self.by_underlying.get(underlying.upper(), [])
            if item.expiry >= trading_day
        ]
        if not available or stock_price <= 0:
            return None
        nearest_expiry = min(item.expiry for item in available)
        expiry_options = [item for item in available if item.expiry == nearest_expiry]
        by_strike = {item.strike: item for item in expiry_options}
        strikes = sorted(by_strike)
        if stock_price <= strikes[0]:
            return by_strike[strikes[0]]
        if stock_price >= strikes[-1]:
            return by_strike[strikes[-1]]

        lower = max(strike for strike in strikes if strike <= stock_price)
        upper = min(strike for strike in strikes if strike >= stock_price)
        if lower == upper:
            return by_strike[lower]
        gap = upper - lower
        progress = (stock_price - lower) / gap
        if gap >= OPTION_WIDE_STRIKE_MIN_GAP:
            selected = upper if progress >= OPTION_WIDE_STRIKE_UPPER_THRESHOLD else lower
        else:
            lower_distance = stock_price - lower
            upper_distance = upper - stock_price
            selected = lower if lower_distance <= upper_distance else upper
        return by_strike[selected]


class SmartApiOptionQuoteProvider:
    """Fetch best bid/ask using SmartAPI FULL market data mode."""

    def __init__(self, smart_api: Any):
        self.smart_api = smart_api
        self.lock = threading.Lock()
        self.last_request_at = 0.0
        self.ist = ZoneInfo(TIMEZONE)

    def get_quote(self, instrument: OptionInstrument) -> OptionQuote:
        with self.lock:
            delay = 0.11 - (time.monotonic() - self.last_request_at)
            if delay > 0:
                time.sleep(delay)
            response = self.smart_api.getMarketData(
                "FULL",
                {"NFO": [instrument.token]},
            )
            self.last_request_at = time.monotonic()
        fetched = (((response or {}).get("data") or {}).get("fetched") or [])
        if not fetched:
            raise RuntimeError("SmartAPI returned no option quote.")
        row = fetched[0]
        depth = row.get("depth") or {}
        bid = _first_depth_price(depth.get("buy") or row.get("buy") or [])
        ask = _first_depth_price(depth.get("sell") or row.get("sell") or [])
        ltp = _positive_float(row.get("ltp"))
        quote_time = _parse_quote_time(
            row.get("exchFeedTime") or row.get("exchTradeTime"),
            self.ist,
        )
        if min(bid, ask, ltp) <= 0 or ask < bid:
            raise RuntimeError("Option quote has invalid bid/ask/LTP values.")
        if quote_time is None:
            raise RuntimeError("Option quote has no exchange timestamp.")
        return OptionQuote(instrument.token, quote_time, ltp, bid, ask)


class OptionPaperExecutor:
    """Paper option fills; underlying strategy remains the only exit authority."""

    def __init__(
        self,
        catalog: OptionCatalog,
        quote_provider: SmartApiOptionQuoteProvider,
        store,
        notify: Callable[[str], None] | None = None,
        paper_lots: int = OPTION_PAPER_LOTS,
    ):
        if paper_lots <= 0:
            raise ValueError("OPTION_PAPER_LOTS must be positive.")
        self.catalog = catalog
        self.quote_provider = quote_provider
        self.store = store
        self.notify = notify or (lambda _message: None)
        self.paper_lots = paper_lots
        self.positions: dict[str, OptionPaperPosition] = {}
        self.pending_tp1: dict[str, float] = {}
        self.pending_closes: dict[str, tuple[float, str]] = {}
        self.next_retry_at = 0.0
        self._load_open_positions()

    @property
    def open_position_count(self) -> int:
        return len(self.positions)

    @property
    def realized_pnl(self) -> float:
        return self.store.option_realized_pnl()

    def open_position(self, timestamp: datetime, stock_position) -> bool:
        instrument = self.catalog.select_ce(
            stock_position.symbol,
            stock_position.entry_price,
            timestamp.date(),
        )
        if instrument is None:
            self._reject(timestamp, stock_position, "NO_ELIGIBLE_CE_CONTRACT")
            return False
        try:
            quote = self._validated_quote(instrument, timestamp)
        except Exception as error:
            self._reject(
                timestamp,
                stock_position,
                "OPTION_ENTRY_QUOTE_REJECTED",
                f"{type(error).__name__}: {error}",
                instrument,
            )
            return False
        paper_quantity = float(instrument.lot_size * self.paper_lots)
        tp1_quantity = paper_quantity * OPTION_TP1_FRACTION
        position = OptionPaperPosition(
            trade_id=stock_position.trade_id,
            underlying=stock_position.symbol,
            entry_tier=stock_position.entry_tier,
            instrument=instrument,
            paper_lots=self.paper_lots,
            paper_quantity=paper_quantity,
            entry_time=timestamp,
            underlying_entry=stock_position.entry_price,
            option_entry_price=quote.ask,
            entry_quote=quote,
            tp1_quantity=tp1_quantity,
            remaining_quantity=paper_quantity,
        )
        self.positions[position.trade_id] = position
        self.store.insert_option_trade(position, stock_position)
        self.store.save_event(
            timestamp,
            position.underlying,
            "OPTION_PAPER_ENTRY",
            stock_position.entry_price,
            self._entry_details(position),
        )
        self.notify(
            f"OPTION PAPER ENTRY | {position.underlying} | {position.entry_tier}\n"
            f"Underlying entry: \u20b9{stock_position.entry_price:.2f}\n"
            f"Contract: {instrument.trading_symbol}\n"
            f"Paper fill (best ask): \u20b9{quote.ask:.2f}\n"
            f"Quantity: {position.paper_quantity:g} "
            f"({self.paper_lots} lot-equivalent)\n"
            f"Underlying SL: \u20b9{stock_position.initial_sl:.2f}\n"
            f"Underlying TP1: \u20b9{stock_position.tp1_target:.2f}"
        )
        return True

    def book_tp1(
        self,
        trade_id: str,
        timestamp: datetime,
        underlying_price: float,
    ) -> bool:
        position = self.positions.get(trade_id)
        if position is None or position.tp1_exit_time is not None:
            return False
        try:
            quote = self._validated_quote(position.instrument, timestamp)
        except Exception as error:
            first_failure = trade_id not in self.pending_tp1
            self.pending_tp1[trade_id] = underlying_price
            if first_failure:
                self._quote_failure(position, timestamp, "TP1", error)
            return False
        position.tp1_exit_time = timestamp
        position.tp1_option_price = quote.bid
        position.remaining_quantity -= position.tp1_quantity
        self.store.update_option_tp1(position, underlying_price, quote)
        self.pending_tp1.pop(trade_id, None)
        self.notify(
            f"OPTION TP1 {position.underlying} | bid ₹{quote.bid:.2f} | "
            f"underlying ₹{underlying_price:.2f} | 70% booked"
        )
        return True

    def close_position(
        self,
        trade_id: str,
        timestamp: datetime,
        underlying_price: float,
        reason: str,
    ) -> bool:
        position = self.positions.get(trade_id)
        if position is None:
            return False
        try:
            quote = self._validated_quote(position.instrument, timestamp)
        except Exception as error:
            first_failure = trade_id not in self.pending_closes
            self.pending_closes[trade_id] = (underlying_price, reason)
            if first_failure:
                self._quote_failure(position, timestamp, "FINAL_EXIT", error)
            return False
        position.final_exit_time = timestamp
        position.final_option_price = quote.bid
        position.final_exit_reason = reason
        self.store.close_option_trade(position, underlying_price, quote)
        self.positions.pop(trade_id, None)
        self.pending_tp1.pop(trade_id, None)
        self.pending_closes.pop(trade_id, None)
        self.notify(
            f"OPTION EXIT {position.underlying} | bid ₹{quote.bid:.2f} | "
            f"underlying ₹{underlying_price:.2f} | {reason}"
        )
        return True

    def advance_clock(self, now: datetime) -> None:
        if time.monotonic() < self.next_retry_at:
            return
        self.next_retry_at = time.monotonic() + 10.0
        for trade_id, (price, reason) in list(self.pending_closes.items()):
            self.close_position(trade_id, now, price, reason)
        for trade_id, price in list(self.pending_tp1.items()):
            if trade_id not in self.pending_closes:
                self.book_tp1(trade_id, now, price)

    def reconcile_underlying_tick(
        self,
        symbol: str,
        timestamp: datetime,
        underlying_price: float,
        active_trade_id: str | None,
    ) -> None:
        for trade_id, position in list(self.positions.items()):
            if (
                position.underlying != symbol
                or trade_id == active_trade_id
                or trade_id in self.pending_closes
            ):
                continue
            self.close_position(
                trade_id,
                timestamp,
                underlying_price,
                "RECOVERY_FIRST_UNDERLYING_QUOTE",
            )

    def _validated_quote(
        self,
        instrument: OptionInstrument,
        now: datetime,
    ) -> OptionQuote:
        quote = self.quote_provider.get_quote(instrument)
        age = abs((now - quote.quote_time).total_seconds())
        if age > OPTION_MAX_QUOTE_AGE_SECONDS:
            raise RuntimeError(f"stale option quote ({age:.1f}s old)")
        if quote.spread_fraction > OPTION_MAX_SPREAD_FRACTION:
            raise RuntimeError(
                f"spread {quote.spread_fraction:.2%} exceeds "
                f"{OPTION_MAX_SPREAD_FRACTION:.2%}"
            )
        return quote

    def _load_open_positions(self) -> None:
        for row in self.store.load_open_option_trades():
            instrument = self.catalog.by_token.get(str(row["option_token"]))
            if instrument is None:
                continue
            quote_time = datetime.fromisoformat(row["entry_quote_time"])
            quote = OptionQuote(
                instrument.token,
                quote_time,
                float(row["entry_option_ltp"]),
                float(row["entry_option_bid"]),
                float(row["entry_option_ask"]),
            )
            position = OptionPaperPosition(
                trade_id=row["trade_id"],
                underlying=row["symbol"],
                entry_tier=row["entry_tier"],
                instrument=instrument,
                paper_lots=int(row["paper_lots"]),
                paper_quantity=float(row["paper_quantity"]),
                entry_time=datetime.fromisoformat(row["entry_time"]),
                underlying_entry=float(row["underlying_entry_price"]),
                option_entry_price=float(row["entry_option_ask"]),
                entry_quote=quote,
                tp1_quantity=float(row["tp1_quantity"]),
                remaining_quantity=float(row["remaining_quantity"]),
                tp1_exit_time=(
                    datetime.fromisoformat(row["tp1_exit_time"])
                    if row["tp1_exit_time"]
                    else None
                ),
                tp1_option_price=row["tp1_option_bid"],
            )
            self.positions[position.trade_id] = position

    def _reject(
        self,
        timestamp: datetime,
        stock_position,
        outcome: str,
        error: str = "",
        instrument: OptionInstrument | None = None,
    ) -> None:
        details = {
            "trade_id": stock_position.trade_id,
            "outcome": outcome,
            "error": error,
            "stock_entry_price": stock_position.entry_price,
            "option_symbol": instrument.trading_symbol if instrument else "",
        }
        self.store.save_event(
            timestamp,
            stock_position.symbol,
            "OPTION_PAPER_ENTRY_REJECTED",
            stock_position.entry_price,
            details,
        )
        self.store.insert_option_rejection(timestamp, stock_position, details)
        self.notify(
            f"OPTION ENTRY REJECTED {stock_position.symbol} | {outcome}"
        )

    def _quote_failure(
        self,
        position: OptionPaperPosition,
        timestamp: datetime,
        action: str,
        error: Exception,
    ) -> None:
        message = f"{type(error).__name__}: {error}"
        self.store.save_event(
            timestamp,
            position.underlying,
            "OPTION_EXIT_QUOTE_FAILED",
            None,
            {"trade_id": position.trade_id, "action": action, "error": message},
        )
        self.notify(
            f"OPTION QUOTE FAILURE {position.underlying} | {action} | {message}"
        )

    @staticmethod
    def _entry_details(position: OptionPaperPosition) -> dict[str, Any]:
        return {
            "trade_id": position.trade_id,
            "entry_tier": position.entry_tier,
            "option_symbol": position.instrument.trading_symbol,
            "option_token": position.instrument.token,
            "expiry": position.instrument.expiry,
            "strike": position.instrument.strike,
            "lot_size": position.instrument.lot_size,
            "paper_lots": position.paper_lots,
            "paper_quantity": position.paper_quantity,
            "entry_option_ask": position.option_entry_price,
            "entry_option_bid": position.entry_quote.bid,
            "spread_percent": position.entry_quote.spread_fraction * 100,
        }


def _parse_master_strike(value: Any) -> float:
    parsed = _positive_float(value)
    return parsed / 100.0 if parsed > 0 else 0.0


def _parse_expiry(value: Any) -> date | None:
    text = str(value or "").strip().upper()
    for pattern in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _parse_quote_time(value: Any, timezone: ZoneInfo) -> datetime | None:
    text = str(value or "").strip()
    for pattern in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=timezone)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=timezone) if parsed.tzinfo is None else parsed.astimezone(timezone)
    except ValueError:
        return None


def _first_depth_price(rows: Any) -> float:
    if not isinstance(rows, list) or not rows:
        return 0.0
    return _positive_float((rows[0] or {}).get("price"))


def _positive_float(value: Any) -> float:
    try:
        result = float(value or 0)
        return result if result > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _positive_int(value: Any) -> int:
    try:
        result = int(float(value or 0))
        return result if result > 0 else 0
    except (TypeError, ValueError):
        return 0
