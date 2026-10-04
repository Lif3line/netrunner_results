import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analyser.__main__ import feedback_assets, main
from analyser.app import Workspace


class OptionalFeedbackTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.patch = patch("analyser.__main__.FEEDBACK_ASSETS", self.directory / "dist")
        self.assets = self.patch.start()
        self.addCleanup(self.patch.stop)

    def build_assets(self):
        self.assets.mkdir()
        for filename in ("loader.js", "overlay.js"):
            (self.assets / filename).write_text("void 0;", encoding="utf-8")

    def test_disabled_feedback_does_not_inspect_optional_files(self):
        with patch("pathlib.Path.open", side_effect=AssertionError("Unexpected file access")):
            self.assertIsNone(feedback_assets(False))

    def test_missing_incomplete_or_empty_assets_are_optional(self):
        with self.assertLogs("analyser.__main__", level="WARNING"):
            self.assertIsNone(feedback_assets(True))
        self.build_assets()
        self.assertEqual(feedback_assets(True), self.assets)
        (self.assets / "overlay.js").unlink()
        with self.assertLogs("analyser.__main__", level="WARNING"):
            self.assertIsNone(feedback_assets(True))
        (self.assets / "overlay.js").write_text("", encoding="utf-8")
        with self.assertLogs("analyser.__main__", level="WARNING"):
            self.assertIsNone(feedback_assets(True))

    def test_unreadable_assets_do_not_prevent_startup(self):
        with patch("pathlib.Path.open", side_effect=PermissionError("unreadable bundle")):
            with self.assertLogs("analyser.__main__", level="WARNING"):
                self.assertIsNone(feedback_assets(True))

    def test_server_starts_without_assets_even_when_feedback_was_requested(self):
        with (
            patch("sys.argv", ["analyser", "serve", "--feedback", "--no-browser"]),
            patch("analyser.__main__.initialise"),
            patch("panel.serve") as serve,
            patch("analyser.app.create_app", return_value="working app") as create_app,
            self.assertLogs("analyser.__main__", level="WARNING"),
        ):
            main()
            self.assertEqual(serve.call_args.args[0](), "working app")
        self.assertEqual(serve.call_args.kwargs["static_dirs"], {})
        self.assertFalse(create_app.call_args.kwargs["feedback"])

    def test_feedback_is_an_independent_module_not_a_blocking_script(self):
        workspace = Workspace(self.directory / "test.sqlite3")
        enabled = workspace.view(feedback=True)
        self.assertNotIn("agentation", enabled.config.js_files)
        self.assertEqual(enabled.config.js_modules["agentation"], "/feedback-assets/loader.js")
        disabled = workspace.view(feedback=False)
        self.assertNotIn("agentation", disabled.config.js_modules)


if __name__ == "__main__":
    unittest.main()
