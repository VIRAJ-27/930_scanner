from __future__ import annotations

import argparse

from scanner930.angel import AngelSession
from scanner930.config import INSTRUMENT_MASTER_PATH
from scanner930.instruments import EquityCatalog, download_instrument_master
from scanner930.notifications import NotificationService
from scanner930.option_paper import OptionCatalog


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate option-paper VPS setup.")
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args()

    session = AngelSession()
    session.login()
    master = download_instrument_master(INSTRUMENT_MASTER_PATH)
    equities = EquityCatalog(master)
    options = OptionCatalog(master)
    covered = len(set(equities.by_name) & set(options.by_underlying))
    notifier = NotificationService()
    print("Angel One login: OK")
    print(f"Underlying stocks: {len(equities.by_name)}")
    print(f"Stocks with CE contracts: {covered}")
    print(f"CE contracts: {len(options.by_token)}")
    print("Telegram:", "configured" if notifier.telegram_enabled else "not configured")
    print("Email:", "configured" if notifier.email_enabled else "not configured")
    if args.notify:
        notifier.send_text(
            f"Scanner930 VPS doctor OK: {covered} stocks have CE contracts."
        )
    notifier.close()
    if covered == 0:
        raise RuntimeError("No stock CE contracts mapped from the instrument master.")
    if notifier.last_error:
        raise RuntimeError(notifier.last_error)


if __name__ == "__main__":
    main()
