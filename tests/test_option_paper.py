import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from scanner930.option_paper import (
    OptionCatalog,
    OptionPaperExecutor,
    OptionQuote,
    SmartApiOptionQuoteProvider,
)
from scanner930.storage import SQLiteStore


IST = ZoneInfo("Asia/Kolkata")


def instrument(symbol, token, strike, expiry="27AUG2026", lot=100):
    return {
        "exch_seg": "NFO",
        "instrumenttype": "OPTSTK",
        "symbol": symbol,
        "name": "TEST",
        "token": token,
        "expiry": expiry,
        "strike": str(strike * 100),
        "lotsize": str(lot),
    }


class FixedQuoteProvider:
    def __init__(self, bid=9.8, ask=10.0, ltp=9.9):
        self.bid = bid
        self.ask = ask
        self.ltp = ltp

    def get_quote(self, option):
        return OptionQuote(
            option.token,
            datetime(2026, 8, 3, 9, 34, 10, tzinfo=IST),
            self.ltp,
            self.bid,
            self.ask,
        )


class OptionPaperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp.name) / "option.db")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_dense_strikes_use_mathematical_nearest(self):
        catalog = OptionCatalog([
            instrument("TEST27AUG26200CE", "200", 200),
            instrument("TEST27AUG26205CE", "205", 205),
        ])
        self.assertEqual(
            catalog.select_ce("TEST", 202, date(2026, 8, 3)).strike,
            200,
        )
        self.assertEqual(
            catalog.select_ce("TEST", 203, date(2026, 8, 3)).strike,
            205,
        )

    def test_wide_strikes_use_seventy_five_percent_threshold(self):
        catalog = OptionCatalog([
            instrument("TEST27AUG261130CE", "1130", 1130),
            instrument("TEST27AUG261150CE", "1150", 1150),
        ])
        self.assertEqual(
            catalog.select_ce("TEST", 1141, date(2026, 8, 3)).strike,
            1130,
        )
        self.assertEqual(
            catalog.select_ce("TEST", 1146, date(2026, 8, 3)).strike,
            1150,
        )

    def test_nearest_unexpired_month_is_selected(self):
        catalog = OptionCatalog([
            instrument("TEST27AUG26200CE", "aug", 200, "27AUG2026"),
            instrument("TEST24SEP26200CE", "sep", 200, "24SEP2026"),
        ])
        self.assertEqual(
            catalog.select_ce("TEST", 200, date(2026, 8, 3)).token,
            "aug",
        )
        self.assertEqual(
            catalog.select_ce("TEST", 200, date(2026, 8, 28)).token,
            "sep",
        )

    def test_full_quote_uses_best_bid_and_ask(self):
        class FakeApi:
            def getMarketData(self, mode, tokens):
                self.request = (mode, tokens)
                return {
                    "data": {
                        "fetched": [{
                            "ltp": 10.1,
                            "exchFeedTime": "03-Aug-2026 09:34:10",
                            "depth": {
                                "buy": [{"price": 10.0}],
                                "sell": [{"price": 10.2}],
                            },
                        }]
                    }
                }

        api = FakeApi()
        provider = SmartApiOptionQuoteProvider(api)
        option = OptionCatalog([
            instrument("TEST27AUG26200CE", "200", 200),
        ]).by_token["200"]
        quote = provider.get_quote(option)
        self.assertEqual(api.request, ("FULL", {"NFO": ["200"]}))
        self.assertEqual((quote.bid, quote.ask, quote.ltp), (10.0, 10.2, 10.1))

    def test_option_pnl_uses_ask_entry_and_bid_exits(self):
        catalog = OptionCatalog([
            instrument("TEST27AUG26200CE", "200", 200, lot=100),
        ])
        provider = FixedQuoteProvider(bid=9.8, ask=10.0, ltp=9.9)
        executor = OptionPaperExecutor(catalog, provider, self.store)
        stock = SimpleNamespace(
            trade_id="2026-08-03-TEST-S1",
            symbol="TEST",
            entry_tier="SILVER",
            entry_price=200.1,
            initial_sl=198.0,
            tp1_target=204.0,
        )
        timestamp = datetime(2026, 8, 3, 9, 34, 10, tzinfo=IST)
        self.assertTrue(executor.open_position(timestamp, stock))
        provider.bid = 14.0
        provider.ask = 14.2
        provider.ltp = 14.1
        self.assertTrue(executor.book_tp1(stock.trade_id, timestamp, 204.0))
        provider.bid = 8.0
        provider.ask = 8.2
        provider.ltp = 8.1
        self.assertTrue(
            executor.close_position(stock.trade_id, timestamp, 202.0, "RUNNER_TRAIL_SL")
        )
        self.store.flush()
        connection = sqlite3.connect(self.store.path)
        row = connection.execute(
            "SELECT realized_pnl, option_return_percent, status, final_quantity "
            "FROM option_paper_trades"
        ).fetchone()
        connection.close()
        self.assertEqual(row[0], 220.0)
        self.assertAlmostEqual(row[1], 22.0)
        self.assertEqual(row[2], "CLOSED")
        self.assertAlmostEqual(row[3], 30.0)

    def test_wide_spread_rejects_option_entry(self):
        catalog = OptionCatalog([
            instrument("TEST27AUG26200CE", "200", 200),
        ])
        executor = OptionPaperExecutor(
            catalog,
            FixedQuoteProvider(bid=9.0, ask=10.0, ltp=9.5),
            self.store,
        )
        stock = SimpleNamespace(
            trade_id="rejected",
            symbol="TEST",
            entry_tier="NORMAL",
            entry_price=200.1,
            initial_sl=198.0,
            tp1_target=204.0,
        )
        timestamp = datetime(2026, 8, 3, 9, 34, 10, tzinfo=IST)
        self.assertFalse(executor.open_position(timestamp, stock))
        self.store.flush()
        connection = sqlite3.connect(self.store.path)
        status = connection.execute(
            "SELECT status FROM option_paper_trades WHERE trade_id='rejected'"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(status, "REJECTED")

    def test_stale_quote_rejects_option_entry(self):
        catalog = OptionCatalog([
            instrument("TEST27AUG26200CE", "200", 200),
        ])
        executor = OptionPaperExecutor(catalog, FixedQuoteProvider(), self.store)
        stock = SimpleNamespace(
            trade_id="stale",
            symbol="TEST",
            entry_tier="NORMAL",
            entry_price=200.1,
            initial_sl=198.0,
            tp1_target=204.0,
        )
        timestamp = datetime(2026, 8, 3, 9, 34, 30, tzinfo=IST)

        self.assertFalse(executor.open_position(timestamp, stock))
        self.store.flush()
        connection = sqlite3.connect(self.store.path)
        status, error = connection.execute(
            "SELECT status, error FROM option_paper_trades WHERE trade_id='stale'"
        ).fetchone()
        connection.close()
        self.assertEqual(status, "REJECTED")
        self.assertIn("stale option quote", error)


if __name__ == "__main__":
    unittest.main()
