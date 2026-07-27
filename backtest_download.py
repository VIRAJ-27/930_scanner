from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import logzero
import pandas as pd
import pyotp
from dotenv import load_dotenv
from SmartApi import SmartConnect

from scanner930.config import REFERENCE_ENV


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download an incremental one-minute data supplement."
    )
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--from-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--delay", type=float, default=0.6)
    return parser.parse_args()


def login() -> SmartConnect:
    env_path = Path(".env") if Path(".env").exists() else REFERENCE_ENV
    load_dotenv(env_path)
    required = {
        "API_KEY": os.getenv("API_KEY"),
        "CLIENT_CODE": os.getenv("CLIENT_CODE"),
        "PIN": os.getenv("PIN"),
        "TOTP_SECRET": os.getenv("TOTP_SECRET"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise ValueError(f"Missing values in {env_path}: {', '.join(missing)}")
    logzero.loglevel(logging.CRITICAL)
    api = SmartConnect(api_key=str(required["API_KEY"]))
    response = api.generateSession(
        str(required["CLIENT_CODE"]),
        str(required["PIN"]),
        pyotp.TOTP(str(required["TOTP_SECRET"])).now(),
    )
    if not response or not response.get("status"):
        raise RuntimeError("Angel One login failed; response was suppressed.")
    return api


def main() -> None:
    args = parse_args()
    start = datetime.fromisoformat(args.from_date).replace(hour=9, minute=15)
    end = datetime.fromisoformat(args.to_date).replace(hour=15, minute=30)
    if end < start:
        raise ValueError("to-date must be on or after from-date.")

    universe = pd.read_csv(args.universe, dtype=str).fillna("")
    universe = universe[
        (universe["Symbol"].str.strip() != "")
        & (universe["Equity Token"].str.strip() != "")
    ].copy()
    universe = universe.drop_duplicates("Symbol").sort_values("Symbol")
    args.output.mkdir(parents=True, exist_ok=True)
    api = login()
    failures: list[dict[str, str]] = []
    summaries: list[dict[str, object]] = []

    print(
        f"Downloading {len(universe)} F&O stocks "
        f"from {start:%Y-%m-%d} to {end:%Y-%m-%d}.",
        flush=True,
    )
    for sequence, (_, row) in enumerate(universe.iterrows(), start=1):
        symbol = str(row["Symbol"]).strip().upper()
        token = str(row["Equity Token"]).strip()
        exchange = str(row["Exchange"]).strip().upper() or "NSE"
        params = {
            "exchange": exchange,
            "symboltoken": token,
            "interval": "ONE_MINUTE",
            "fromdate": start.strftime("%Y-%m-%d %H:%M"),
            "todate": end.strftime("%Y-%m-%d %H:%M"),
        }
        last_error = ""
        frame = pd.DataFrame()
        for attempt in range(1, 5):
            try:
                response = api.getCandleData(params)
                if not response or not response.get("status"):
                    raise RuntimeError(str((response or {}).get("message") or "request failed"))
                rows = response.get("data") or []
                frame = pd.DataFrame(
                    rows,
                    columns=["Datetime", "Open", "High", "Low", "Close", "Volume"],
                )
                break
            except Exception as error:
                last_error = f"{type(error).__name__}: {error}"
                if attempt < 4:
                    time.sleep(3 * attempt)
        if frame.empty:
            failures.append({"Symbol": symbol, "Reason": last_error or "No data"})
            print(f"[{sequence}/{len(universe)}] {symbol}: no data", flush=True)
        else:
            frame["Datetime"] = pd.to_datetime(frame["Datetime"], utc=True)
            frame = (
                frame.sort_values("Datetime")
                .drop_duplicates("Datetime", keep="last")
                .reset_index(drop=True)
            )
            path = args.output / f"{safe_name(symbol)}_1m.csv"
            frame.to_csv(path, index=False)
            summaries.append(
                {
                    "Symbol": symbol,
                    "Rows": len(frame),
                    "First": frame["Datetime"].min().isoformat(),
                    "Last": frame["Datetime"].max().isoformat(),
                }
            )
            print(
                f"[{sequence}/{len(universe)}] {symbol}: "
                f"{len(frame):,} rows through {frame['Datetime'].max()}",
                flush=True,
            )
        time.sleep(args.delay)

    pd.DataFrame(summaries).to_csv(args.output / "DownloadSummary.csv", index=False)
    pd.DataFrame(failures, columns=["Symbol", "Reason"]).to_csv(
        args.output / "FailedDownloads.csv",
        index=False,
    )
    print(
        f"Download complete: {len(summaries)} successful, "
        f"{len(failures)} failed.",
        flush=True,
    )


def safe_name(symbol: str) -> str:
    result = symbol
    for character in '<>:"/\\|?*':
        result = result.replace(character, "_")
    return result


if __name__ == "__main__":
    main()

