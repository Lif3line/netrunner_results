import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from analyser.app import Workspace
from analyser.importer import parse_tournament
from analyser.settings import load_selection, save_selection, selection_path
from analyser.store import initialise, save_tournament
from analyser.tests.test_analyser import URL, tournament


class SettingsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.database = self.directory / "example.sqlite3"

    def test_round_trip_with_dates_empty_bounds_and_separate_databases(self):
        settings = {
            "since": "2026-08-01",
            "until": None,
            "min_players": 32,
            "format": "standard",
            "event_types": ["continental championship"],
            "skipped_ids": ["cobra-42"],
        }
        save_selection(self.database, settings)
        self.assertEqual(load_selection(self.database), {**settings, "since": date(2026, 8, 1)})
        self.assertEqual(load_selection(self.directory / "other.sqlite3"), {})
        self.assertEqual(json.loads(selection_path(self.database).read_text()), settings)

    def test_missing_or_malformed_file_uses_defaults(self):
        self.assertEqual(load_selection(self.database), {})
        for content in ("{invalid", "[]"):
            with self.subTest(content=content):
                selection_path(self.database).write_text(content)
                with self.assertLogs("analyser.settings", level="WARNING"):
                    self.assertEqual(load_selection(self.database), {})

    def test_invalid_fields_do_not_discard_valid_settings(self):
        save_selection(
            self.database,
            {
                "since": "not a date",
                "until": "2026-09-30",
                "min_players": -1,
                "format": "invalid",
                "event_types": ["district championship"],
                "skipped_ids": [123],
            },
        )
        with self.assertLogs("analyser.settings", level="WARNING"):
            settings = load_selection(self.database)
        self.assertEqual(
            settings, {"until": date(2026, 9, 30), "event_types": ["district championship"]}
        )

    def test_failed_replace_retains_previous_selection_and_cleans_temporary_file(self):
        save_selection(self.database, {"min_players": 16})
        with patch("pathlib.Path.replace", side_effect=OSError("cannot replace")):
            with self.assertRaises(OSError):
                save_selection(self.database, {"min_players": 32})
        self.assertEqual(load_selection(self.database), {"min_players": 16})
        self.assertEqual(
            list(self.directory.glob("*.selection.json")), [selection_path(self.database)]
        )


class WorkspaceSelectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "test.sqlite3"
        initialise(self.database)
        for index, event_date in ((42, "2026-08-01"), (43, "2026-09-05")):
            raw = tournament()
            raw["name"] = f"Event {index}"
            raw["date"] = event_date
            event, entries, games = parse_tournament(
                raw,
                URL.rsplit("/", 1)[0] + f"/{index}",
                {"format": "standard", "level": "continental championship"},
            )
            save_tournament(event, entries, games, [], self.database)

    def workspace(self):
        with patch("requests.Session.get", side_effect=AssertionError("Unexpected network access")):
            return Workspace(self.database)

    def test_filter_and_drag_changes_restore_in_a_new_session(self):
        workspace = self.workspace()
        workspace.since.value = date(2026, 8, 1)
        workspace.until.value = date(2026, 9, 30)
        workspace.attendance.value = 0
        workspace.format.value = "All formats"
        workspace.event_types.value = ["continental championship"]
        workspace.events.included = ["cobra-42"]

        with patch("analyser.app.save_selection") as write:
            restored = self.workspace()
        write.assert_not_called()
        self.assertEqual(restored.since.value, date(2026, 8, 1))
        self.assertEqual(restored.until.value, date(2026, 9, 30))
        self.assertEqual(restored.attendance.value, 0)
        self.assertEqual(restored.format.value, "All formats")
        self.assertEqual(restored.event_types.value, ["continental championship"])
        self.assertEqual(restored.events.included, ["cobra-42"])
        self.assertEqual(restored.skipped_ids, {"cobra-43"})

        restored.since.value = date(2026, 8, 15)
        self.assertEqual(restored.events.included, [])
        another = self.workspace()
        self.assertEqual(another.events.included, [])
        another.since.value = date(2026, 8, 1)
        self.assertEqual(another.events.included, ["cobra-42"])
        another.events.included = ["cobra-42", "cobra-43"]
        self.assertEqual(set(self.workspace().events.included), {"cobra-42", "cobra-43"})

    def test_all_skipped_remains_empty_after_reload(self):
        workspace = self.workspace()
        workspace.since.value = date(2026, 8, 1)
        workspace.until.value = date(2026, 9, 30)
        workspace.attendance.value = 0
        workspace.events.included = []
        restored = self.workspace()
        self.assertEqual(restored.events.included, [])
        self.assertEqual(restored.skipped_ids, {"cobra-42", "cobra-43"})

    def test_saved_custom_types_survive_without_matching_current_events(self):
        save_selection(self.database, {"event_types": ["custom event type"]})
        workspace = self.workspace()
        self.assertEqual(workspace.event_types.value, ["custom event type"])
        self.assertIn("custom event type", workspace.event_types.options.values())

    def test_write_failure_is_reported_without_breaking_analysis(self):
        workspace = self.workspace()
        with (
            patch("analyser.app.save_selection", side_effect=OSError("read-only directory")),
            self.assertLogs("analyser.app", level="WARNING"),
        ):
            workspace.attendance.value = 0
        self.assertEqual(workspace.attendance.value, 0)
        self.assertIn("Could not save tournament selection", workspace.status.object)


if __name__ == "__main__":
    unittest.main()
