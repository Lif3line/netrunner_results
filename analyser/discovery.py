"""ABR discovery like original importer; ambiguous sources stick around."""

import json
import logging
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests

from analyser.cache import CachedSession, CacheMiss
from analyser.event_types import normalise_type
from analyser.importer import get, normalise_banlist, resolve_source, source_metadata, source_url
from analyser.store import DEFAULT_DB, connect

LOGGER = logging.getLogger(__name__)


class SourceLinks(HTMLParser):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.links = set()

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                link = source_url(urljoin(self.url, href))
                if link:
                    self.links.add(link)


def find_sources(event, session, progress):
    sources = set()
    if event.get("matchdata"):
        try:
            upload = get(session, f"https://alwaysberunning.net/tjsons/{event['id']}.json").json()
            for link in upload.get("links", []):
                if link.get("rel", "").lower() == "uploadedfrom" and source_url(
                    link.get("href", "")
                ):
                    sources.add(resolve_source(link["href"], session))
        except (requests.RequestException, ValueError, AttributeError) as error:
            progress(f"{event['title']}: upload lookup failed ({error}); checking event page")
        if sources:
            return sources
    links = SourceLinks(event["url"])
    links.feed(get(session, event["url"]).text)
    return {resolve_source(link, session) for link in sorted(links.links)}


def cached_banlists(path=DEFAULT_DB):
    """Recover display metadata from cache."""
    with connect(path) as connection:
        imported = list(connection.execute("SELECT url, raw_json FROM tournaments"))
        searches = list(
            connection.execute(
                "SELECT request_url, body FROM http_cache WHERE request_url LIKE ? ORDER BY fetched_at DESC",
                ("https://alwaysberunning.net/api/tournaments?%",),
            )
        )
    labels = {}
    for event in imported:
        metadata = json.loads(event["raw_json"]).get("metadata", {})
        labels[event["url"]] = normalise_banlist(metadata.get("banlist"))
    seen = set()
    with CachedSession(path, offline=True) as session:
        for search in searches:
            try:
                events = json.loads(search["body"])
                if not isinstance(events, list):
                    raise ValueError("Expected an event list")
            except (ValueError, TypeError) as error:
                LOGGER.warning(
                    "Cannot read cached banlists from %s: %s", search["request_url"], error
                )
                continue
            for event in events:
                if not isinstance(event, dict) or event.get("id") is None or event["id"] in seen:
                    continue
                seen.add(event["id"])
                label = normalise_banlist(event.get("mwl"))
                if not label:
                    continue
                try:
                    sources = find_sources(event, session, lambda _: None)
                except CacheMiss:
                    continue
                except (ValueError, KeyError, AttributeError) as error:
                    LOGGER.warning(
                        "Cannot resolve cached banlist for ABR %s: %s", event["id"], error
                    )
                    continue
                if len(sources) == 1:
                    url = next(iter(sources))
                    if url in labels and labels[url] is None:
                        labels[url] = label
        # Cobra's deckbuilding restrictions over ABR labels
        for url in labels:
            if "nullsignal.games" not in url:
                continue
            try:
                label = source_metadata(session.get(url + "/info").text).get("banlist")
            except CacheMiss:
                continue
            if label:
                labels[url] = label
    return labels


def discover(
    since,
    until=None,
    min_players=32,
    format_name="standard",
    event_types=(),
    progress=None,
    *,
    path=DEFAULT_DB,
    refresh=False,
):
    until = until or date.today()
    progress = progress or (lambda message: None)
    if since > until or until > date.today():
        raise ValueError("Choose a date range ending today or earlier")
    found, warnings, seen = [], [], set()
    with CachedSession(path, refresh=refresh) as session:
        events = get(
            session,
            "https://alwaysberunning.net/api/tournaments",
            params={
                "concluded": 1,
                "start": since.strftime("%Y.%m.%d."),
                "end": until.strftime("%Y.%m.%d."),
            },
        ).json()
        if not isinstance(events, list):
            raise ValueError("ABR returned an unexpected tournament list")
        for event in events:
            try:
                event_date = datetime.strptime(event["date"], "%Y.%m.%d.").date()
                if not event.get("concluded") or not since <= event_date <= until:
                    continue
                if event.get("approved") == -1:
                    continue
                if format_name != "All formats" and event.get("format") != format_name:
                    continue
                if int(event.get("players_count") or 0) < min_players:
                    continue
                if event_types and normalise_type(event.get("type")) not in event_types:
                    continue
                progress(f"Resolving {event['title']}")
                sources = find_sources(event, session, progress)
                if len(sources) != 1:
                    warnings.append(f"{event['title']}: {len(sources)} sources found; skipped")
                    continue
                url = next(iter(sources))
                if url in seen:
                    continue
                seen.add(url)
                found.append(
                    {
                        "url": url,
                        "name": event["title"],
                        "date": event_date.isoformat(),
                        "format": event["format"],
                        "level": event.get("type"),
                        "banlist": normalise_banlist(event.get("mwl")),
                        "location": event.get("location"),
                        "players": int(event.get("players_count") or 0),
                    }
                )
            except (requests.RequestException, ValueError, KeyError, TypeError) as error:
                warnings.append(f"{event.get('title', 'Unknown event')}: {error}")
    return found, warnings
