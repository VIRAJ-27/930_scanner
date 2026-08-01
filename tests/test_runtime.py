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
    return datetime.fromisoformat(f"2026-08-01T{hhmmss}").replace(tzinfo=IST)


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

    def c3(self, hhmm, o, h, l, close, vwap=99.5):
        return Candle("TEST", 3, dt(f"{hhmm}:00"), o, h, l, close, 100, vwap)

    def prepare_entry(self):
        strategy = self.runtime.strategy
        strategy.on_three_minute(self.c3("09:27", 99.8, 100.5, 99.7, 100.3))
        strategy.on_three_minute(self.c3("09:30", 100.35, 100.4, 100, 100.2))
        strategy.on_one_minute(
            Candle("TEST", 1, dt("09:33:00"), 100.2, 100.5, 100.1, 100.45, 10)
        )
        return strategy.on_entry_tick(dt("09:34:10"), 100.51)

    def test_tp1_books_70_at_close_and_moves_30_runner_to_trigger_high(self):
        position = self.prepare_entry()
        self.runtime.trading_day = dt("09:34:00").date()
        self.runtime.on_tick(dt("09:40:20"), position.tp1_target, 1000)
        self.runtime._on_one_minute(
            Candle(
                "TEST", 1, dt("09:40:00"), position.tp1_target - 0.1,
                position.tp1_target + 0.1, position.tp1_target - 0.2,
                position.tp1_target + 0.05, 10,
            )
        )
        self.assertTrue(position.tp1_booked)
        self.assertEqual(position.open_quantity, 30)
        self.assertEqual(position.current_sl, 100.4)

    def test_five_minute_trail_and_immediate_runner_stop(self):
        position = self.prepare_entry()
        self.runtime.trading_day = dt("09:34:00").date()
        self.runtime.strategy.mark_tp1_booked(dt("09:40:00"), position.tp1_target)
        self.runtime._on_five_minute(
            Candle("TEST", 5, dt("09:40:00"), 101, 102, 100.8, 101.5, 100)
        )
        self.assertEqual(position.current_sl, 100.8)
        self.runtime.on_tick(dt("09:45:10"), 100.79, 1010)
        self.assertEqual(position.open_quantity, 0)
        self.assertEqual(position.final_exit_reason, "RUNNER_TRAIL_SL")

    def test_live_broker_uses_cash_intraday_order_and_records_fill(self):
        class FakeApi:
            def __init__(self):
                self.params = None

            def placeOrderFullResponse(self, params):
                self.params = params
                return {"status": True, "data": {"orderid": "OID-1"}}

            def orderBook(self):
                return {"data": [{"orderid": "OID-1", "orderstatus": "complete", "averageprice": "105.25"}]}

        api = FakeApi()
        live_broker = EquityBroker(self.store, "LIVE", api)
        live_broker.submit(
            dt("09:36:01"), "trade-1", EquityInstrument("TEST", "TEST-EQ", "100"),
            "BUY", 100, "G1_HIGH_BREAK", 105.1,
        )
        live_broker.close()
        self.store.flush()
        connection = sqlite3.connect(self.store.path)
        rows = connection.execute("SELECT status, fill_price FROM orders ORDER BY id").fetchall()
        connection.close()
        self.assertEqual(api.params["exchange"], "NSE")
        self.assertEqual(api.params["producttype"], "INTRADAY")
        self.assertEqual(api.params["quantity"], "100")
        self.assertEqual(rows[-1], ("FILLED", 105.25))


if __name__ == "__main__":
    unittest.main()
