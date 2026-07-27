from __future__ import annotations

import queue
import threading
from datetime import datetime
from typing import Any

from .instruments import EquityInstrument
from .storage import SQLiteStore


class EquityBroker:
    """Paper fills immediately; live requests are submitted on a worker."""

    def __init__(
        self,
        store: SQLiteStore,
        mode: str,
        smart_api: Any = None,
    ):
        self.store = store
        self.mode = mode.upper()
        self.smart_api = smart_api
        self.orders: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.monitor: threading.Thread | None = None
        self.monitor_stop = threading.Event()
        self.api_lock = threading.Lock()
        self.pending: dict[str, dict[str, Any]] = {}
        self.pending_lock = threading.Lock()
        if self.mode == "LIVE":
            if smart_api is None:
                raise ValueError("Live broker requires an authenticated SmartAPI client.")
            self.worker = threading.Thread(target=self._run, daemon=True)
            self.worker.start()
            self.monitor = threading.Thread(
                target=self._monitor_orders,
                daemon=True,
            )
            self.monitor.start()

    def submit(
        self,
        timestamp: datetime,
        trade_id: str,
        instrument: EquityInstrument,
        side: str,
        quantity: int,
        reason: str,
        signal_price: float,
    ) -> None:
        order = {
            "requested_ts": timestamp,
            "updated_ts": timestamp,
            "trade_id": trade_id,
            "symbol": instrument.name,
            "trading_symbol": instrument.trading_symbol,
            "token": instrument.token,
            "side": side,
            "quantity": quantity,
            "reason": reason,
            "mode": self.mode,
            "signal_price": signal_price,
            "status": "FILLED" if self.mode == "PAPER" else "QUEUED",
            "fill_price": signal_price if self.mode == "PAPER" else None,
        }
        self.store.save_order(order)
        if self.mode == "LIVE":
            self.orders.put(order)

    def close(self) -> None:
        if self.worker is not None:
            self.orders.put(None)
            self.worker.join(60)
            if self.worker.is_alive():
                raise RuntimeError("Timed out while draining live broker orders.")
            self._refresh_pending()
            self.monitor_stop.set()
            if self.monitor is not None:
                self.monitor.join(10)

    def _run(self) -> None:
        while True:
            order = self.orders.get()
            if order is None:
                return
            try:
                params = {
                    "variety": "NORMAL",
                    "tradingsymbol": order["trading_symbol"],
                    "symboltoken": "",
                    "transactiontype": order["side"],
                    "exchange": "NSE",
                    "ordertype": "MARKET",
                    "producttype": "INTRADAY",
                    "duration": "DAY",
                    "price": "0",
                    "squareoff": "0",
                    "stoploss": "0",
                    "quantity": str(order["quantity"]),
                }
                # Resolve token from the trading symbol is not necessary for
                # paper mode. Live mode injects it before the queue in runtime.
                params["symboltoken"] = order["token"]
                with self.api_lock:
                    response = self.smart_api.placeOrderFullResponse(params)
                data = (response or {}).get("data") or {}
                order["broker_order_id"] = str(
                    data.get("orderid") or data.get("uniqueorderid") or ""
                )
                order["status"] = (
                    "SUBMITTED"
                    if response and response.get("status") and order["broker_order_id"]
                    else "REJECTED"
                )
                order["error"] = None if order["status"] == "SUBMITTED" else str(
                    (response or {}).get("message") or "Unknown broker rejection"
                )
                if order["status"] == "SUBMITTED":
                    with self.pending_lock:
                        self.pending[order["broker_order_id"]] = order
            except Exception as error:
                order["status"] = "ERROR"
                order["error"] = f"{type(error).__name__}: {error}"
            order["updated_ts"] = datetime.now(order["requested_ts"].tzinfo)
            self.store.save_order(order)

    def _monitor_orders(self) -> None:
        while not self.monitor_stop.wait(1.0):
            self._refresh_pending()

    def _refresh_pending(self) -> None:
        with self.pending_lock:
            pending_ids = set(self.pending)
        if not pending_ids:
            return
        try:
            with self.api_lock:
                response = self.smart_api.orderBook() or {}
            rows = response.get("data") or []
        except Exception:
            return
        by_id = {
            str(row.get("orderid") or ""): row
            for row in rows
            if str(row.get("orderid") or "") in pending_ids
        }
        completed = []
        for order_id, match in by_id.items():
            with self.pending_lock:
                order = self.pending.get(order_id)
            if order is None:
                continue
            status = str(
                match.get("orderstatus") or match.get("status") or ""
            ).upper()
            if status in {"COMPLETE", "COMPLETED", "FILLED"}:
                order["status"] = "FILLED"
                try:
                    order["fill_price"] = float(match.get("averageprice") or 0) or None
                except (TypeError, ValueError):
                    order["fill_price"] = None
                completed.append(order_id)
            elif status in {"REJECTED", "CANCELLED"}:
                order["status"] = status
                order["error"] = str(match.get("text") or match.get("message") or "")
                completed.append(order_id)
            else:
                continue
            order["updated_ts"] = datetime.now(order["requested_ts"].tzinfo)
            self.store.save_order(order)
        if completed:
            with self.pending_lock:
                for order_id in completed:
                    self.pending.pop(order_id, None)
