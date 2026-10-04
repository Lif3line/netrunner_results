import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import requests
from analyser.aliases import cross_event_keys, load_aliases
from analyser.cache import CachedSession
from analyser.discovery import discover
from analyser.importer import import_tournament, parse_tournament
from analyser.metrics import Selection, analyse
from analyser.store import connect, initialise, load_data, save_tournament
from analyser.tests.test_analyser import URL, tournament


def response(data, url=URL, status=200):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = json.dumps(data).encode()
    result.encoding = "utf-8"
    return result


class AliasTests(unittest.TestCase):
    def test_known_aliases_and_ambiguous_names(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "players.json"
            path.write_text(
                json.dumps(
                    {
                        "10": {"nrdb_name": "Alice", "aliases": ["Alias A", "Shared"]},
                        "20": {"nrdb_name": "Bob", "aliases": ["Shared"]},
                    }
                )
            )
            aliases = load_aliases(path)
        entries = pd.DataFrame(
            [
                {"tournament_id": "a", "player_id": "1", "name": "ALICE"},
                {"tournament_id": "b", "player_id": "2", "name": "  Alias   A "},
                {"tournament_id": "a", "player_id": "3", "name": "Shared"},
                {"tournament_id": "b", "player_id": "4", "name": "Shared"},
            ]
        )
        keys = cross_event_keys(entries, aliases)
        self.assertEqual(keys[0], keys[1])
        self.assertNotEqual(keys[2], keys[3])

    def test_duplicate_name_in_one_event_is_not_propagated(self):
        entries = pd.DataFrame(
            [
                {"tournament_id": "a", "player_id": "1", "name": "Same"},
                {"tournament_id": "a", "player_id": "2", "name": "Same"},
                {"tournament_id": "b", "player_id": "3", "name": "Same"},
            ]
        )
        self.assertEqual(cross_event_keys(entries, {}).nunique(), len(entries))

    def test_cross_event_cohort_uses_only_included_events_and_stays_side_specific(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite3"
            initialise(path)
            first = tournament()
            first["players"][0]["name"] = "Alice"
            first["players"][1]["name"] = "Bob"
            second = tournament()
            second["players"][0]["name"] = "Alias A"
            second["players"][1]["name"] = "Bob"
            second["players"][0]["rank"] = 3
            second["eliminationPlayers"] = []
            for raw, url in ((first, URL), (second, URL + "1")):
                event, entries, games = parse_tournament(raw, url)
                save_tournament(event, entries, games, [], path)
            data = load_data(path)
        aliases = {"alice": "alias:10", "alias a": "alias:10"}
        for cohort in ("any_cut", "any_top", "any_percent"):
            with self.subTest(cohort=cohort):
                result = analyse(
                    data, Selection(("cobra-42", "cobra-421"), cohort=cohort, top_n=1), aliases
                )
                self.assertEqual(len(result["entries"]), 2)
                self.assertEqual(set(result["observations"].player_id), {"1"})
                self.assertEqual(
                    set(result["observations"].tournament_id), {"cobra-42", "cobra-421"}
                )
                excluded_qualifier = analyse(
                    data, Selection(("cobra-421",), cohort=cohort, top_n=1), aliases
                )
                self.assertTrue(excluded_qualifier["entries"].empty)
        local = analyse(data, Selection(("cobra-42", "cobra-421"), cohort="cut"), aliases)
        self.assertEqual(len(local["entries"]), 1)


class CacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "test.sqlite3"
        initialise(self.path)

    def test_repeat_get_uses_cache_refresh_replaces_and_failure_preserves(self):
        with patch("requests.Session.get", return_value=response({"value": 1})) as network:
            with CachedSession(self.path) as session:
                self.assertEqual(session.get(URL).json(), {"value": 1})
                self.assertEqual(session.get(URL).json(), {"value": 1})
            network.assert_called_once()
        with patch("requests.Session.get", return_value=response({"value": 2})) as network:
            with CachedSession(self.path, refresh=True) as session:
                self.assertEqual(session.get(URL).json(), {"value": 2})
            network.assert_called_once()
        with patch("requests.Session.get", return_value=response({}, status=500)):
            with CachedSession(self.path, refresh=True) as session:
                with self.assertRaises(requests.HTTPError):
                    session.get(URL).raise_for_status()
        with patch(
            "requests.Session.get", side_effect=AssertionError("Unexpected network request")
        ):
            with CachedSession(self.path) as session:
                self.assertEqual(session.get(URL).json(), {"value": 2})

    def test_import_caches_empty_deck_result_and_only_refresh_fetches_again(self):
        with patch(
            "requests.Session.get", side_effect=[response(tournament()), response({"stages": []})]
        ) as network:
            imported = import_tournament(URL, self.path, metadata={"format": "standard"})
            self.assertIsNotNone(imported["decks_checked_at"])
            self.assertEqual(network.call_count, 2)
        with patch(
            "requests.Session.get", side_effect=AssertionError("Unexpected network request")
        ):
            reused = import_tournament(URL, self.path)
            self.assertEqual(reused["imported_at"], imported["imported_at"])
        with patch(
            "requests.Session.get",
            side_effect=[response(tournament()), response({}), response({"stages": []})],
        ) as network:
            import_tournament(URL, self.path, refresh=True, metadata={"format": "standard"})
            self.assertEqual(network.call_count, 3)
        self.assertEqual(len(load_data(self.path)["games"]), len(tournament()["rounds"]))

    def test_missing_decks_reuse_imported_results(self):
        with patch("requests.Session.get", return_value=response(tournament())) as network:
            import_tournament(URL, self.path, include_decks=False, metadata={"format": "standard"})
            network.assert_called_once()
        with patch("requests.Session.get", return_value=response({"stages": []})) as network:
            import_tournament(URL, self.path, include_decks=True)
            network.assert_called_once_with(URL + "/players/standings_data", timeout=30)

    def test_discovery_reuses_source_responses_across_filter_changes(self):
        event = {
            "id": 12,
            "date": "2026.08.01.",
            "concluded": True,
            "matchdata": True,
            "players_count": 32,
            "format": "standard",
            "title": "Example",
        }
        upload = {"links": [{"rel": "uploadedfrom", "href": URL}]}
        with patch(
            "requests.Session.get", side_effect=[response([event]), response(upload)]
        ) as network:
            found, warnings = discover(date(2026, 8, 1), date(2026, 8, 2), path=self.path)
            self.assertEqual(len(found), 1)
            self.assertFalse(warnings)
            self.assertEqual(network.call_count, 2)
        with patch(
            "requests.Session.get", side_effect=AssertionError("Unexpected network request")
        ):
            found, _ = discover(date(2026, 8, 1), date(2026, 8, 2), min_players=16, path=self.path)
            self.assertEqual(len(found), 1)

    def test_version_one_database_migrates_without_losing_results(self):
        event, entries, games = parse_tournament(tournament(), URL)
        event["deck_status"] = "0 public lists"
        save_tournament(event, entries, games, [], self.path)
        with connect(self.path) as connection:
            connection.execute("ALTER TABLE tournaments DROP COLUMN decks_checked_at")
            connection.execute("DROP TABLE http_cache")
            connection.execute("PRAGMA user_version = 1")
        initialise(self.path)
        initialise(self.path)
        data = load_data(self.path)
        self.assertEqual(len(data["games"]), len(games))
        self.assertIsNotNone(data["tournaments"].iloc[0].decks_checked_at)


if __name__ == "__main__":
    unittest.main()
