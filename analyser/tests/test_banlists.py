import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analyser.cache import CachedSession, CacheMiss
from analyser.discovery import cached_banlists
from analyser.importer import (
    import_tournament,
    normalise_banlist,
    parse_tournament,
    source_metadata,
)
from analyser.store import initialise, save_tournament
from analyser.tests.test_alias_cache import response
from analyser.tests.test_analyser import URL, tournament

SEARCH = "https://alwaysberunning.net/api/tournaments?concluded=1&start=2026.08.01.&end=2026.08.02."
UPLOAD = "https://alwaysberunning.net/tjsons/123.json"


class BanlistTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "test.sqlite3"
        initialise(self.path)
        event, entries, games = parse_tournament(tournament(), URL)
        save_tournament(event, entries, games, [], self.path)

    def cache(self, url, payload):
        result = response(payload, url=url)
        if isinstance(payload, str):
            result._content = payload.encode()
        with (
            patch("requests.Session.get", return_value=result),
            CachedSession(self.path) as session,
        ):
            session.get(url)

    def search(self, label, sources):
        self.cache(
            SEARCH,
            [
                {
                    "id": 123,
                    "title": "Different event title",
                    "matchdata": True,
                    "url": "https://alwaysberunning.net/tournaments/123",
                    "mwl": label,
                }
            ],
        )
        self.cache(UPLOAD, {"links": [{"rel": "uploadedfrom", "href": url} for url in sources]})

    def test_labels_are_normalised_without_date_inference(self):
        self.assertEqual(normalise_banlist("Standard Balance Update 26.08"), "26.08")
        self.assertEqual(normalise_banlist("Standard Ban List 25.10"), "25.10")
        self.assertEqual(normalise_banlist("Standard MWL 2.1"), "MWL-2.1")
        self.assertIsNone(normalise_banlist("---unknown---"))
        metadata = source_metadata(
            "<p>Deckbuilding Restrictions: Standard Balance Update 26.08</p>"
        )
        self.assertEqual(metadata["banlist"], "26.08")
        with patch(
            "requests.Session.get", side_effect=AssertionError("Unexpected network request")
        ):
            self.assertIsNone(cached_banlists(self.path)[URL])

    def test_cached_abr_provenance_is_used_without_matching_titles(self):
        self.search("Standard Ban List 26.05", [URL])
        with patch(
            "requests.Session.get", side_effect=AssertionError("Unexpected network request")
        ):
            self.assertEqual(cached_banlists(self.path)[URL], "26.05")

    def test_cached_cobra_restrictions_override_stale_abr_label(self):
        self.search("Standard Ban List 26.03", [URL])
        self.cache(URL + "/info", "<p>Deckbuilding Restrictions: Standard Balance Update 26.08</p>")
        with patch(
            "requests.Session.get", side_effect=AssertionError("Unexpected network request")
        ):
            self.assertEqual(cached_banlists(self.path)[URL], "26.08")

    def test_ambiguous_sources_do_not_assign_a_banlist(self):
        self.search("Standard Ban List 26.05", [URL, URL + "1"])
        with patch(
            "requests.Session.get", side_effect=AssertionError("Unexpected network request")
        ):
            self.assertIsNone(cached_banlists(self.path)[URL])

    def test_offline_cache_miss_never_attempts_the_network(self):
        with patch(
            "requests.Session.get", side_effect=AssertionError("Unexpected network request")
        ):
            with CachedSession(self.path, offline=True) as session:
                with self.assertRaises(CacheMiss):
                    session.get(URL)

    def test_explicit_refresh_prefers_cobra_and_retains_banlist_metadata(self):
        info = response({})
        info._content = b"<p>Game Format: Standard</p><p>Deckbuilding Restrictions: Standard Balance Update 26.08</p>"
        with patch("requests.Session.get", side_effect=[response(tournament()), info]):
            event = import_tournament(
                URL,
                self.path,
                include_decks=False,
                refresh=True,
                metadata={"format": "standard", "banlist": "26.03"},
            )
        self.assertEqual(json.loads(event["raw_json"])["metadata"]["banlist"], "26.08")
        with patch(
            "requests.Session.get", side_effect=AssertionError("Unexpected network request")
        ):
            self.assertEqual(cached_banlists(self.path)[URL], "26.08")


if __name__ == "__main__":
    unittest.main()
