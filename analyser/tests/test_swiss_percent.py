import unittest

import pandas as pd
from analyser.importer import parse_tournament
from analyser.metrics import Selection, analyse
from analyser.tests.test_analyser import player, tournament


class SwissPercentTests(unittest.TestCase):
    def setUp(self):
        events, entries, games = [], [], []
        for identifier, size in ((41, 4), (42, 7)):
            raw = tournament()
            raw["players"] = [
                {**player(index, index), "name": f"Player {identifier}-{index}"}
                for index in range(1, size + 1)
            ]
            event, event_entries, event_games = parse_tournament(
                raw, f"https://tournaments.nullsignal.games/tournaments/{identifier}"
            )
            events.append(event)
            entries.extend(event_entries)
            games.extend(event_games)
        self.data = {
            "tournaments": pd.DataFrame(events),
            "entries": pd.DataFrame(entries),
            "games": pd.DataFrame(games),
        }
        self.data["entries"]["swiss_rank"] = self.data["entries"]["swiss_rank"].astype("Int64")

    def selection(self, percentage, **kwargs):
        return Selection(
            ("cobra-41", "cobra-42"), cohort="percent", top_percent=percentage, **kwargs
        )

    def test_cutoffs_use_each_field_and_round_up(self):
        result = analyse(self.data, self.selection(25))
        qualifying = result["entries"].groupby("tournament_id").player_id.apply(list).to_dict()
        self.assertEqual(qualifying, {"cobra-41": ["1"], "cobra-42": ["1", "2"]})
        observed = set(zip(result["observations"].tournament_id, result["observations"].player_id))
        self.assertEqual(observed, {("cobra-41", "1"), ("cobra-42", "1"), ("cobra-42", "2")})

    def test_missing_ranks_still_count_towards_field_size_but_do_not_qualify(self):
        entries = self.data["entries"]
        entries.loc[entries.tournament_id == "cobra-41", "swiss_rank"] = [None, 2, 3, None]
        result = analyse(self.data, self.selection(50))
        first = result["entries"].query("tournament_id == 'cobra-41'")
        self.assertEqual(first.player_id.tolist(), ["2"])

    def test_boundary_ties_are_included(self):
        entries = self.data["entries"]
        entries.loc[
            (entries.tournament_id == "cobra-42") & (entries.player_id == "3"), "swiss_rank"
        ] = 2
        result = analyse(self.data, self.selection(25))
        second = result["entries"].query("tournament_id == 'cobra-42'")
        self.assertEqual(second.player_id.tolist(), ["1", "2", "3"])

    def test_game_filters_do_not_change_qualification_denominator(self):
        baseline = analyse(self.data, self.selection(25))["entries"]
        filtered = analyse(self.data, self.selection(25, phase="cut", loser_min_winrate=0.5))
        pd.testing.assert_frame_equal(baseline, filtered["entries"])
        self.assertTrue(filtered["games"].empty)

    def test_percentage_limits_and_empty_selection(self):
        for percentage in (0, 101):
            with self.subTest(percentage=percentage), self.assertRaises(ValueError):
                analyse(self.data, self.selection(percentage))
        self.assertEqual(len(analyse(self.data, self.selection(1))["entries"]), 2)
        self.assertEqual(
            len(analyse(self.data, self.selection(100))["entries"]), len(self.data["entries"])
        )
        empty = analyse(self.data, Selection((), cohort="percent"))
        self.assertTrue(empty["entries"].empty)
        self.assertTrue(empty["games"].empty)

    def test_nonpositive_ranks_do_not_qualify_even_at_one_hundred_percent(self):
        entries = self.data["entries"]
        entries.loc[entries.tournament_id == "cobra-41", "swiss_rank"] = [0, -1, None, 4]
        result = analyse(self.data, self.selection(100))
        first = result["entries"].query("tournament_id == 'cobra-41'")
        self.assertEqual(first.player_id.tolist(), ["4"])


if __name__ == "__main__":
    unittest.main()
