"""Cross-event matching via player alias"""

import json
import unicodedata
from pathlib import Path

DEFAULT_ALIASES = Path(__file__).resolve().parent.parent / "players.json"


def normalise_name(name):
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def load_aliases(path=DEFAULT_ALIASES):
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        players = json.load(handle)
    aliases = {}
    for identifier, player in players.items():
        for name in [player.get("nrdb_name", ""), *player.get("aliases", [])]:
            key = normalise_name(name)
            if not key:
                continue
            match = f"alias:{identifier}"
            if key in aliases and aliases[key] != match:
                aliases[key] = None
            else:
                aliases[key] = match
    return aliases


def cross_event_keys(entries, aliases):
    keys = entries.name.map(normalise_name).map(
        lambda name: aliases.get(name, f"name:{name}") if name else None
    )
    # A shared name within one event cannot actually identify a single person
    duplicate = entries.assign(match_key=keys).duplicated(
        ["tournament_id", "match_key"], keep=False
    )
    ambiguous = set(keys[duplicate].dropna())
    separate = keys.isna() | keys.isin(ambiguous)
    keys.loc[separate] = (
        "entry:" + entries.loc[separate, "tournament_id"] + ":" + entries.loc[separate, "player_id"]
    )
    return keys
