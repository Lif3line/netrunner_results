import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from html import escape
from pathlib import Path
from unittest.mock import Mock, patch

from analyser.discovery import discover, find_sources
from analyser.importer import (
    game_result,
    import_tournament,
    parse_decks,
    parse_tournament,
    source_url,
)
from analyser.metrics import Selection, analyse, deck_stats, identity_stats, matchup_stats
from analyser.store import initialise, load_data, save_tournament

URL = "https://tournaments.nullsignal.games/tournaments/42"


def player(identifier, rank, identity="Corp"):
    return {
        "id": identifier,
        "name": "Same name" if identifier in (1, 2) else "Third",
        "rank": rank,
        "corpIdentity": identity,
        "runnerIdentity": "Runner",
        "corpFaction": "nbn",
        "runnerFaction": "criminal",
    }


def pairing(corp, runner, corp_score=3, runner_score=0, **kwargs):
    return {
        "player1": {"id": corp, "role": "corp", "corpScore": corp_score},
        "player2": {"id": runner, "role": "runner", "runnerScore": runner_score},
        "table": 1,
        **kwargs,
    }


def tournament():
    return {
        "name": "Example",
        "date": "2026-08-01",
        "players": [player(1, 1), player(2, 2), player(3, 3, "Unrecognised identity")],
        "eliminationPlayers": [{"id": 1, "rank": 1}],
        "rounds": [
            [pairing(1, 2)],
            [pairing(3, 1, 0, 3)],
            [pairing(2, 3, 1, 1)],
            [pairing(1, 3, 0, 3)],
        ],
    }


def deck_payload(player_id=1, side="corp"):
    return {
        "details": {
            "player_id": player_id,
            "side_id": side,
            "name": "A & B",
            "identity_title": "Corp",
        },
        "cards": [
            {"nrdb_card_id": "a_card", "title": "A <Card>", "quantity": 3, "card_type_id": "asset"}
        ],
    }


def deck_html(payload):
    return f'<input id="{payload["details"]["side_id"]}_deck" value="{escape(json.dumps(payload), quote=True)}">'


class ImportTests(unittest.TestCase):
    def test_source_urls_and_shortcode_provenance(self):
        self.assertEqual(source_url(URL + "/players/standings"), URL)
        self.assertEqual(source_url(URL + ".json"), URL)
        self.assertEqual(
            source_url("https://tournaments.nullsignal.games/tournaments/bqnh"),
            "https://tournaments.nullsignal.games/BQNH",
        )
        self.assertEqual(
            source_url("https://aesopstables.net/475.json"), "https://www.aesopstables.net/475"
        )
        self.assertIsNone(source_url("https://example.com/tournaments/42"))

    def test_sss_retains_source_ids_unknown_identities_and_draws(self):
        event, entries, games = parse_tournament(tournament(), URL)
        self.assertEqual(event["id"], "cobra-42")
        self.assertEqual(entries[0]["name"], entries[1]["name"])
        self.assertNotEqual(entries[0]["player_id"], entries[1]["player_id"])
        self.assertEqual(entries[2]["corp_identity"], "Unrecognised identity")
        self.assertEqual([game["result"] for game in games], ["corp", "runner", "draw", "runner"])

    def test_dss_produces_one_record_for_each_side(self):
        raw = tournament()
        raw["rounds"] = [
            [
                {
                    "player1": {"id": 1, "runnerScore": 3, "corpScore": 1},
                    "player2": {"id": 2, "runnerScore": 1, "corpScore": 0},
                }
            ]
        ]
        _, _, games = parse_tournament(raw, URL)
        self.assertEqual(
            [(game["corp_player_id"], game["runner_player_id"], game["result"]) for game in games],
            [("2", "1", "runner"), ("1", "2", "draw")],
        )

    def test_cut_byes_intentional_draws_and_incomplete_scores(self):
        cases = [
            (pairing(None, 2), "bye"),
            (pairing(1, 2, intentionalDraw=True), "intentional_draw"),
            (pairing(1, 2, twoForOne=True), "two_for_one"),
            (pairing(1, 2, None, None), "unknown"),
            (pairing(1, 2, 0, 0), "unknown"),
        ]
        for table, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(game_result(table, table["player1"], table["player2"]), expected)
        self.assertEqual(
            game_result(
                {"eliminationGame": True}, {"id": 1, "winner": True}, {"id": 2, "winner": False}
            ),
            "corp",
        )
        self.assertEqual(
            game_result(
                {"eliminationGame": True}, {"id": 1, "winner": False}, {"id": 2, "winner": False}
            ),
            "unknown",
        )

    def test_aesops_scores_use_each_players_side(self):
        raw = tournament()
        raw["rounds"] = [[pairing(1, 2, 0, 3, eliminationGame=True)]]
        event, _, games = parse_tournament(raw, "https://www.aesopstables.net/475")
        self.assertEqual(event["id"], "aesop-475")
        self.assertEqual(games[0]["result"], "runner")
        self.assertEqual(games[0]["phase"], "cut")

    def test_cobra_bye_without_assigned_roles(self):
        raw = tournament()
        raw["rounds"] = [
            [
                {
                    "player1": {"id": 1, "role": None},
                    "player2": {"id": None, "role": None},
                }
            ]
        ]
        _, _, games = parse_tournament(raw, URL)
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["result"], "bye")
        self.assertIsNone(games[0]["corp_player_id"])
        self.assertIsNone(games[0]["runner_player_id"])

    def test_public_deck_payload_and_wrong_player(self):
        decks = parse_decks(deck_html(deck_payload()), "cobra-42", "1", URL)
        self.assertEqual(decks[0]["name"], "A & B")
        self.assertEqual(decks[0]["cards"][0]["title"], "A <Card>")
        self.assertEqual(decks[0]["cards"][0]["quantity"], 3)
        with self.assertRaisesRegex(ValueError, "requested player"):
            parse_decks(deck_html(deck_payload()), "cobra-42", "2", URL)
        with self.assertRaisesRegex(ValueError, "no structured"):
            parse_decks("<html>Sign in</html>", "cobra-42", "1", URL)


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "results.sqlite3"
        initialise(self.path)
        self.event, self.entries, self.games = parse_tournament(tournament(), URL)
        self.decks = parse_decks(deck_html(deck_payload()), "cobra-42", "1", URL)
        save_tournament(self.event, self.entries, self.games, self.decks, self.path)

    def test_reimport_is_idempotent_and_rollback_retains_complete_event(self):
        save_tournament(self.event, self.entries, self.games, self.decks, self.path)
        data = load_data(self.path)
        self.assertEqual(len(data["games"]), len(self.games))
        self.assertEqual(len(data["decks"]), len(self.decks))
        broken = copy.deepcopy(self.games)
        broken[0]["corp_player_id"] = "missing"
        with self.assertRaises(sqlite3.IntegrityError):
            save_tournament(self.event, self.entries, broken, [], self.path)
        restored = load_data(self.path)
        self.assertEqual(len(restored["games"]), len(self.games))
        self.assertEqual(len(restored["deck_cards"]), 1)

    def test_results_only_refresh_retains_decks_and_successful_refresh_replaces_them(self):
        save_tournament(self.event, self.entries, self.games, None, self.path)
        self.assertEqual(len(load_data(self.path)["deck_cards"]), 1)
        save_tournament(self.event, self.entries, self.games, [], self.path)
        self.assertTrue(load_data(self.path)["deck_cards"].empty)

    def test_failed_remote_deck_refresh_does_not_touch_database(self):
        response = Mock()
        response.json.return_value = tournament()
        with (
            patch("analyser.importer.get", return_value=response),
            patch("analyser.importer.fetch_decks", side_effect=ValueError("broken deck")),
        ):
            with self.assertRaisesRegex(ValueError, "broken deck"):
                import_tournament(URL, self.path, metadata={"format": "standard"})
        self.assertEqual(len(load_data(self.path)["decks"]), 1)

    def test_whole_field_counts_games_once_and_keeps_unfamiliar_named_identity(self):
        result = analyse(load_data(self.path), Selection(("cobra-42",)))
        self.assertEqual(len(result["games"]), len(self.games))
        self.assertEqual(len(result["observations"]), 2 * len(self.games))
        corps = identity_stats(result, "corp").set_index("identity")
        self.assertIn("Unrecognised identity", corps.index)
        self.assertAlmostEqual(corps.loc["Corp", "winrate"], 1 / 3)
        self.assertEqual(corps.loc["Corp", "draws"], 1)

    def test_unknown_identity_games_are_excluded_from_both_sides_and_decks(self):
        self.entries[0]["corp_identity"] = "Unknown identity"
        self.entries[2]["runner_identity"] = " unknown "
        save_tournament(self.event, self.entries, self.games, self.decks, self.path)
        data = load_data(self.path)
        result = analyse(data, Selection(("cobra-42",)))
        self.assertEqual(result["excluded"]["unknown_identity"], 3)
        self.assertEqual(result["games"].game_id.tolist(), [2])
        self.assertEqual(len(result["observations"]), 2)
        self.assertEqual(identity_stats(result, "corp").iloc[0].winrate, 0)
        self.assertEqual(identity_stats(result, "runner").iloc[0].winrate, 1)
        decks, _, coverage = deck_stats(data, result, "corp")
        self.assertTrue(decks.empty)
        self.assertEqual(coverage, (0, 2))

        data["games"].loc[data["games"].game_id == 1, "result"] = "intentional_draw"
        result = analyse(data, Selection(("cobra-42",)))
        self.assertEqual(result["excluded"]["unknown_identity"], 2)
        self.assertEqual(result["excluded"]["intentional_draw"], 1)

    def test_unknown_identity_exclusions_follow_the_phase_filter(self):
        self.entries[0]["corp_identity"] = "Unknown identity"
        self.games[0]["phase"] = "cut"
        save_tournament(self.event, self.entries, self.games, self.decks, self.path)
        data = load_data(self.path)
        cut = analyse(data, Selection(("cobra-42",), phase="cut"))
        swiss = analyse(data, Selection(("cobra-42",), phase="swiss"))
        self.assertEqual(cut["excluded"]["unknown_identity"], 1)
        self.assertTrue(cut["games"].empty)
        self.assertEqual(swiss["excluded"]["unknown_identity"], 1)
        self.assertEqual(swiss["games"].game_id.tolist(), [2, 3])

    def test_cohort_tracks_qualifying_side_not_opponent_or_same_name(self):
        result = analyse(load_data(self.path), Selection(("cobra-42",), cohort="cut"))
        self.assertEqual(set(result["observations"].player_id), {"1"})
        self.assertEqual(len(result["games"]), 3)
        self.assertEqual(len(result["observations"]), 3)
        corps = identity_stats(result, "corp")
        self.assertEqual(corps.iloc[0].games, 2)
        self.assertEqual(corps.iloc[0].winrate, 0.5)
        matches = matchup_stats(result, "corp", "Corp")
        self.assertEqual(matches.iloc[0].games, 2)

    def test_loser_threshold_uses_tournament_side_rates(self):
        result = analyse(load_data(self.path), Selection(("cobra-42",), loser_min_winrate=0.1))
        self.assertEqual(result["excluded"]["low_loser_winrate"], 2)
        self.assertEqual(len(result["games"]), 2)

    def test_deck_denominator_is_known_lists_not_all_entries(self):
        data = load_data(self.path)
        result = analyse(data, Selection(("cobra-42",)))
        decks, cards, coverage = deck_stats(data, result, "corp")
        self.assertEqual(coverage, (1, 3))
        self.assertEqual(cards.iloc[0].inclusion, 1)
        self.assertEqual(cards.iloc[0].average_copies, 3)
        self.assertEqual(decks.iloc[0].games, 2)
        self.assertEqual(decks.iloc[0].winrate, 0.5)
        decks, cards, _ = deck_stats(data, result, "corp", contains="missing")
        self.assertTrue(decks.empty)
        self.assertTrue(cards.empty)

    def test_empty_selection_has_no_stale_results(self):
        data = load_data(self.path)
        result = analyse(data, Selection(()))
        self.assertTrue(result["games"].empty)
        self.assertTrue(identity_stats(result, "corp").empty)
        self.assertTrue(deck_stats(data, result, "corp")[0].empty)


class DiscoveryTests(unittest.TestCase):
    def test_uploaded_provenance_preferred_over_page_links(self):
        event = {
            "id": 12,
            "matchdata": True,
            "url": "https://alwaysberunning.net/tournaments/12",
            "title": "Example",
        }
        response = Mock()
        response.json.return_value = {"links": [{"rel": "uploadedfrom", "href": URL}]}
        session = Mock()
        session.get.return_value = response
        self.assertEqual(find_sources(event, session, lambda _: None), {URL})
        self.assertEqual(session.get.call_count, 1)

    def test_date_attendance_level_and_ambiguous_sources(self):
        base = {
            "id": 12,
            "date": "2026.08.01.",
            "concluded": True,
            "players_count": 32,
            "format": "standard",
            "type": "continental championship",
            "title": "Example",
        }
        events = [
            base,
            {**base, "id": 13, "date": "2026.07.31."},
            {**base, "id": 14, "players_count": 31},
            {**base, "id": 15, "type": "casual"},
            {**base, "id": 16, "title": "Ambiguous"},
        ]
        response = Mock()
        response.json.return_value = events
        with (
            patch("analyser.discovery.get", return_value=response),
            patch("analyser.discovery.find_sources", side_effect=[{URL}, {URL, URL + "3"}]),
        ):
            found, warnings = discover(
                date(2026, 8, 1), date(2026, 8, 2), 32, event_types=("continental championship",)
            )
        self.assertEqual([event["url"] for event in found], [URL])
        self.assertEqual(len(warnings), 1)
        self.assertIn("2 sources", warnings[0])


if __name__ == "__main__":
    unittest.main()
