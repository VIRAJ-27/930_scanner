from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import logzero
import pyotp
from dotenv import load_dotenv
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from .config import (
    NSE_CASH_EXCHANGE_TYPE,
    PRICE_DIVISOR,
    REFERENCE_ENV,
    TIMEZONE,
    WEBSOCKET_MODE_QUOTE,
)
from .instruments import EquityCatalog
from .runtime import LiveTradingSystem


class AngelSession:
    def __init__(self, env_path: Path | None = None):
        chosen = env_path or (Path(".env") if Path(".env").exists() else REFERENCE_ENV)
        load_dotenv(chosen)
        required = {
            "API_KEY": os.getenv("API_KEY"),
            "CLIENT_CODE": os.getenv("CLIENT_CODE"),
            "PIN": os.getenv("PIN"),
            "TOTP_SECRET": os.getenv("TOTP_SECRET"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(
                f"Missing broker values in {chosen}: " + ", ".join(missing)
            )
        self.api_key = str(required["API_KEY"])
        self.client_code = str(required["CLIENT_CODE"])
        self.pin = str(required["PIN"])
        self.totp_secret = str(required["TOTP_SECRET"])
        self.smart_api: SmartConnect | None = None
        self.auth_token = ""
        self.feed_token = ""

    def login(self) -> None:
        logzero.loglevel(logging.CRITICAL)
        api = SmartConnect(api_key=self.api_key)
        response = api.generateSession(
            self.client_code,
            self.pin,
            pyotp.TOTP(self.totp_secret).now(),
        )
        if not response or not response.get("status"):
            raise RuntimeError("Angel One login failed; credentials were not logged.")
        data = response.get("data") or {}
        self.auth_token = str(data.get("jwtToken") or "")
        self.feed_token = str(api.getfeedToken() or "")
        if not self.auth_token or not self.feed_token:
            raise RuntimeError("Angel One did not return WebSocket tokens.")
        self.smart_api = api


class AngelMarketData:
    def __init__(
        self,
        session: AngelSession,
        catalog: EquityCatalog,
        system: LiveTradingSystem,
    ):
        self.session = session
        self.catalog = catalog
        self.system = system
        self.websocket: SmartWebSocketV2 | None = None
        self.connected = threading.Event()
        self.last_error = ""
        self.ist = ZoneInfo(TIMEZONE)

    def connect(self) -> None:
        websocket = SmartWebSocketV2(
            self.session.auth_token,
            self.session.api_key,
            self.session.client_code,
            self.session.feed_token,
            max_retry_attempt=20,
            retry_strategy=1,
            retry_delay=2,
            retry_multiplier=2,
            retry_duration=240,
        )
        self.websocket = websocket
        websocket.on_open = self._on_open
        websocket.on_data = self._on_data
        websocket.on_error = self._on_error
        websocket.on_close = self._on_close
        websocket.connect()

    def close(self) -> None:
        if self.websocket is not None:
            self.websocket.close_connection()

    def _on_open(self, _wsapp) -> None:
        tokens = sorted(self.catalog.by_token)
        self.websocket.subscribe(
            "scanner930-stocks",
            WEBSOCKET_MODE_QUOTE,
            [{"exchangeType": NSE_CASH_EXCHANGE_TYPE, "tokens": tokens}],
        )
        self.connected.set()
        print(f"Connected: monitoring {len(tokens)} current F&O stocks.")

    def _on_data(self, _wsapp, packet: dict) -> None:
        try:
            token = str(packet.get("token", ""))
            instrument = self.catalog.by_token.get(token)
            if instrument is None:
                return
            timestamp_ms = int(packet.get("exchange_timestamp") or 0)
            timestamp = (
                datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
                .astimezone(self.ist)
                if timestamp_ms > 0
                else datetime.now(self.ist)
            )
            price = float(packet.get("last_traded_price") or 0) / PRICE_DIVISOR
            if price <= 0:
                return
            self.system.on_tick(
                {
                    "exchange_ts": timestamp,
                    "received_ts": datetime.now(self.ist),
                    "token": token,
                    "symbol": instrument.name,
                    "price": price,
                    "cumulative_volume": packet.get("volume_trade_for_the_day"),
                    "sequence_number": packet.get("sequence_number"),
                }
            )
        except Exception as error:
            self.last_error = f"{type(error).__name__}: {error}"
            print("Tick processing error:", self.last_error)

    def _on_error(self, *_args) -> None:
        self.last_error = "Angel WebSocket error; automatic reconnect is active."
        print(self.last_error)

    def _on_close(self, *_args) -> None:
        self.connected.clear()
        print("Angel WebSocket closed.")

