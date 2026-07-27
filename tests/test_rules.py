import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scanner930.candles import SessionVwap
from scanner930.models import Candle
from scanner930.strategy import ScannerStrategy


IST = ZoneInfo("Asia/Kolkata")


def moment(hhmm: str) -> datetime:
    return datetime.fromisoformat(f"2026-07-27T{hhmm}:00").replace(tzinfo=IST)


def candle(
    hhmm: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    vwap: float,
) -> Candle:
    return Candle("TEST", 3, moment(hhmm), open_, high, low, close, 100, vwap)


class StrategyRuleTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.strategy = ScannerStrategy(
            "TEST",
            lambda ts, kind, price, details=None: self.events.append(
                (kind, details or {})
            ),
        )

    def valid_setup(self, trigger_time="09:30", c1_time="09:33"):
        self.strategy.on_three_minute(candle("09:27", 100, 103, 99, 102, 101))
        self.strategy.on_three_minute(
            candle(trigger_time, 103, 104, 101, 102, 101.5)
        )
        self.strategy.on_three_minute(
            candle(c1_time, 102, 105, 101, 104, 102)
        )

    def test_trigger_c1_and_strict_c2_high_break_enter(self):
        self.valid_setup()
        self.assertEqual(self.strategy.attempts, 1)
        self.assertIsNone(self.strategy.on_entry_tick(moment("09:36"), 105.0))
        position = self.strategy.on_entry_tick(
            moment("09:36").replace(second=1),
            105.05,
        )
        self.assertIsNotNone(position)
        self.assertEqual(position.initial_sl, 101)
        self.assertAlmostEqual(position.tp1_target, 111.125)
        self.assertEqual(position.open_quantity, 100)
        self.assertEqual(position.entry_mode, "C1_HIGH_BREAK")

    def test_trigger_high_break_enters_before_c1_close(self):
        self.strategy.on_three_minute(candle("09:27", 100, 103, 99, 102, 101))
        self.strategy.on_three_minute(candle("09:30", 103, 104, 101, 102, 101.5))
        position = self.strategy.on_entry_tick(
            moment("09:33").replace(second=1),
            104.05,
        )
        self.assertIsNotNone(position)
        self.assertEqual(position.initial_sl, 101)
        self.assertIsNone(position.tp1_target)
        self.assertTrue(position.awaiting_c1_close)
        self.strategy.on_three_minute(
            candle("09:33", 102, 106, 103, 105, 102)
        )
        self.assertEqual(position.current_sl, 103)
        self.assertAlmostEqual(position.tp1_target, 105.625)
        self.assertTrue(position.tp1_due_at_c1_close)

    def test_c2_low_break_first_consumes_attempt(self):
        self.valid_setup()
        position = self.strategy.on_entry_tick(
            moment("09:36").replace(second=1),
            100.99,
        )
        self.assertIsNone(position)
        self.assertEqual(self.strategy.attempts, 1)
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(self.events[-1][1]["outcome"], "C2_BROKE_C1_LOW_FIRST")

    def test_c2_close_without_entry_consumes_attempt(self):
        self.valid_setup()
        self.strategy.on_three_minute(candle("09:36", 104, 105, 102, 104.5, 102))
        self.assertEqual(self.strategy.attempts, 1)
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(self.events[-1][1]["outcome"], "C2_NO_C1_HIGH_BREAK")

    def test_invalid_c1_does_not_count(self):
        self.strategy.on_three_minute(candle("09:27", 100, 103, 99, 102, 101))
        self.strategy.on_three_minute(candle("09:30", 103, 104, 101, 102, 101.5))
        self.strategy.on_three_minute(candle("09:33", 102, 103, 100, 101, 101))
        self.assertEqual(self.strategy.attempts, 0)
        self.assertEqual(self.strategy.state, "DONE")

    def test_0951_trigger_can_enter_later(self):
        self.strategy.on_three_minute(candle("09:48", 100, 103, 99, 102, 101))
        self.strategy.on_three_minute(candle("09:51", 103, 104, 101, 102, 101.5))
        self.strategy.on_three_minute(candle("09:54", 102, 105, 101, 104, 102))
        position = self.strategy.on_entry_tick(
            moment("09:57").replace(second=1),
            105.1,
        )
        self.assertIsNotNone(position)

    def test_trigger_close_must_be_inside_previous_range(self):
        self.strategy.on_three_minute(candle("09:27", 100, 101.5, 99, 101, 100))
        self.strategy.on_three_minute(candle("09:30", 103, 104, 102, 102.5, 101))
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(self.events[-1][0], "TRIGGER_REJECTED_FIRST_RED")

    def test_first_red_failure_discards_stock_for_day(self):
        self.strategy.on_three_minute(candle("09:27", 100, 103, 99, 102, 101))
        self.strategy.on_three_minute(candle("09:30", 103, 104, 101, 102, 102.5))
        self.assertEqual(self.strategy.state, "DONE")
        self.strategy.on_three_minute(candle("09:33", 101, 104, 100, 103, 102))
        self.strategy.on_three_minute(candle("09:36", 104, 105, 102, 103, 102.5))
        self.assertEqual(self.strategy.state, "DONE")
        self.assertIsNone(self.strategy.setup)

    def test_green_candles_are_skipped_until_first_red_candidate(self):
        self.strategy.on_three_minute(candle("09:27", 99, 102, 98, 101, 100))
        self.strategy.on_three_minute(candle("09:30", 101, 104, 100, 103, 101))
        self.assertEqual(self.strategy.state, "SEARCH_TRIGGER")
        self.strategy.on_three_minute(candle("09:33", 104, 105, 102, 103, 102))
        self.assertEqual(self.strategy.state, "WAIT_C1")

    def test_standard_hlc3_session_vwap(self):
        calculator = SessionVwap()
        first = Candle("TEST", 3, moment("09:15"), 9, 12, 6, 9, 100)
        second = Candle("TEST", 3, moment("09:18"), 10, 15, 9, 12, 200)
        calculator.apply(first)
        calculator.apply(second)
        expected = (((12 + 6 + 9) / 3) * 100 + ((15 + 9 + 12) / 3) * 200) / 300
        self.assertAlmostEqual(second.vwap, expected)

    def test_one_counted_failure_ends_stock_for_day(self):
        self.valid_setup()
        self.strategy.on_three_minute(candle("09:36", 103, 104, 102, 103.5, 102))
        self.assertEqual(self.strategy.attempts, 1)
        self.assertEqual(self.strategy.state, "DONE")

    def test_tp1_touch_prevents_another_setup_even_if_trade_closes(self):
        self.valid_setup()
        position = self.strategy.on_entry_tick(
            moment("09:36").replace(second=1),
            105.1,
        )
        self.strategy.tp1_ever_hit = True
        self.strategy.mark_closed(
            moment("09:37").replace(second=1),
            position.initial_sl,
            "INITIAL_SL",
        )
        self.assertEqual(self.strategy.state, "DONE")


if __name__ == "__main__":
    unittest.main()
