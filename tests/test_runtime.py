import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scanner930.broker import EquityBroker
from scanner930.instruments import EquityInstrument
from scanner930.models import Candle
from scanner930.runtime import StockRuntime
from scanner930.storage import SQLiteStore


IST = ZoneInfo("Asia/Kolkata")


def dt(hhmmss: str) -> datetime:
    return datetime.fromisoformat(f"2026-07-27T{hhmmss}").replace(tzinfo=IST)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp.name) / "test.db")
        self.broker = EquityBroker(self.store, "PAPER")
        self.runtime = StockRuntime(
            EquityInstrument("TEST", "TEST-EQ", "100"),
            self.store,
            self.broker,
            lambda: True,
        )

    def tearDown(self):
        self.broker.close()
        self.store.close()
        self.temp.cleanup()

    def c(self, hhmm, o, h, l, close, vwap):
        return Candle("TEST", 3, dt(f"{hhmm}:00"), o, h, l, close, 100, vwap)

    def test_100_share_fallback_entry_50_tp1_and_50_runner(self):
        self.runtime.trading_day = dt("09:27:00").date()
        self.runtime.strategy.on_three_minute(self.c("09:27", 100, 103, 99, 102, 101))
        self.runtime.strategy.on_three_minute(self.c("09:30", 103, 104, 101, 102, 101.5))
        self.runtime.strategy.on_three_minute(self.c("09:33", 102, 105, 101, 104, 102))

        self.runtime.on_tick(dt("09:36:01"), 105.1, 1000)
        position = self.runtime.strategy.position
        self.assertEqual(position.open_quantity, 100)
        self.runtime.on_tick(dt("09:36:30"), position.tp1_target, 1010)
        self.assertTrue(position.tp1_touched)
        self.assertFalse(position.tp1_booked)

        minute = Candle(
            "TEST",
            1,
            dt("09:36:00"),
            105.1,
            position.tp1_target + 0.1,
            105.1,
            position.tp1_target + 0.05,
            10,
        )
        self.runtime._on_one_minute(minute)
        self.assertTrue(position.tp1_booked)
        self.assertEqual(position.open_quantity, 50)

        trail_candle = self.c("09:36", 108, 112, 106, 110, 104)
        self.runtime.strategy.on_three_minute(trail_candle)
        self.assertEqual(position.current_sl, 106)
        self.runtime.on_tick(dt("09:39:01"), 105.9, 1020)
        self.assertEqual(position.open_quantity, 0)
        self.assertEqual(position.final_exit_reason, "RUNNER_TRAIL_SL")

    def test_early_entry_books_half_when_c1_already_reached_1_5r(self):
        self.runtime.trading_day = dt("09:27:00").date()
        self.runtime.strategy.on_three_minute(
            self.c("09:27", 100, 103, 99, 102, 101)
        )
        self.runtime.strategy.on_three_minute(
            self.c("09:30", 103, 104, 101, 102, 101.5)
        )
        self.runtime.on_tick(dt("09:33:01"), 104.05, 1000)
        position = self.runtime.strategy.position
        self.assertEqual(position.entry_mode, "TRIGGER_HIGH_BREAK")
        self.assertIsNone(position.tp1_target)
        self.runtime._on_three_minute(
            self.c("09:33", 102, 106, 103, 105, 102)
        )
        self.assertAlmostEqual(position.tp1_target, 105.625)
        self.assertEqual(position.current_sl, 103)
        self.assertTrue(position.tp1_booked)
        self.assertEqual(position.open_quantity, 50)

    def test_live_broker_uses_cash_intraday_order_and_records_fill(self):
        class FakeApi:
            def __init__(self):
                self.params = None

            def placeOrderFullResponse(self, params):
                self.params = params
                return {"status": True, "data": {"orderid": "OID-1"}}

            def orderBook(self):
                return {
                    "data": [
                        {
                            "orderid": "OID-1",
                            "orderstatus": "complete",
                            "averageprice": "105.25",
                        }
                    ]
                }

        api = FakeApi()
        live_broker = EquityBroker(self.store, "LIVE", api)
        instrument = EquityInstrument("TEST", "TEST-EQ", "100")
        live_broker.submit(
            dt("09:36:01"),
            "trade-1",
            instrument,
            "BUY",
            100,
            "C2_C1_HIGH_BREAK",
            105.1,
        )
        live_broker.close()
        self.store.flush()
        connection = sqlite3.connect(self.store.path)
        rows = connection.execute(
            "SELECT status, fill_price FROM orders ORDER BY id"
        ).fetchall()
        connection.close()
        self.assertEqual(api.params["exchange"], "NSE")
        self.assertEqual(api.params["producttype"], "INTRADAY")
        self.assertEqual(api.params["ordertype"], "MARKET")
        self.assertEqual(api.params["symboltoken"], "100")
        self.assertEqual(api.params["quantity"], "100")
        self.assertEqual(rows[-1], ("FILLED", 105.25))


if __name__ == "__main__":
    unittest.main()
