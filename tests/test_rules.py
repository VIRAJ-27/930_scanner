import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scanner930.candles import SessionVwap
from scanner930.models import Candle
from scanner930.strategy import ScannerStrategy


IST = ZoneInfo("Asia/Kolkata")


def moment(hhmmss: str) -> datetime:
    return datetime.fromisoformat(f"2026-08-01T{hhmmss}").replace(tzinfo=IST)


def c3(hhmm, o, h, l, close, vwap=99.5):
    return Candle("TEST", 3, moment(f"{hhmm}:00"), o, h, l, close, 100, vwap)


def c1(hhmm, o, h, l, close):
    return Candle("TEST", 1, moment(f"{hhmm}:00"), o, h, l, close, 100)


class StrategyRuleTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.strategy = ScannerStrategy(
            "TEST",
            lambda ts, kind, price, details=None: self.events.append(
                (kind, details or {})
            ),
        )

    def valid_trigger(self, high=100.4, low=100.0):
        self.strategy.on_three_minute(c3("09:27", 99.8, 100.5, 99.7, 100.3))
        self.strategy.on_three_minute(
            c3("09:30", high - 0.05, high, low, high - 0.2)
        )

    def add_g1(self, hhmm="09:33", high=100.5, low=100.1, close=100.45):
        self.strategy.on_one_minute(c1(hhmm, 100.2, high, low, close))

    def test_trigger_below_half_percent_multiplies_range_by_1_4(self):
        self.valid_trigger(100.4, 100.0)
        setup = self.strategy.setup
        self.assertAlmostEqual(setup.trigger_range_fraction, 0.004)
        self.assertAlmostEqual(setup.ep_fraction, 0.0056)
        self.assertAlmostEqual(setup.r1_target, 100.4 * 1.0056)

    def test_trigger_at_or_above_half_percent_adds_point_one_percent(self):
        self.valid_trigger(100.52, 100.0)
        setup = self.strategy.setup
        self.assertAlmostEqual(setup.trigger_range_fraction, 0.0052)
        self.assertAlmostEqual(setup.ep_fraction, 0.0062)
        self.assertAlmostEqual(setup.r1_target, 100.52 * 1.0062)

    def test_first_green_within_next_three_becomes_g1(self):
        self.valid_trigger()
        self.strategy.on_one_minute(c1("09:33", 100.3, 100.35, 100.2, 100.25))
        self.strategy.on_one_minute(c1("09:34", 100.25, 100.45, 100.2, 100.4))
        self.assertEqual(self.strategy.state, "WAIT_ENTRY")
        self.assertEqual(self.strategy.setup.g1.start, moment("09:34:00"))

    def test_no_green_in_three_discards(self):
        self.valid_trigger()
        for hhmm in ["09:33", "09:34", "09:35"]:
            self.strategy.on_one_minute(c1(hhmm, 100.3, 100.35, 100.2, 100.25))
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(
            self.events[-1][1]["outcome"],
            "NO_GREEN_G1_IN_THREE_1M_CANDLES",
        )

    def test_any_of_next_three_can_break_g1_high(self):
        self.valid_trigger()
        self.add_g1()
        self.strategy.on_one_minute(c1("09:34", 100.4, 100.49, 100.2, 100.45))
        position = self.strategy.on_entry_tick(moment("09:35:20"), 100.51)
        self.assertIsNotNone(position)
        self.assertEqual(position.initial_sl, 100.0)
        self.assertEqual(position.entry_mode, "G1_HIGH_BREAK")

    def test_g1_low_break_before_entry_discards(self):
        self.valid_trigger()
        self.add_g1()
        self.strategy.on_entry_tick(moment("09:34:10"), 100.09)
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(
            self.events[-1][1]["outcome"], "ENTRY_CANDLE_BROKE_G1_LOW"
        )

    def test_no_break_in_three_entry_candles_discards(self):
        self.valid_trigger()
        self.add_g1()
        for hhmm in ["09:34", "09:35", "09:36"]:
            self.strategy.on_one_minute(c1(hhmm, 100.4, 100.49, 100.2, 100.45))
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(
            self.events[-1][1]["outcome"],
            "NO_G1_HIGH_BREAK_IN_THREE_CANDLES",
        )

    def test_target_is_minimum_of_r1_and_three_r(self):
        self.valid_trigger()
        self.add_g1()
        position = self.strategy.on_entry_tick(moment("09:34:10"), 100.51)
        expected_r2 = 100.51 + 3 * (100.51 - 100.0)
        self.assertAlmostEqual(position.r2_target, expected_r2)
        self.assertAlmostEqual(
            position.tp1_target, min(self.strategy.setup.r1_target, expected_r2)
        )

    def test_three_minute_close_above_trigger_moves_sl_to_g1_low(self):
        self.valid_trigger()
        self.add_g1(high=100.6, low=100.1, close=100.55)
        position = self.strategy.on_entry_tick(moment("09:34:10"), 100.61)
        self.assertEqual(position.current_sl, 100.0)
        self.strategy.on_three_minute(c3("09:33", 100.2, 100.8, 100.1, 100.5))
        self.assertEqual(position.current_sl, 100.1)

    def test_tp1_moves_stop_to_trigger_high_then_five_minute_trails(self):
        self.valid_trigger()
        self.add_g1()
        position = self.strategy.on_entry_tick(moment("09:34:10"), 100.51)
        self.strategy.mark_tp1_booked(moment("09:40:00"), position.tp1_target)
        self.assertEqual(position.current_sl, 100.4)
        self.strategy.on_five_minute(
            Candle("TEST", 5, moment("09:40:00"), 101, 102, 100.8, 101.5, 100)
        )
        self.assertEqual(position.current_sl, 100.8)
        self.strategy.on_five_minute(
            Candle("TEST", 5, moment("09:45:00"), 101, 102, 100.6, 101.5, 100)
        )
        self.assertEqual(position.current_sl, 100.8)

    def test_first_red_failure_and_no_red_window_discard(self):
        self.strategy.on_three_minute(c3("09:27", 99.8, 100.5, 99.7, 100.3))
        self.strategy.on_three_minute(c3("09:30", 100.4, 100.5, 100, 100.2, 100.3))
        self.assertEqual(self.strategy.state, "DONE")

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
