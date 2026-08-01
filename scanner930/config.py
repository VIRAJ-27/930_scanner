from __future__ import annotations

import os
from datetime import time
from pathlib import Path


TIMEZONE = "Asia/Kolkata"
MARKET_OPEN = time(9, 15)
TRIGGER_START = time(9, 30)
LAST_TRIGGER_START = time(9, 36)
MARKET_EXIT = time(15, 15)

QUANTITY = 100
TP1_QUANTITY = 70
RUNNER_QUANTITY = 30
TP1_R_MULTIPLE = 3.0
MAX_SETUPS_PER_STOCK = 1
G1_SEARCH_CANDLE_COUNT = 3
ENTRY_SEARCH_CANDLE_COUNT = 1
TRIGGER_RANGE_THRESHOLD = 0.005
TRIGGER_RANGE_MULTIPLIER = 1.4
TRIGGER_RANGE_ADDEND = 0.001

# Entry-quality tiers. EMA values use completed candles only.
EMA_PERIOD = 20
NORMAL_EMA_3M_LOOKBACK_BARS = 2
NORMAL_EMA_3M_MIN_RISE = 0.0001
SILVER_EMA_1M_LOOKBACK_BARS = 5
SILVER_EMA_1M_MIN_RISE = 0.00116
SILVER_G1_MIN_BODY_FRACTION = 0.579

DATA_DIR = Path("LiveData")
REPORT_DIR = Path("LiveReports")
DATABASE_PATH = DATA_DIR / "scanner930.db"
INSTRUMENT_MASTER_PATH = DATA_DIR / "OpenAPIScripMaster.json"
INSTRUMENT_MASTER_URL = (
    "https://margincalculator.angelone.in/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)

# If this project has no local .env, reuse the credentials from the user's
# existing VBOS project. SCANNER_BROKER_ENV can override this on any machine.
REFERENCE_ENV = Path(
    os.getenv(
        "SCANNER_BROKER_ENV",
        r"C:\Users\Admin\Documents\Codex\2026-07-25\he\work"
        r"\VBOS_Backtester\VBOS_Backtester-main\.env",
    )
)

NSE_CASH_EXCHANGE_TYPE = 1
WEBSOCKET_MODE_QUOTE = 2
PRICE_DIVISOR = 100.0
REPORT_REFRESH_SECONDS = 30
DATABASE_FLUSH_SECONDS = 1.0
SINGLE_INSTANCE_PORT = 29330

# Real cash-equity orders require both command-line confirmation and this
# untracked approval file. Paper mode is always the default.
LIVE_APPROVAL_PHRASE = "LIVE_EQUITY_ORDERS"
LIVE_APPROVAL_FILE = Path("LIVE_APPROVAL.txt")
