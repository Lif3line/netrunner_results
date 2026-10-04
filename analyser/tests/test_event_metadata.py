import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from analyser.discovery import discover
from analyser.event_types import normalise_type
from analyser.importer import import_tournament, source_metadata
from analyser.store import initialise
from analyser.tests.test_alias_cache import response
from analyser.tests.test_analyser import URL, tournament


class MetadataTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "test.sqlite3"
        initialise(self.path)

    def test_direct_url_reads_source_format_and_reuses_it_without_network(self):
        info = response({})
        info._content = (
            b"<nav>Tournament Types World Championship</nav><p>Game Format: <b>Startup</b></p>"
        )
        with patch("requests.Session.get", side_effect=[response(tournament()), info]) as network:
            event = import_tournament(URL, self.path, include_decks=False)
            self.assertEqual(event["format"], "startup")
            self.assertIsNone(event["level"])
            self.assertEqual(network.call_args_list[-1].args[0], URL + "/info")
        with patch("requests.Session.get", side_effect=AssertionError("Unexpected request")):
            self.assertEqual(
                import_tournament(URL, self.path, include_decks=False)["format"], "startup"
            )

    def test_existing_metadata_does_not_fetch_an_extra_info_page(self):
        with patch("requests.Session.get", return_value=response(tournament())) as network:
            event = import_tournament(
                URL, self.path, metadata={"format": "standard"}, include_decks=False
            )
        network.assert_called_once()
        self.assertEqual(event["format"], "standard")

    def test_unavailable_metadata_keeps_unknown_instead_of_guessing(self):
        notes = []
        with patch(
            "requests.Session.get", side_effect=[response(tournament()), response({}, status=404)]
        ):
            event = import_tournament(URL, self.path, include_decks=False, progress=notes.append)
        self.assertEqual(event["format"], "unknown")
        self.assertTrue(any("metadata" in note for note in notes))

    def test_only_labelled_info_is_used_and_source_names_are_normalised(self):
        metadata = source_metadata(
            "<script>Game Format: Invalid</script><nav>Tournament Types: World Championship</nav>"
            "<p>Game Format: Standard</p><p>Tournament Type: World Championship</p>"
        )
        self.assertEqual(metadata, {"format": "standard", "level": "world championship"})
        self.assertEqual(normalise_type(metadata["level"]), "worlds championship")
        self.assertEqual(normalise_type("Game Night Kit"), "gnk / seasonal")
        self.assertEqual(normalise_type(None), "unknown")

    def test_event_types_are_exact_categories_and_allow_multiple_choices(self):
        base = {
            "date": "2026.08.01.",
            "concluded": True,
            "players_count": 32,
            "format": "standard",
            "title": "Example",
        }
        categories = ["district championship", "megacity championship", "worlds championship"]
        events = [
            {**base, "id": index, "type": category} for index, category in enumerate(categories)
        ]
        api = Mock()
        api.json.return_value = events
        with (
            patch("analyser.discovery.get", return_value=api),
            patch("analyser.discovery.find_sources", side_effect=[{URL}, {URL + "1"}]) as sources,
        ):
            found, warnings = discover(
                date(2026, 8, 1),
                date(2026, 8, 2),
                event_types=tuple(categories[:2]),
                path=self.path,
            )
        self.assertEqual([event["level"] for event in found], categories[:2])
        self.assertEqual(sources.call_count, 2)
        self.assertFalse(warnings)


if __name__ == "__main__":
    unittest.main()
