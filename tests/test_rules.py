import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scanner930.candles import SessionVwap
from scanner930.models import Candle
from scanner930.strategy import ScannerStrategy


IST = ZoneInfo("Asia/Kolkata")


def moment(hhmmss: str) -> datetime:
    return datetime.fromisoformat(f"2026-07-27T{hhmmss}").replace(tzinfo=IST)


def c3(hhmm: str, open_: float, high: float, low: float, close: float, vwap: float):
    return Candle("TEST", 3, moment(f"{hhmm}:00"), open_, high, low, close, 100, vwap)


def c1(hhmm: str, open_: float, high: float, low: float, close: float):
    return Candle("TEST", 1, moment(f"{hhmm}:00"), open_, high, low, close, 100)


class StrategyRuleTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.strategy = ScannerStrategy(
            "TEST",
            lambda ts, kind, price, details=None: self.events.append(
                (kind, details or {})
            ),
        )

    def valid_trigger(self, trigger_time="09:30"):
        previous_time = {"09:30": "09:27", "09:33": "09:30", "09:36": "09:33"}[
            trigger_time
        ]
        self.strategy.on_three_minute(c3(previous_time, 100, 103, 99, 102, 101))
        self.strategy.on_three_minute(
            c3(trigger_time, 103, 104, 101, 102, 101.5)
        )

    def test_first_red_of_first_three_can_be_trigger(self):
        self.strategy.on_three_minute(c3("09:27", 100, 103, 99, 102, 101))
        self.strategy.on_three_minute(c3("09:30", 101, 104, 100, 103, 101))
        self.strategy.on_three_minute(c3("09:33", 104, 105, 102, 103, 102))
        self.assertEqual(self.strategy.state, "WAIT_G1")

    def test_no_red_by_0936_discards_day(self):
        self.strategy.on_three_minute(c3("09:27", 100, 103, 99, 102, 101))
        self.strategy.on_three_minute(c3("09:30", 101, 104, 100, 103, 101))
        self.strategy.on_three_minute(c3("09:33", 102, 105, 101, 104, 102))
        self.strategy.on_three_minute(c3("09:36", 103, 106, 102, 105, 103))
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(self.events[-1][1]["outcome"], "NO_RED_TRIGGER_IN_FIRST_THREE")

    def test_first_red_failure_discards_day(self):
        self.strategy.on_three_minute(c3("09:27", 100, 103, 99, 102, 101))
        self.strategy.on_three_minute(c3("09:30", 103, 104, 101, 102, 102.5))
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(self.events[-1][0], "TRIGGER_REJECTED_FIRST_RED")

    def test_g1_range_equal_020_percent_is_valid(self):
        self.valid_trigger()
        self.strategy.on_one_minute(c1("09:33", 102.0, 102.204, 102.0, 102.1))
        self.assertEqual(self.strategy.state, "WAIT_G2")
        self.assertEqual(self.events[-1][0], "G1_VALID")

    def test_g1_range_above_020_percent_discards(self):
        self.valid_trigger()
        self.strategy.on_one_minute(c1("09:33", 102.0, 102.205, 102.0, 102.1))
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(self.events[-1][1]["outcome"], "G1_RANGE_ABOVE_0_20_PERCENT")

    def test_first_green_within_four_minutes_becomes_g1(self):
        self.valid_trigger()
        self.strategy.on_one_minute(c1("09:33", 102.1, 102.15, 102.0, 102.05))
        self.strategy.on_one_minute(c1("09:34", 102.05, 102.15, 102.0, 102.1))
        self.assertEqual(self.strategy.setup.g1.start, moment("09:34:00"))

    def test_g2_strict_break_enters_at_tick(self):
        self.valid_trigger()
        self.strategy.on_one_minute(c1("09:33", 102.0, 102.2, 102.0, 102.1))
        position = self.strategy.on_entry_tick(moment("09:34:20"), 102.21)
        self.assertIsNotNone(position)
        self.assertEqual(position.entry_mode, "G2_G1_HIGH_BREAK")
        self.assertEqual(position.initial_sl, 102.0)
        self.assertAlmostEqual(position.tp1_target, 102.672)

    def test_g2_equal_then_g3_strict_break_enters(self):
        self.valid_trigger()
        self.strategy.on_one_minute(c1("09:33", 102.0, 102.2, 102.0, 102.1))
        self.strategy.on_one_minute(c1("09:34", 102.1, 102.2, 102.05, 102.15))
        self.assertEqual(self.strategy.state, "WAIT_G3")
        position = self.strategy.on_entry_tick(moment("09:35:10"), 102.21)
        self.assertIsNotNone(position)
        self.assertEqual(position.entry_mode, "G3_G1_HIGH_BREAK")

    def test_g2_below_g1_high_discards(self):
        self.valid_trigger()
        self.strategy.on_one_minute(c1("09:33", 102.0, 102.2, 102.0, 102.1))
        self.strategy.on_one_minute(c1("09:34", 102.1, 102.19, 102.05, 102.15))
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(self.events[-1][1]["outcome"], "G2_DID_NOT_REACH_G1_HIGH")

    def test_g2_break_of_g1_low_discards(self):
        self.valid_trigger()
        self.strategy.on_one_minute(c1("09:33", 102.0, 102.2, 102.0, 102.1))
        self.strategy.on_entry_tick(moment("09:34:10"), 101.99)
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(self.events[-1][1]["outcome"], "WAIT_G2_BROKE_G1_LOW")

    def test_trigger_low_break_in_guide_window_discards(self):
        self.valid_trigger()
        self.strategy.on_entry_tick(moment("09:33:10"), 100.99)
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(self.events[-1][1]["outcome"], "GUIDE_BROKE_TRIGGER_LOW")

    def test_only_red_three_minute_candle_raises_runner_trail(self):
        self.valid_trigger()
        self.strategy.on_one_minute(c1("09:33", 102.0, 102.2, 102.0, 102.1))
        position = self.strategy.on_entry_tick(moment("09:34:10"), 102.21)
        self.strategy.mark_tp1_booked(moment("09:40:00"), position.tp1_target)
        self.assertEqual(position.current_sl, position.entry_price)
        self.strategy.on_three_minute(c3("09:39", 103, 104, 102.3, 103.5, 102))
        self.assertEqual(position.current_sl, position.entry_price)
        self.strategy.on_three_minute(c3("09:42", 104, 105, 103, 103.5, 102))
        self.assertEqual(position.current_sl, 103)

    def test_standard_hlc3_session_vwap(self):
        calculator = SessionVwap()
        first = Candle("TEST", 3, moment("09:15:00"), 9, 12, 6, 9, 100)
        second = Candle("TEST", 3, moment("09:18:00"), 10, 15, 9, 12, 200)
        calculator.apply(first)
        calculator.apply(second)
        expected = (((12 + 6 + 9) / 3) * 100 + ((15 + 9 + 12) / 3) * 200) / 300
        self.assertAlmostEqual(second.vwap, expected)


if __name__ == "__main__":
    unittest.main()
