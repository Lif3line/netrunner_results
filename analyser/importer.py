"""Cobra/Aesop's results + public Cobra decklists."""

import json
import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

import requests

from analyser.cache import CachedSession
from analyser.store import DEFAULT_DB, cached_tournament, json_text, save_tournament

FACTIONS = {
    "haas-bioroid": "HB",
    "jinteki": "Jinteki",
    "nbn": "NBN",
    "weyland-consortium": "Weyland",
    "anarch": "Anarch",
    "criminal": "Criminal",
    "shaper": "Shaper",
    "adam": "Mini-faction",
    "apex": "Mini-faction",
    "sunny-lebeau": "Mini-faction",
    "neutral-corp": "Neutral Corp",
    "neutral-runner": "Neutral Runner",
}


def source_url(value):
    parsed = urlsplit(value.strip())
    if parsed.scheme not in ("http", "https"):
        return None
    parts = parsed.path.strip("/").split("/")
    if parsed.hostname == "tournaments.nullsignal.games":
        identifier = parts[1] if len(parts) > 1 and parts[0] == "tournaments" else parts[0]
        identifier = identifier.removesuffix(".json")
        if len(parts) > 1 and parts[0] == "tournaments" and identifier.isdigit():
            return f"https://tournaments.nullsignal.games/tournaments/{identifier}"
        if re.fullmatch(r"[A-Za-z0-9]{4}", identifier) and identifier.lower() != "help":
            return f"https://tournaments.nullsignal.games/{identifier.upper()}"
    if parsed.hostname in ("aesopstables.net", "www.aesopstables.net"):
        identifier = parts[0].removesuffix(".json")
        if identifier.isdigit():
            return f"https://www.aesopstables.net/{identifier}"
    return None


def get(session, url, **kwargs):
    response = session.get(url, timeout=30, **kwargs)
    response.raise_for_status()
    return response


def resolve_source(value, session):
    url = source_url(value)
    if not url:
        raise ValueError("Enter a Cobra or Aesop's tournament URL")
    if "nullsignal.games" in url and "/tournaments/" not in url:
        url = source_url(get(session, url).url)
        if not url or "/tournaments/" not in url:
            raise ValueError("Cobra shortcode did not resolve to a tournament")
    return url


def faction(value):
    return FACTIONS.get(str(value).lower().replace("_", "-"), value or "Unknown")


class SourceInfo(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.hidden = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = False

    def handle_data(self, value):
        if not self.hidden and value.strip():
            self.text.append(value.strip())


def source_metadata(html):
    parser = SourceInfo()
    parser.feed(html)
    text = "\n".join(parser.text)
    metadata = {}
    for field, label in (
        ("format", "Game Format"),
        ("level", "Tournament Type"),
        ("banlist", "Deckbuilding Restrictions"),
    ):
        match = re.search(rf"\b{label}:\s*([^\n]+)", text, flags=re.IGNORECASE)
        if match:
            metadata[field] = match.group(1).strip().lower()
    if "banlist" in metadata:
        metadata["banlist"] = normalise_banlist(metadata["banlist"])
    return metadata


def normalise_banlist(value):
    if value is None:
        return None
    value = str(value).strip()
    if value.casefold() in ("", "unknown", "---unknown---"):
        return None
    value = re.sub(
        r"^(?:Standard|Startup|Sunset) (?:Ban ?List|Balance Update)\s+",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return re.sub(r"^(?:Standard|NAPD) MWL\s+", "MWL-", value, flags=re.IGNORECASE)


def game_result(table, corp, runner):
    if corp.get("id") is None or runner.get("id") is None:
        return "bye"
    if table.get("intentionalDraw"):
        return "intentional_draw"
    if table.get("twoForOne"):
        return "two_for_one"
    if table.get("eliminationGame") and ("winner" in corp or "winner" in runner):
        if corp.get("winner") is True and runner.get("winner") is not True:
            return "corp"
        if runner.get("winner") is True and corp.get("winner") is not True:
            return "runner"
        return "unknown"
    scores = (corp.get("corpScore"), runner.get("runnerScore"))
    return {(3, 0): "corp", (0, 3): "runner", (1, 1): "draw"}.get(scores, "unknown")


def parse_tournament(raw, url, metadata=None):
    metadata = metadata or {}
    source = "cobra" if "nullsignal.games" in url else "aesop"
    event_id = f"{source}-{url.rsplit('/', 1)[-1]}"
    event_date = str(metadata.get("date") or raw["date"])[:10]
    date.fromisoformat(event_date)
    cut = {str(player["id"]): player.get("rank") for player in raw["eliminationPlayers"]}
    entries = []
    for player in raw["players"]:
        entries.append(
            {
                "tournament_id": event_id,
                "player_id": str(player["id"]),
                "name": player["name"],
                "swiss_rank": player.get("rank"),
                "cut_rank": cut.get(str(player["id"])),
                "corp_identity": player.get("corpIdentity") or "Unknown identity",
                "corp_faction": faction(player.get("corpFaction")),
                "runner_identity": player.get("runnerIdentity") or "Unknown identity",
                "runner_faction": faction(player.get("runnerFaction")),
            }
        )
    player_ids = {entry["player_id"] for entry in entries}
    if len(player_ids) != len(entries):
        raise ValueError("Tournament contains duplicate player IDs")
    games = []
    for round_number, tables in enumerate(raw["rounds"], start=1):
        for table in tables:
            one, two = table["player1"], table["player2"]
            bye = one.get("id") is None or two.get("id") is None
            if bye:
                # Cobra's SSS byes have no assigned side; keep one exclusion record
                pairings = [(one, two)]
            elif "role" in one or "role" in two:
                if one.get("role") == "corp" or two.get("role") == "runner":
                    pairings = [(one, two)]
                elif one.get("role") == "runner" or two.get("role") == "corp":
                    pairings = [(two, one)]
                else:
                    raise ValueError(f"Unknown player roles in round {round_number}")
            else:
                pairings = [(two, one), (one, two)]
            for corp, runner in pairings:
                corp_id = str(corp["id"]) if corp.get("id") is not None else None
                runner_id = str(runner["id"]) if runner.get("id") is not None else None
                if bye:
                    corp_id = runner_id = None
                for player_id in (corp_id, runner_id):
                    if player_id is not None and player_id not in player_ids:
                        raise ValueError(
                            f"Round {round_number} references missing player {player_id}"
                        )
                games.append(
                    {
                        "tournament_id": event_id,
                        "game_id": len(games) + 1,
                        "phase": "cut" if table.get("eliminationGame") else "swiss",
                        "round": round_number,
                        "table_number": table.get("table", table.get("tableNumber")),
                        "corp_player_id": corp_id,
                        "runner_player_id": runner_id,
                        "result": game_result(table, corp, runner),
                    }
                )
    event = {
        "id": event_id,
        "url": url,
        "name": metadata.get("name") or raw["name"],
        "date": event_date,
        "format": metadata.get("format") or raw.get("format") or "unknown",
        "level": metadata.get("level"),
        "location": metadata.get("location"),
        "size": len(entries),
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "deck_status": "Not requested",
        "decks_checked_at": None,
        "raw_json": json_text({"results": raw, "metadata": metadata}),
    }
    return event, entries, games


class DeckInputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.payloads = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("id") in ("corp_deck", "runner_deck"):
            value = attrs.get("value")
            if value:
                payload = json.loads(value)
                if payload:
                    self.payloads[attrs["id"].removesuffix("_deck")] = payload


def parse_decks(html, event_id, player_id, url):
    parser = DeckInputs()
    parser.feed(html)
    decks = []
    for side, payload in parser.payloads.items():
        details = payload["details"]
        if str(details["player_id"]) != player_id or details["side_id"] != side:
            raise ValueError("Deck page does not match the requested player and side")
        cards = []
        for card in payload["cards"]:
            quantity = card["quantity"]
            if not isinstance(quantity, int) or quantity < 1:
                raise ValueError(f"Invalid quantity for {card['title']}")
            cards.append(
                {
                    "card_id": str(card["nrdb_card_id"]),
                    "title": card["title"],
                    "quantity": quantity,
                    "card_type": card.get("card_type_id") or "unknown",
                }
            )
        if not cards:
            raise ValueError("Published deck contains no cards")
        decks.append(
            {
                "tournament_id": event_id,
                "player_id": player_id,
                "side": side,
                "name": details.get("name") or details["identity_title"],
                "identity": details["identity_title"],
                "source_url": url,
                "raw_json": json_text(payload),
                "cards": cards,
            }
        )
    if not decks:
        raise ValueError("Cobra advertised public decks but returned no readable decklists")
    return decks


def fetch_decks(event, entries, session, progress):
    data = get(session, event["url"] + "/players/standings_data").json()
    visible = set()
    entries_by_id = {entry["player_id"]: entry for entry in entries}
    for stage in data["stages"]:
        for standing in stage["standings"]:
            player = standing.get("player")
            if not player:
                continue
            player_id = str(player["id"])
            if standing.get("policy", {}).get("view_decks"):
                visible.add(player_id)
            entry = entries_by_id.get(player_id)
            if entry:
                for side in ("corp", "runner"):
                    identity = player.get(f"{side}_id") or {}
                    if identity.get("faction"):
                        entry[f"{side}_faction"] = faction(identity["faction"])
    decks = []
    for index, player_id in enumerate(sorted(visible), start=1):
        if player_id not in entries_by_id:
            raise ValueError(f"Public deck references missing player {player_id}")
        progress(f"Decks {index}/{len(visible)} · {event['name']}")
        url = f"{event['url']}/players/{player_id}/view_decks"
        decks.extend(parse_decks(get(session, url).text, event["id"], player_id, url))
    return decks


def import_tournament(
    url, path=DEFAULT_DB, *, metadata=None, include_decks=True, refresh=False, progress=None
):
    progress = progress or (lambda message: None)
    with CachedSession(path, refresh=refresh) as session:
        canonical = resolve_source(url, session)
        cached = cached_tournament(canonical, path)
        if (
            cached
            and not refresh
            and (
                not include_decks
                or cached["decks_checked_at"]
                or not cached["id"].startswith("cobra-")
            )
        ):
            progress(f"Using cached {cached['name']} · fetched {cached['imported_at']}")
            return cached
        previous = json.loads(cached["raw_json"]) if cached else None
        if previous:
            metadata = {**previous["metadata"], **(metadata or {})}
        if cached and not refresh:
            raw = previous["results"]
            progress(f"Using cached results; fetching missing decklists for {cached['name']}")
        else:
            progress(f"{'Refreshing' if refresh else 'Fetching'} {canonical}")
            raw = get(session, canonical + ".json").json()
        if "nullsignal.games" in canonical and (
            refresh
            or ((metadata or {}).get("format") in (None, "unknown") and not raw.get("format"))
        ):
            try:
                info = source_metadata(get(session, canonical + "/info").text)
                metadata = {
                    **info,
                    **{
                        key: value
                        for key, value in (metadata or {}).items()
                        if value not in (None, "unknown")
                    },
                }
                if info.get("banlist"):
                    metadata["banlist"] = info["banlist"]
            except requests.RequestException as error:
                progress(f"Could not read source metadata; keeping unknown fields: {error}")
        event, entries, games = parse_tournament(raw, canonical, metadata)
        decks = None
        if include_decks and event["id"].startswith("cobra-"):
            decks = fetch_decks(event, entries, session, progress)
            event["deck_status"] = f"{len(decks)} public lists"
            event["decks_checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        elif include_decks:
            event["deck_status"] = "Aesop's decklists not supported"
        save_tournament(event, entries, games, decks, path)
        return event
