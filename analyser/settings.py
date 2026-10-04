"""Local, atomic storage for the last-used tournament selection"""

import json
import logging
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

FORMATS = ["All formats", "standard", "startup", "eternal", "unknown"]
LOGGER = logging.getLogger(__name__)


def selection_path(database):
    return Path(database).with_suffix(".selection.json")


def load_selection(database):
    path = selection_path(database)
    try:
        with path.open(encoding="utf-8") as handle:
            settings = json.load(handle)
        if not isinstance(settings, dict):
            raise ValueError("Expected a selection object")
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        LOGGER.warning("Cannot restore tournament selection from %s: %s", path, error)
        return {}

    invalid = set()
    for key in ("since", "until"):
        if key in settings and settings[key] is not None:
            try:
                settings[key] = date.fromisoformat(settings[key])
            except (TypeError, ValueError):
                invalid.add(key)
    if "min_players" in settings:
        value = settings["min_players"]
        if type(value) is not int or value < 0:
            invalid.add("min_players")
    if "format" in settings and settings["format"] not in FORMATS:
        invalid.add("format")
    for key in ("event_types", "skipped_ids"):
        if key in settings:
            value = settings[key]
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                invalid.add(key)
    for key in invalid:
        LOGGER.warning("Ignoring invalid saved selection field %s in %s", key, path)
        del settings[key]
    return settings


def save_selection(database, settings):
    destination = selection_path(database)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=".tmp-",
            suffix=".selection.json",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(settings, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
