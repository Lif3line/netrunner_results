import math
import unittest

import pandas as pd
from analyser.charts import winrate_chart
from analyser.metrics import aggregate, wilson_interval
from bokeh.models import HoverTool, Whisker


class IntervalTests(unittest.TestCase):
    def test_reference_values_include_small_and_extreme_samples(self):
        cases = [
            (0, 1, 0.0, 0.7934506856),
            (1, 1, 0.2065493144, 1.0),
            (2, 2, 0.3423802275, 1.0),
            (50, 100, 0.4038315304, 0.5961684696),
        ]
        for wins, games, lower, upper in cases:
            with self.subTest(wins=wins, games=games):
                actual = wilson_interval(wins, games)
                self.assertAlmostEqual(actual[0], lower, places=8)
                self.assertAlmostEqual(actual[1], upper, places=8)

    def test_intervals_are_bounded_symmetric_and_narrow_with_more_games(self):
        for games in (1, 2, 10, 100):
            for wins in range(games + 1):
                lower, upper = wilson_interval(wins, games)
                other_lower, other_upper = wilson_interval(games - wins, games)
                self.assertGreaterEqual(lower, 0)
                self.assertLessEqual(upper, 1)
                self.assertLessEqual(lower, wins / games + 1e-12)
                self.assertGreaterEqual(upper, wins / games - 1e-12)
                self.assertAlmostEqual(lower, 1 - other_upper)
                self.assertAlmostEqual(upper, 1 - other_lower)
        small = wilson_interval(5, 10)
        large = wilson_interval(50, 100)
        self.assertLess(large[1] - large[0], small[1] - small[0])

    def test_no_games_has_no_interval_and_invalid_counts_are_rejected(self):
        self.assertTrue(all(math.isnan(bound) for bound in wilson_interval(0, 0)))
        for wins, games in ((-1, 10), (11, 10), (0, -1)):
            with self.subTest(wins=wins, games=games), self.assertRaises(ValueError):
                wilson_interval(wins, games)

    def test_aggregation_uses_the_same_denominator_including_draws(self):
        observations = pd.DataFrame(
            {
                "identity": ["Example"] * 4,
                "faction": ["NBN"] * 4,
                "win": [1, 1, 0, 0],
                "draw": [0, 0, 1, 0],
            }
        )
        stats = aggregate(observations, ["identity", "faction"])
        self.assertEqual(stats.iloc[0].winrate, 0.5)
        lower, upper = wilson_interval(2, 4)
        self.assertEqual(stats.iloc[0].ci_lower, lower)
        self.assertEqual(stats.iloc[0].ci_upper, upper)
        empty = aggregate(observations.iloc[:0], ["identity", "faction"])
        self.assertTrue(empty.empty)
        self.assertIn("ci_lower", empty.columns)

    def test_chart_whiskers_toggle_without_changing_the_observed_rate(self):
        stats = aggregate(
            pd.DataFrame(
                {
                    "identity": ["Example"] * 2,
                    "faction": ["NBN"] * 2,
                    "win": [0, 0],
                    "draw": [0, 1],
                }
            ),
            ["identity", "faction"],
        )
        for visible in (True, False):
            with self.subTest(visible=visible):
                plot = winrate_chart(stats, "Example", show_intervals=visible)
                interval = plot.select_one(Whisker)
                self.assertEqual(interval.visible, visible)
                self.assertEqual(interval.dimension, "width")
                self.assertEqual(interval.level, "overlay")
                self.assertEqual(interval.source.data["winrate"][0], 0)
                self.assertAlmostEqual(
                    interval.source.data["ci_upper"][0], wilson_interval(0, 2)[1]
                )
                hover = plot.select_one(HoverTool)
                self.assertTrue(any("ci_lower" in text for _, text in hover.tooltips))
                self.assertEqual(len(hover.renderers), 2)


if __name__ == "__main__":
    unittest.main()
