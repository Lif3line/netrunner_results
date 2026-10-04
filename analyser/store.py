"""SQLite as working dataset; imports replace one event atomically."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

DEFAULT_DB = Path(__file__).parent / "data" / "analyser.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tournaments (
    id TEXT PRIMARY KEY, url TEXT NOT NULL, name TEXT NOT NULL, date TEXT NOT NULL,
    format TEXT NOT NULL, level TEXT, location TEXT, size INTEGER NOT NULL,
    imported_at TEXT NOT NULL, deck_status TEXT NOT NULL, raw_json TEXT NOT NULL,
    decks_checked_at TEXT
);
CREATE TABLE IF NOT EXISTS entries (
    tournament_id TEXT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    player_id TEXT NOT NULL, name TEXT NOT NULL, swiss_rank INTEGER, cut_rank INTEGER,
    corp_identity TEXT NOT NULL, corp_faction TEXT NOT NULL,
    runner_identity TEXT NOT NULL, runner_faction TEXT NOT NULL,
    PRIMARY KEY (tournament_id, player_id)
);
CREATE TABLE IF NOT EXISTS games (
    tournament_id TEXT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    game_id INTEGER NOT NULL, phase TEXT NOT NULL, round INTEGER NOT NULL,
    table_number INTEGER, corp_player_id TEXT, runner_player_id TEXT,
    result TEXT NOT NULL,
    PRIMARY KEY (tournament_id, game_id),
    FOREIGN KEY (tournament_id, corp_player_id) REFERENCES entries(tournament_id, player_id),
    FOREIGN KEY (tournament_id, runner_player_id) REFERENCES entries(tournament_id, player_id)
);
CREATE TABLE IF NOT EXISTS decks (
    tournament_id TEXT NOT NULL, player_id TEXT NOT NULL, side TEXT NOT NULL,
    name TEXT NOT NULL, identity TEXT NOT NULL, source_url TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (tournament_id, player_id, side),
    FOREIGN KEY (tournament_id, player_id)
        REFERENCES entries(tournament_id, player_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS deck_cards (
    tournament_id TEXT NOT NULL, player_id TEXT NOT NULL, side TEXT NOT NULL,
    card_id TEXT NOT NULL, title TEXT NOT NULL, quantity INTEGER NOT NULL CHECK (quantity > 0),
    card_type TEXT NOT NULL,
    PRIMARY KEY (tournament_id, player_id, side, card_id),
    FOREIGN KEY (tournament_id, player_id, side)
        REFERENCES decks(tournament_id, player_id, side) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS http_cache (
    request_url TEXT PRIMARY KEY, response_url TEXT NOT NULL,
    body BLOB NOT NULL, headers TEXT NOT NULL, encoding TEXT, fetched_at TEXT NOT NULL
);
"""


@contextmanager
def connect(path=DEFAULT_DB):
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialise(path=DEFAULT_DB):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            raise ValueError(f"Unsupported database version {version}")
        if version == 1:
            connection.execute("ALTER TABLE tournaments ADD COLUMN decks_checked_at TEXT")
            connection.execute(
                "UPDATE tournaments SET decks_checked_at = imported_at "
                "WHERE deck_status LIKE '%public lists' OR deck_status LIKE 'Retained;%'"
            )
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA user_version = 2")


def insert_rows(connection, table, rows):
    if not rows:
        return
    columns = list(rows[0])
    placeholders = ",".join("?" for _ in columns)
    connection.executemany(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
        [[row[column] for column in columns] for row in rows],
    )


def save_tournament(event, entries, games, decks, path=DEFAULT_DB):
    """None means deck refresh was disabled; retain lists for surviving entries."""
    with connect(path) as connection:
        retained_decks = []
        retained_cards = []
        if decks is None:
            previous = connection.execute(
                "SELECT decks_checked_at FROM tournaments WHERE id = ?", (event["id"],)
            ).fetchone()
            if previous:
                event = {**event, "decks_checked_at": previous["decks_checked_at"]}
            player_ids = {entry["player_id"] for entry in entries}
            for table, rows in (("decks", retained_decks), ("deck_cards", retained_cards)):
                rows.extend(
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM {table} WHERE tournament_id = ?", (event["id"],)
                    )
                    if row["player_id"] in player_ids
                )
            if retained_decks:
                event = {**event, "deck_status": "Retained; deck refresh skipped"}
        connection.execute("DELETE FROM tournaments WHERE id = ?", (event["id"],))
        insert_rows(connection, "tournaments", [event])
        insert_rows(connection, "entries", entries)
        insert_rows(connection, "games", games)
        if decks is None:
            insert_rows(connection, "decks", retained_decks)
            insert_rows(connection, "deck_cards", retained_cards)
        else:
            for deck in decks:
                cards = deck["cards"]
                row = {key: value for key, value in deck.items() if key != "cards"}
                insert_rows(connection, "decks", [row])
                insert_rows(
                    connection,
                    "deck_cards",
                    [
                        {
                            "tournament_id": event["id"],
                            "player_id": deck["player_id"],
                            "side": deck["side"],
                            **card,
                        }
                        for card in cards
                    ],
                )


def load_data(path=DEFAULT_DB):
    with connect(path) as connection:
        connection.execute("BEGIN")
        return {
            table: pd.read_sql_query(f"SELECT * FROM {table}", connection).drop(
                columns=["raw_json"], errors="ignore"
            )
            for table in ("tournaments", "entries", "games", "decks", "deck_cards")
        }


def json_text(value):
    return json.dumps(value, ensure_ascii=False)


def cached_tournament(url, path=DEFAULT_DB):
    with connect(path) as connection:
        row = connection.execute("SELECT * FROM tournaments WHERE url = ?", (url,)).fetchone()
        return dict(row) if row else None
