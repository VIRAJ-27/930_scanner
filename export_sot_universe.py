from __future__ import annotations

import argparse
import csv
from pathlib import Path

from scanner930.config import INSTRUMENT_MASTER_PATH
from scanner930.instruments import EquityCatalog, download_instrument_master


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the current F&O stock universe for SOT SETUP."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("LiveData/SOT_FO_Universe.csv"),
    )
    args = parser.parse_args()

    catalog = EquityCatalog(download_instrument_master(INSTRUMENT_MASTER_PATH))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Symbol", "Trading Symbol", "Equity Token", "Exchange"])
        for instrument in sorted(catalog.by_name.values(), key=lambda item: item.name):
            writer.writerow(
                [instrument.name, instrument.trading_symbol, instrument.token,
                 instrument.exchange]
            )
    print(f"Exported {len(catalog.by_name)} current F&O stocks to {args.output}.")


if __name__ == "__main__":
    main()

