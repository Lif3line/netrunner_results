"""Dataframe calc."""

from dataclasses import dataclass
from math import ceil, nan, sqrt
from statistics import NormalDist

import pandas as pd

from analyser.aliases import cross_event_keys

ENTRY_KEY = ["tournament_id", "player_id"]
DECK_KEY = [*ENTRY_KEY, "side"]
WILSON_Z = NormalDist().inv_cdf(0.975)


@dataclass(frozen=True)
class Selection:
    tournament_ids: tuple[str, ...]
    phase: str = "all"
    cohort: str = "all"
    top_n: int = 16
    loser_min_winrate: float = 0.0
    top_percent: int = 25


def known_identity(identities):
    return ~identities.fillna("").str.strip().str.casefold().isin(
        ("", "unknown", "unknown identity")
    )


def wilson_interval(wins, games):
    """95% Wilson score bounds for wins/games; NaNs when no games were played"""
    if games < 0 or not 0 <= wins <= games:
        raise ValueError("Expected 0 <= wins <= games")
    if games == 0:
        return nan, nan
    proportion = wins / games
    z_squared = WILSON_Z**2
    denominator = 1 + z_squared / games
    centre = (proportion + z_squared / (2 * games)) / denominator
    radius = (
        WILSON_Z
        * sqrt(proportion * (1 - proportion) / games + z_squared / (4 * games**2))
        / denominator
    )
    return max(0.0, centre - radius), min(1.0, centre + radius)


def analyse(data, selection, aliases=None):
    events = data["tournaments"][data["tournaments"]["id"].isin(selection.tournament_ids)]
    entries = data["entries"][data["entries"]["tournament_id"].isin(events["id"])].copy()
    entries["eligible"] = True
    if selection.cohort in ("cut", "any_cut"):
        entries["eligible"] = pd.to_numeric(entries["cut_rank"], errors="coerce").gt(0)
    elif selection.cohort in ("top", "any_top"):
        rank = pd.to_numeric(entries["swiss_rank"], errors="coerce")
        entries["eligible"] = rank.between(1, selection.top_n)
    elif selection.cohort in ("percent", "any_percent"):
        if not 1 <= selection.top_percent <= 100:
            raise ValueError("Swiss top percentage must be between 1 and 100")
        field_sizes = entries.groupby("tournament_id")["player_id"].transform("size")
        cutoffs = field_sizes.map(lambda size: ceil(size * selection.top_percent / 100))
        rank = pd.to_numeric(entries["swiss_rank"], errors="coerce")
        entries["eligible"] = rank.ge(1) & rank.le(cutoffs)
    elif selection.cohort != "all":
        raise ValueError(f"Unknown cohort: {selection.cohort}")
    if selection.cohort.startswith("any_"):
        keys = cross_event_keys(entries, aliases or {})
        entries["eligible"] = keys.isin(keys[entries.eligible])
    all_games = data["games"][data["games"]["tournament_id"].isin(events["id"])].copy()
    valid = all_games[all_games["result"].isin(["corp", "runner", "draw"])].copy()
    if selection.phase not in ("all", "swiss", "cut"):
        raise ValueError(f"Unknown phase: {selection.phase}")
    phased = (
        all_games if selection.phase == "all" else all_games[all_games.phase == selection.phase]
    )
    excluded = (
        phased.loc[~phased.result.isin(["corp", "runner", "draw"]), "result"]
        .value_counts()
        .to_dict()
    )
    known = pd.Series(True, index=valid.index)
    for side in ("corp", "runner"):
        players = entries.loc[known_identity(entries[f"{side}_identity"]), ENTRY_KEY]
        game_players = pd.MultiIndex.from_frame(valid[["tournament_id", f"{side}_player_id"]])
        known &= game_players.isin(pd.MultiIndex.from_frame(players))
    unknown_games = valid[~known]
    if selection.phase != "all":
        unknown_games = unknown_games[unknown_games.phase == selection.phase]
    excluded["unknown_identity"] = len(unknown_games)
    valid = valid[known].copy()
    games = valid if selection.phase == "all" else valid[valid.phase == selection.phase].copy()

    # Evaluate the loser threshold once against all valid games in each tournament
    if selection.loser_min_winrate > 0:
        for side in ("corp", "runner"):
            rates = (
                valid.assign(win=valid.result.eq(side))
                .groupby(["tournament_id", f"{side}_player_id"])
                .win.mean()
            )
            games = games.join(
                rates.rename(f"{side}_rate"), on=["tournament_id", f"{side}_player_id"]
            )
        low_loser = (
            games.result.eq("corp") & games.runner_rate.lt(selection.loser_min_winrate)
        ) | (games.result.eq("runner") & games.corp_rate.lt(selection.loser_min_winrate))
        excluded["low_loser_winrate"] = int(low_loser.sum())
        games = games[~low_loser].copy()

    observations = []
    for side, opponent in (("corp", "runner"), ("runner", "corp")):
        side_games = games.rename(columns={f"{side}_player_id": "player_id"})
        side_games = side_games.merge(entries, on=ENTRY_KEY, validate="many_to_one")
        side_games = side_games[side_games.eligible].copy()
        opponents = entries[[*ENTRY_KEY, f"{opponent}_identity", f"{opponent}_faction"]].rename(
            columns={
                "player_id": f"{opponent}_player_id",
                f"{opponent}_identity": "opponent_identity",
                f"{opponent}_faction": "opponent_faction",
            }
        )
        side_games = side_games.merge(
            opponents, on=["tournament_id", f"{opponent}_player_id"], validate="many_to_one"
        )
        side_games["side"] = side
        side_games["identity"] = side_games[f"{side}_identity"]
        side_games["faction"] = side_games[f"{side}_faction"]
        side_games["win"] = side_games.result.eq(side).astype(int)
        side_games["draw"] = side_games.result.eq("draw").astype(int)
        observations.append(
            side_games[
                [
                    *DECK_KEY,
                    "game_id",
                    "phase",
                    "name",
                    "identity",
                    "faction",
                    "opponent_identity",
                    "opponent_faction",
                    "win",
                    "draw",
                ]
            ]
        )
    observations = pd.concat(observations, ignore_index=True)
    tracked_games = observations[["tournament_id", "game_id"]].drop_duplicates()
    excluded["outside_cohort"] = len(games) - len(tracked_games)
    games = games.merge(tracked_games, on=["tournament_id", "game_id"], validate="one_to_one")
    return {
        "events": events,
        "entries": entries[
            entries.eligible
            & (known_identity(entries.corp_identity) | known_identity(entries.runner_identity))
        ],
        "games": games,
        "observations": observations,
        "excluded": excluded,
    }


def aggregate(observations, columns):
    result = (
        observations.groupby(columns, dropna=False)
        .agg(games=("win", "size"), wins=("win", "sum"), draws=("draw", "sum"))
        .reset_index()
    )
    result["winrate"] = result.wins / result.games
    intervals = [wilson_interval(wins, games) for wins, games in zip(result.wins, result.games)]
    result["ci_lower"] = [lower for lower, _ in intervals]
    result["ci_upper"] = [upper for _, upper in intervals]
    return result


def identity_stats(analysis, side):
    observations = analysis["observations"]
    stats = aggregate(observations[observations.side == side], ["identity", "faction"])
    representation = analysis["entries"].groupby(f"{side}_identity").size()
    stats["entries"] = stats.identity.map(representation).fillna(0).astype(int)
    return stats.sort_values(["games", "identity"], ascending=[False, True])


def matchup_stats(analysis, side, identity):
    observations = analysis["observations"]
    matches = observations[(observations.side == side) & (observations.identity == identity)]
    return (
        aggregate(matches, ["opponent_identity", "opponent_faction"])
        .rename(columns={"opponent_identity": "identity", "opponent_faction": "faction"})
        .sort_values(["games", "identity"], ascending=[False, True])
    )


def deck_stats(data, analysis, side, identity=None, contains=None):
    entries = analysis["entries"]
    entries = entries[known_identity(entries[f"{side}_identity"])]
    if identity:
        entries = entries[entries[f"{side}_identity"] == identity]
    decks = data["decks"][data["decks"].side == side].merge(
        entries[[*ENTRY_KEY, "name", "swiss_rank", "cut_rank", f"{side}_identity"]],
        on=ENTRY_KEY,
        suffixes=("", "_player"),
        validate="many_to_one",
    )
    # Entry identities are shared with game analysis; Cobra deck titles can use different quotes
    decks["identity"] = decks[f"{side}_identity"]
    coverage = (len(decks), len(entries))
    if contains:
        keys = data["deck_cards"][data["deck_cards"].card_id == contains][DECK_KEY]
        decks = decks.merge(keys, on=DECK_KEY, validate="one_to_one")
    rates = aggregate(analysis["observations"], DECK_KEY)
    decks = decks.merge(rates, on=DECK_KEY, how="left", validate="one_to_one")
    for column in ("games", "wins", "draws"):
        decks[column] = decks[column].fillna(0).astype(int)
    cards = data["deck_cards"].merge(decks[DECK_KEY], on=DECK_KEY, validate="many_to_one")
    card_stats = (
        cards.groupby(["card_id", "title", "card_type"])
        .agg(
            decks=("quantity", "size"),
            copies=("quantity", "sum"),
            average_copies=("quantity", "mean"),
        )
        .reset_index()
    )
    card_stats["inclusion"] = card_stats.decks / len(decks) if len(decks) else 0.0
    return decks, card_stats.sort_values(["decks", "title"], ascending=[False, True]), coverage
