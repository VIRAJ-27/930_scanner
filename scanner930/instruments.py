from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import INSTRUMENT_MASTER_URL


@dataclass(frozen=True)
class EquityInstrument:
    name: str
    trading_symbol: str
    token: str
    exchange: str = "NSE"


def download_instrument_master(path: Path) -> list[dict[str, Any]]:
    import requests

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and datetime.fromtimestamp(path.stat().st_mtime).date() == datetime.now().date():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(cached, list) and cached:
            return cached
    try:
        response = requests.get(
            INSTRUMENT_MASTER_URL,
            headers={"Accept": "application/json", "User-Agent": "Scanner930/1.0"},
            timeout=60,
        )
        response.raise_for_status()
        master = response.json()
        if not isinstance(master, list) or not master:
            raise ValueError("Instrument master is empty.")
        path.write_text(json.dumps(master), encoding="utf-8")
        return master
    except Exception:
        if not path.exists():
            raise
        cached = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cached, list) or not cached:
            raise ValueError("Cached instrument master is invalid.")
        return cached


class EquityCatalog:
    """Map the current stock F&O underlyings to their NSE cash instruments."""

    def __init__(self, master: list[dict[str, Any]]):
        fo_names = {
            str(item.get("name", "")).strip().upper()
            for item in master
            if str(item.get("exch_seg", "")).strip().upper() == "NFO"
            and str(item.get("instrumenttype", "")).strip().upper()
            in {"FUTSTK", "OPTSTK"}
        }
        self.by_token: dict[str, EquityInstrument] = {}
        self.by_name: dict[str, EquityInstrument] = {}
        for item in master:
            exchange = str(item.get("exch_seg", "")).strip().upper()
            symbol = str(item.get("symbol", "")).strip().upper()
            name = str(item.get("name", "")).strip().upper()
            token = str(item.get("token", "")).strip()
            if (
                exchange != "NSE"
                or not symbol.endswith("-EQ")
                or name not in fo_names
                or not token
                or "TEST" in name
                or "TEST" in symbol
            ):
                continue
            instrument = EquityInstrument(name, symbol, token)
            self.by_token[token] = instrument
            self.by_name[name] = instrument
        if not self.by_token:
            raise ValueError("No NSE cash instruments mapped for F&O stocks.")
