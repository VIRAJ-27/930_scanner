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
        self.strategy.on_three_minute(c3("09:21", 99.2, 99.7, 99.2, 99.6))
        self.strategy.on_three_minute(c3("09:24", 99.6, 100.0, 99.5, 99.9))
        self.strategy.on_three_minute(
            c3(
                "09:27",
                high - 0.40,
                high + 0.05,
                high - 0.45,
                high - 0.10,
            )
        )
        self.strategy.on_three_minute(
            c3("09:30", high - 0.05, high, low, high - 0.2)
        )

    def valid_trigger_with_large_green(self, high=100.4, low=100.0):
        self.strategy.on_three_minute(
            c3("09:21", 99.2, 100.0, 99.3, 99.9)
        )
        self.strategy.on_three_minute(c3("09:24", 99.6, 100.0, 99.5, 99.9))
        self.strategy.on_three_minute(
            c3(
                "09:27",
                high - 0.45,
                high + 0.05,
                high - 0.50,
                high - 0.10,
            )
        )
        self.strategy.on_three_minute(
            c3("09:30", high - 0.05, high, low, high - 0.2)
        )

    def add_g1(self, hhmm="09:33", high=100.5, low=100.1, close=100.45):
        self.strategy.on_one_minute(c1(hhmm, 100.2, high, low, close))

    def valid_0933_trigger(self, seed_volume=False):
        if seed_volume:
            for day in ["2026-07-27", "2026-07-28", "2026-07-29"]:
                start = datetime.fromisoformat(f"{day}T09:33:00").replace(tzinfo=IST)
                self.strategy.seed_three_minute_volume(start, 100)
            for minute in range(20):
                start = datetime.fromisoformat("2026-07-31T14:00:00").replace(
                    tzinfo=IST
                )
                self.strategy.seed_three_minute_volume(start, 100)
        self.strategy.on_three_minute(c3("09:21", 99.2, 99.7, 99.2, 99.6))
        self.strategy.on_three_minute(c3("09:24", 99.6, 100.0, 99.5, 99.9))
        self.strategy.on_three_minute(c3("09:27", 99.9, 100.3, 99.8, 100.2))
        self.strategy.on_three_minute(c3("09:30", 100.0, 100.5, 99.9, 100.35))
        self.strategy.on_three_minute(c3("09:33", 100.3, 100.4, 100.0, 100.2))

    def open_golden_g1(self, seed_volume=False):
        self.valid_0933_trigger(seed_volume=seed_volume)
        self.add_g1("09:36", high=100.50, low=100.31, close=100.48)
        return self.strategy.on_entry_tick(moment("09:37:10"), 100.51)

    def test_trigger_below_half_percent_multiplies_range_by_1_4(self):
        self.valid_trigger(100.4, 100.0)
        setup = self.strategy.setup
        self.assertAlmostEqual(setup.trigger_range_fraction, 0.004)
        self.assertAlmostEqual(setup.ep_fraction, 0.0056)
        self.assertAlmostEqual(setup.r1_target, 100.4 * 1.0056)

    def test_trigger_below_point_two_percent_multiplies_range_by_two(self):
        self.valid_trigger(100.15, 100.0)
        setup = self.strategy.setup
        self.assertAlmostEqual(setup.trigger_range_fraction, 0.0015)
        self.assertAlmostEqual(setup.ep_fraction, 0.003)
        self.assertAlmostEqual(setup.r1_target, 100.15 * 1.003)

    def test_trigger_at_or_above_half_percent_adds_point_one_percent(self):
        self.valid_trigger(100.52, 100.0)
        setup = self.strategy.setup
        self.assertAlmostEqual(setup.trigger_range_fraction, 0.0052)
        self.assertAlmostEqual(setup.ep_fraction, 0.0062)
        self.assertAlmostEqual(setup.r1_target, 100.52 * 1.0062)

    def test_trigger_above_point_six_percent_is_rejected(self):
        self.valid_trigger(100.61, 100.0)
        self.assertEqual(self.strategy.state, "DONE")
        self.assertIn(
            "TRIGGER_RANGE_ABOVE_0_60_PERCENT",
            self.events[-1][1]["reasons"],
        )

    def test_pretrigger_green_above_point_eight_five_percent_discards(self):
        self.strategy.on_three_minute(c3("09:21", 100.0, 100.86, 100.0, 100.8))
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(
            self.events[-1][1]["outcome"],
            "PRETRIGGER_GREEN_RANGE_ABOVE_0_85_PERCENT",
        )

    def test_large_green_route_threshold_is_strictly_above_point_five_five(self):
        self.strategy.on_three_minute(c3("09:21", 100.0, 100.55, 100.0, 100.5))
        self.assertIsNone(self.strategy.large_pretrigger_green)
        self.strategy.on_three_minute(c3("09:24", 100.0, 100.56, 100.0, 100.5))
        self.assertIsNotNone(self.strategy.large_pretrigger_green)

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

    def test_only_next_candle_can_break_g1_high(self):
        self.valid_trigger()
        self.add_g1()
        position = self.strategy.on_entry_tick(moment("09:34:20"), 100.51)
        self.assertIsNotNone(position)
        self.assertEqual(position.initial_sl, 100.0)
        self.assertEqual(position.entry_mode, "G1_HIGH_BREAK")
        self.assertEqual(position.entry_tier, "NORMAL")

    def test_silver_has_precedence_when_both_filters_pass(self):
        self.valid_trigger()
        self.add_g1(high=100.5, low=100.1, close=100.48)
        self.strategy.ema20_1m = [100.0] * 5 + [100.2]
        position = self.strategy.on_entry_tick(moment("09:34:10"), 100.51)
        self.assertIsNotNone(position)
        self.assertEqual(position.entry_tier, "SILVER")

    def test_entry_is_discarded_when_neither_quality_filter_passes(self):
        self.valid_trigger()
        self.add_g1()
        self.strategy.ema20_3m = [100.0, 100.0, 100.0]
        self.strategy.ema20_1m = [100.0] * 6
        position = self.strategy.on_entry_tick(moment("09:34:10"), 100.51)
        self.assertIsNone(position)
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(
            self.events[-1][1]["outcome"],
            "ENTRY_QUALITY_FILTERS_FAILED",
        )

    def test_g1_low_break_before_entry_discards(self):
        self.valid_trigger()
        self.add_g1()
        self.strategy.on_entry_tick(moment("09:34:10"), 100.09)
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(
            self.events[-1][1]["outcome"], "ENTRY_CANDLE_BROKE_G1_LOW"
        )

    def test_no_break_in_next_entry_candle_discards(self):
        self.valid_trigger()
        self.add_g1()
        self.strategy.on_one_minute(c1("09:34", 100.4, 100.49, 100.2, 100.45))
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(
            self.events[-1][1]["outcome"],
            "NO_G1_HIGH_BREAK_IN_NEXT_CANDLE",
        )

    def test_second_candle_after_g1_cannot_enter(self):
        self.valid_trigger()
        self.add_g1()
        self.strategy.on_one_minute(c1("09:34", 100.4, 100.49, 100.2, 100.45))
        position = self.strategy.on_entry_tick(moment("09:35:10"), 100.51)
        self.assertIsNone(position)
        self.assertEqual(self.strategy.state, "DONE")

    def test_target_is_minimum_of_r1_and_range_based_r2(self):
        self.valid_trigger()
        self.add_g1()
        position = self.strategy.on_entry_tick(moment("09:34:10"), 100.51)
        expected_r2 = 100.51 + 1.5 * (100.51 - 100.0)
        self.assertAlmostEqual(position.r2_target, expected_r2)
        self.assertAlmostEqual(
            position.tp1_target, min(self.strategy.setup.r1_target, expected_r2)
        )

    def test_alpha_entry_becomes_golden_with_double_quantity_and_fixed_1_3r(self):
        position = self.open_golden_g1()
        self.assertIsNotNone(position)
        self.assertEqual(position.entry_quality, "GOLDEN")
        self.assertTrue(position.alpha_entry)
        self.assertFalse(position.beta_entry)
        self.assertFalse(position.gamma_entry)
        self.assertEqual(position.quantity, 200)
        self.assertEqual(position.tp1_quantity, 140)
        self.assertEqual(position.runner_quantity, 60)
        expected = position.entry_price + 1.3 * (
            position.entry_price - position.initial_sl
        )
        self.assertAlmostEqual(position.tp1_target, expected)
        self.assertAlmostEqual(position.r2_target, expected)

    def test_seeded_volume_sets_beta_and_gamma_but_uses_same_golden_treatment(self):
        position = self.open_golden_g1(seed_volume=True)
        self.assertTrue(position.alpha_entry)
        self.assertTrue(position.beta_entry)
        self.assertTrue(position.gamma_entry)
        self.assertEqual(position.entry_quality, "GOLDEN")
        self.assertEqual(position.quantity, 200)
        self.assertGreaterEqual(position.trigger_same_slot_rvol10, 0.575)
        self.assertGreaterEqual(position.trigger_rvol20_3m, 0.57)

    def test_0930_entry_remains_standard_with_current_quantity_and_target(self):
        self.valid_trigger()
        self.add_g1()
        position = self.strategy.on_entry_tick(moment("09:34:10"), 100.51)
        self.assertEqual(position.entry_quality, "STANDARD")
        self.assertEqual(position.quantity, 100)
        self.assertEqual(position.tp1_quantity, 70)
        self.assertEqual(position.runner_quantity, 30)
        self.assertAlmostEqual(
            position.tp1_target,
            min(position.r1_target, position.r2_target),
        )

    def test_golden_tp1_leaves_sixty_share_runner(self):
        position = self.open_golden_g1()
        self.strategy.mark_tp1_booked(moment("09:45:00"), position.tp1_target)
        self.assertEqual(position.open_quantity, 60)

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

    def test_large_green_selects_b1_path(self):
        self.valid_trigger_with_large_green()
        self.assertEqual(self.strategy.state, "WAIT_B1")
        self.assertEqual(self.strategy.setup.entry_path, "LARGE_GREEN_B1")
        self.assertGreater(
            self.strategy.setup.large_green_range_fraction,
            0.006,
        )

    def test_red_pretrigger_candle_above_threshold_does_not_select_b1(self):
        self.strategy.on_three_minute(
            c3("09:21", 99.9, 100.0, 99.3, 99.4)
        )
        self.strategy.on_three_minute(c3("09:24", 99.6, 100.0, 99.5, 99.9))
        self.strategy.on_three_minute(c3("09:27", 99.9, 100.4, 99.9, 100.3))
        self.strategy.on_three_minute(c3("09:30", 100.35, 100.4, 100.0, 100.2))
        self.assertEqual(self.strategy.state, "WAIT_G1")
        self.assertEqual(self.strategy.setup.entry_path, "STANDARD_G1")

    def test_first_one_minute_close_above_trigger_becomes_b1(self):
        self.valid_trigger_with_large_green()
        self.strategy.on_one_minute(c1("09:33", 100.2, 100.6, 100.1, 100.35))
        self.assertEqual(self.strategy.state, "WAIT_B1")
        self.strategy.on_one_minute(c1("09:34", 100.35, 100.8, 100.2, 100.6))
        self.assertEqual(self.strategy.state, "WAIT_B1_BREAK")
        self.assertEqual(self.strategy.setup.b1.start, moment("09:34:00"))

    def test_no_close_above_trigger_in_six_one_minute_candles_discards(self):
        self.valid_trigger_with_large_green()
        for hhmm in ["09:33", "09:34", "09:35", "09:36", "09:37", "09:38"]:
            self.strategy.on_one_minute(c1(hhmm, 100.2, 100.5, 100.1, 100.35))
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(
            self.events[-1][1]["outcome"],
            "NO_1M_CLOSE_ABOVE_TRIGGER_IN_SIX_CANDLES",
        )

    def test_wide_b1_uses_b1_low_stop_and_one_point_two_r_without_ema(self):
        self.valid_trigger_with_large_green()
        self.strategy.on_one_minute(c1("09:33", 100.2, 100.8, 100.1, 100.6))
        self.strategy.on_entry_tick(moment("09:34:05"), 100.79)
        self.assertEqual(self.strategy.state, "WAIT_B1_BREAK")
        position = self.strategy.on_entry_tick(moment("09:34:20"), 100.81)
        self.assertIsNotNone(position)
        self.assertEqual(position.entry_mode, "B1_HIGH_BREAK")
        self.assertEqual(position.entry_tier, "B1")
        self.assertAlmostEqual(position.initial_sl, 100.1)
        self.assertAlmostEqual(
            position.tp1_target,
            100.81 + 1.2 * (100.81 - 100.1),
        )

    def test_b1_range_target_bands(self):
        self.assertEqual(self.strategy._b1_target_r_multiple(0.0009), 3.0)
        self.assertEqual(self.strategy._b1_target_r_multiple(0.0010), 2.0)
        self.assertEqual(self.strategy._b1_target_r_multiple(0.0035), 2.0)
        self.assertEqual(self.strategy._b1_target_r_multiple(0.0036), 1.2)

    def test_g1_range_target_bands(self):
        self.assertEqual(self.strategy._g1_target_r_multiple(0.0007), 5.0)
        self.assertEqual(self.strategy._g1_target_r_multiple(0.0008), 1.3)
        self.assertEqual(self.strategy._g1_target_r_multiple(0.0030), 1.3)
        self.assertEqual(self.strategy._g1_target_r_multiple(0.0031), 1.5)

    def test_b1_high_must_break_in_immediately_next_one_minute_candle(self):
        self.valid_trigger_with_large_green()
        self.strategy.on_one_minute(c1("09:33", 100.2, 100.8, 100.1, 100.6))
        self.strategy.on_one_minute(c1("09:34", 100.5, 100.79, 100.2, 100.7))
        self.assertEqual(self.strategy.state, "DONE")
        self.assertEqual(
            self.events[-1][1]["outcome"],
            "NO_B1_HIGH_BREAK_IN_NEXT_1M_CANDLE",
        )
        self.assertIsNone(
            self.strategy.on_entry_tick(moment("09:35:05"), 100.81)
        )

    def test_one_of_x1_x2_x3_must_close_above_b1_high(self):
        self.valid_trigger_with_large_green()
        self.strategy.on_one_minute(c1("09:33", 100.2, 100.8, 100.1, 100.6))
        position = self.strategy.on_entry_tick(moment("09:34:20"), 100.81)
        self.assertIsNone(
            self.strategy.on_one_minute(c1("09:34", 100.7, 100.9, 100.2, 100.75))
        )
        self.assertIsNone(
            self.strategy.on_one_minute(c1("09:35", 100.7, 101.0, 100.5, 100.9))
        )
        self.assertTrue(position.b1_close_confirmed)

    def test_x3_close_exits_when_no_candle_closes_above_b1_high(self):
        self.valid_trigger_with_large_green()
        self.strategy.on_one_minute(c1("09:33", 100.2, 100.8, 100.1, 100.6))
        self.strategy.on_entry_tick(moment("09:34:20"), 100.81)
        for hhmm in ["09:34", "09:35"]:
            self.assertIsNone(
                self.strategy.on_one_minute(c1(hhmm, 100.7, 100.9, 100.2, 100.75))
            )
        reason = self.strategy.on_one_minute(
            c1("09:36", 100.7, 100.9, 100.2, 100.75)
        )
        self.assertEqual(reason, "BE_EXIT_NO_CLOSE_ABOVE_B1_HIGH")

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
