"""Browser controls and presentation; calculations live in metrics.py."""

import asyncio
import logging
from datetime import date, timedelta
from html import escape

import pandas as pd
import panel as pn
from bokeh.models import NumberFormatter

from analyser import APP_TITLE
from analyser.aliases import load_aliases
from analyser.charts import winrate_chart
from analyser.discovery import cached_banlists, discover
from analyser.event_types import EVENT_TYPES, normalise_type
from analyser.importer import import_tournament
from analyser.metrics import (
    Selection,
    analyse,
    deck_stats,
    identity_stats,
    known_identity,
    matchup_stats,
)
from analyser.settings import FORMATS, load_selection, save_selection
from analyser.store import DEFAULT_DB, initialise, load_data
from analyser.tournament_picker import TournamentPicker

pn.extension("tabulator")
LOGGER = logging.getLogger(__name__)
CONTROL_PANEL_STYLES = {
    "background": "#f1f8f6",
    "border": "1px solid #cbdcd9",
    "border-radius": "8px",
}


def table(frame, *, compact=False, **kwargs):
    return pn.widgets.Tabulator(
        frame.reset_index(drop=True),
        show_index=False,
        editors={column: None for column in frame.columns},
        sizing_mode="fixed" if compact else "stretch_width",
        layout="fit_data_table" if compact else "fit_data_stretch",
        pagination="local",
        page_size=12,
        **kwargs,
    )


def message(text):
    return pn.pane.Markdown(
        text,
        styles={"color": "#617185"},
        stylesheets=["p { margin: 0; }"],
        margin=(3, 10),
        sizing_mode="stretch_width",
    )


def tile(label, value, detail):
    return pn.pane.HTML(
        f'<div class="stat"><div class="label">{escape(label)}</div>'
        f'<div class="value">{escape(str(value))}</div>'
        f'<div class="detail">{escape(detail)}</div></div>',
        stylesheets=[
            """
            .stat { background: white; border: 1px solid #e2e8ef; border-radius: 10px;
                    padding: 18px; }
            .label { color: #617185; font-size: 12px; letter-spacing: .06em; text-transform: uppercase; }
            .value { color: #192e40; font-size: 34px; font-weight: 650; margin: 8px 0; }
            .detail { color: #617185; font-size: 12px; }
        """
        ],
        sizing_mode="stretch_width",
    )


class Workspace:
    def __init__(self, path=DEFAULT_DB):
        self.path = path
        initialise(path)
        saved = load_selection(path)
        self.data = load_data(path)
        self.banlists = cached_banlists(path)
        self.aliases = load_aliases()
        self.loading = False
        self.found = []
        self.catalog = {}
        self.skipped_ids = set(saved.get("skipped_ids", []))
        self.since = pn.widgets.DatePicker(
            name="From", value=saved.get("since", date.today() - timedelta(days=180)), width=190
        )
        self.until = pn.widgets.DatePicker(
            name="Through", value=saved.get("until", date.today()), width=190
        )
        self.attendance = pn.widgets.IntInput(
            name="Minimum attendance",
            value=saved.get("min_players", 32),
            start=0,
            step=8,
            width=190,
        )
        self.format = pn.widgets.Select(
            name="Format",
            options=FORMATS,
            value=saved.get("format", "standard"),
            width=210,
        )
        types = dict(EVENT_TYPES)
        for name in saved.get("event_types", []):
            if name not in types.values():
                types[name.capitalize()] = name
        self.event_types = pn.widgets.MultiChoice(
            name="Event types",
            options=types,
            value=saved.get("event_types", []),
            placeholder="All event types",
            width=350,
        )
        self.events = TournamentPicker(sizing_mode="stretch_width")
        self.selection_note = message("")
        self.phase = pn.widgets.RadioButtonGroup(
            name="Games", options={"All games": "all", "Swiss": "swiss", "Cut": "cut"}, value="all"
        )
        self.show_intervals = pn.widgets.Checkbox(
            name="Show 95% Wilson intervals",
            value=True,
            align="center",
            stylesheets=[":host .bk-label { top: 0 !important; }"],
        )
        self.cohort = pn.widgets.Select(
            name="Player cohort",
            options={
                "Whole field": "all",
                "Made cut · this tournament": "cut",
                "Made cut · any included tournament": "any_cut",
                "Swiss top N · this tournament": "top",
                "Swiss top N · any included tournament": "any_top",
                "Swiss top % · this tournament": "percent",
                "Swiss top % · any included tournament": "any_percent",
            },
            width=380,
        )
        self.top_n = pn.widgets.IntInput(
            name="Swiss top N",
            value=16,
            start=1,
            end=1000,
            visible=False,
            width=self.cohort.width,
            stylesheets=["input.bk-input { height: 34px; }"],
        )
        self.top_percent = pn.widgets.IntInput(
            name="Swiss top (%)",
            value=25,
            start=1,
            end=100,
            visible=False,
            description="Percentage of each tournament's full field, rounded up; tied qualifying ranks are included",
            width=self.cohort.width,
            stylesheets=["input.bk-input { height: 34px; }"],
        )
        self.loser_rate = pn.widgets.FloatSlider(
            name="Exclude wins over opponents below (%)",
            start=0,
            end=50,
            step=1,
            value=0,
            sizing_mode="stretch_width",
        )
        self.summary = pn.Row(sizing_mode="stretch_width")
        self.overview = pn.Column(sizing_mode="stretch_width")
        self.deck_view = pn.Column(sizing_mode="stretch_width")
        self.library = pn.Column(sizing_mode="stretch_width")
        self.notes = pn.pane.Markdown(sizing_mode="stretch_width")
        self.status = pn.pane.Alert(
            "Use cached data, or find tournaments to add to the field.",
            alert_type="light",
            sizing_mode="stretch_width",
        )
        self.log = pn.widgets.TextAreaInput(
            name="Import log", disabled=True, height=130, sizing_mode="stretch_width"
        )
        self.urls = pn.widgets.TextAreaInput(
            name="Tournament URLs · one per line",
            placeholder="https://tournaments.nullsignal.games/tournaments/…",
            height=100,
            sizing_mode="stretch_width",
        )
        self.include_decks = pn.widgets.Checkbox(name="Fetch public Cobra decklists", value=True)
        self.import_button = pn.widgets.Button(name="Load URLs", button_type="primary")
        self.discover_button = pn.widgets.Button(name="Find tournaments", button_type="primary")
        self.refresh_search = pn.widgets.Button(name="Force Refresh")
        self.import_found = pn.widgets.Button(name="Load included", button_type="primary")
        self.refresh_included = pn.widgets.Button(name="Refresh included from source")
        self.deck_side = pn.widgets.RadioButtonGroup(
            options={"Corp": "corp", "Runner": "runner"}, value="corp"
        )
        self.deck_identity = pn.widgets.Select(name="Identity", options={"All identities": None})
        self.deck_card = pn.widgets.Select(name="Contains card", options={"Any card": None})
        self.deck_picker = pn.widgets.Select(
            name="Read a deck", options={}, sizing_mode="stretch_width"
        )
        self.deck_detail = pn.Column(sizing_mode="stretch_width")
        self.analysis = None

        self.import_button.on_click(self.import_urls)
        self.discover_button.on_click(self.find_events)
        self.refresh_search.on_click(self.refresh_discovery)
        self.import_found.on_click(self.import_selection)
        self.refresh_included.on_click(self.refresh_selection)
        self.events.param.watch(self.select_events, "included")
        for widget in (self.since, self.until, self.attendance, self.format, self.event_types):
            widget.param.watch(self.change_filters, "value")
        for widget in (self.phase, self.cohort, self.top_n, self.top_percent):
            widget.param.watch(self.refresh, "value")
        self.show_intervals.param.watch(self.update_intervals, "value")
        self.loser_rate.param.watch(self.refresh, "value_throttled")
        for widget in (self.deck_side, self.deck_identity, self.deck_card):
            widget.param.watch(self.refresh_decks, "value")
        self.deck_picker.param.watch(self.show_deck, "value")
        self.filter_events()
        self.refresh_library()

    def view(self, feedback=False):
        imports = pn.Tabs(
            (
                "Discover tournaments",
                pn.Column(
                    pn.Row(self.discover_button, self.refresh_search),
                    message(
                        "Find uses cached [ABR](https://alwaysberunning.net)/Cobra source responses where available. Force Refresh explicitly checks the websites again."
                    ),
                ),
            ),
            ("Specific URLs", pn.Column(self.urls, self.import_button)),
            sizing_mode="stretch_width",
            dynamic=True,
        )
        template = pn.template.FastListTemplate(
            title=APP_TITLE,
            js_modules={"agentation": "/feedback-assets/loader.js"} if feedback else {},
            accent_base_color="#227e76",
            header_background="#192e40",
            main_layout=None,
            theme="default",
            theme_toggle=False,
            raw_css=["body { background: #f3f5f8; }"],
            main=[
                pn.Column(
                    pn.pane.Markdown("## 1 · Find tournaments"),
                    pn.FlexBox(
                        self.since, self.until, self.attendance, self.format, self.event_types
                    ),
                    imports,
                    sizing_mode="stretch_width",
                ),
                pn.Column(
                    pn.pane.Markdown("## 2 · Choose your field"),
                    self.events,
                    self.selection_note,
                    pn.Row(self.import_found, self.refresh_included, self.include_decks),
                    self.status,
                    pn.Accordion(
                        ("Import log", self.log),
                        active=[],
                        margin=(5, 10),
                        sizing_mode="stretch_width",
                        stylesheets=[
                            ".accordion { margin-left: 0 !important; margin-right: 0 !important; }"
                        ],
                    ),
                    sizing_mode="stretch_width",
                ),
                pn.Column(
                    pn.pane.Markdown("## 3 · Analysis options"),
                    pn.Row(self.phase, self.show_intervals),
                    pn.FlexBox(self.cohort, self.top_n, self.top_percent),
                    pn.Column(
                        self.loser_rate,
                        message(
                            "Opponent win rates use all valid games on that side in the same tournament. 0 disables this exclusion."
                        ),
                        sizing_mode="stretch_width",
                        margin=(4, 10),
                        styles=CONTROL_PANEL_STYLES,
                    ),
                    message(
                        "Cross-event cohorts rely on aliases/naming, this is somewhat unreliable"
                    ),
                    sizing_mode="stretch_width",
                ),
                self.summary,
                pn.Tabs(
                    ("Overview & matchups", pn.Column(self.overview, self.notes)),
                    (
                        "Decks & cards",
                        pn.Column(
                            pn.Row(self.deck_side, self.deck_identity, self.deck_card),
                            self.deck_view,
                            self.deck_picker,
                            self.deck_detail,
                        ),
                    ),
                    ("Cached library", self.library),
                    sizing_mode="stretch_width",
                    dynamic=True,
                ),
            ],
        )
        return template

    def persist_selection(self):
        settings = {
            "since": self.since.value.isoformat() if self.since.value else None,
            "until": self.until.value.isoformat() if self.until.value else None,
            "min_players": self.attendance.value or 0,
            "format": self.format.value,
            "event_types": list(self.event_types.value),
            "skipped_ids": sorted(self.skipped_ids),
        }
        try:
            save_selection(self.path, settings)
        except OSError as error:
            LOGGER.warning("Could not save tournament selection: %s", error)
            self.status.alert_type = "warning"
            self.status.object = f"Could not save tournament selection: {escape(str(error))}"

    def change_filters(self, *_):
        self.filter_events()
        self.persist_selection()

    def filter_events(self, *_):
        candidates = {event["url"]: event for event in self.data["tournaments"].to_dict("records")}
        cached_urls = set(candidates)
        for event in self.found:
            if event["url"] not in candidates:
                source = "cobra" if "nullsignal.games" in event["url"] else "aesop"
                candidates[event["url"]] = {
                    **event,
                    "id": f"{source}-{event['url'].rsplit('/', 1)[-1]}",
                    "size": event["players"],
                }
        types = dict(EVENT_TYPES)
        for name in self.event_types.value:
            if name not in types.values():
                types[name.capitalize()] = name
        for event in candidates.values():
            name = normalise_type(event.get("level"))
            if name not in types.values():
                types[name.capitalize()] = name
        if self.event_types.options != types:
            self.event_types.options = types
        self.catalog = {}
        cards = []
        for event in sorted(candidates.values(), key=lambda item: item["date"], reverse=True):
            event["banlist"] = self.banlists.get(event["url"]) or event.get("banlist")
            if self.since.value and event["date"] < self.since.value.isoformat():
                continue
            if self.until.value and event["date"] > self.until.value.isoformat():
                continue
            if event["size"] < (self.attendance.value or 0):
                continue
            if self.format.value != "All formats" and event["format"] != self.format.value:
                continue
            if (
                self.event_types.value
                and normalise_type(event.get("level")) not in self.event_types.value
            ):
                continue
            self.catalog[event["id"]] = event
            cards.append(
                {
                    "id": event["id"],
                    "name": event["name"],
                    "date": event["date"],
                    "size": event["size"],
                    "cache": "Cached" if event["url"] in cached_urls else "Needs import",
                    "banlist": event["banlist"] or "unknown",
                }
            )
        self.loading = True
        self.events.cards = cards
        self.events.included = [card["id"] for card in cards if card["id"] not in self.skipped_ids]
        self.loading = False
        self.refresh()

    def select_events(self, *_):
        if self.loading:
            return
        visible = set(self.catalog)
        self.skipped_ids = (self.skipped_ids - visible) | (visible - set(self.events.included))
        self.refresh()
        self.persist_selection()

    def refresh(self, *_):
        if self.loading:
            return
        self.top_n.visible = self.cohort.value in ("top", "any_top")
        self.top_percent.visible = self.cohort.value in ("percent", "any_percent")
        selection = Selection(
            tuple(self.events.included),
            self.phase.value,
            self.cohort.value,
            self.top_n.value or 16,
            self.loser_rate.value / 100,
            top_percent=self.top_percent.value or 25,
        )
        self.analysis = analyse(self.data, selection, self.aliases)
        games, observations = self.analysis["games"], self.analysis["observations"]
        pending = set(self.events.included) - set(self.data["tournaments"].id)
        self.selection_note.object = (
            f"{len(self.events.included)} included · {len(pending)} need importing. "
            "Drag events between lists. Analysis uses loaded events only."
        )
        corp = observations[observations.side == "corp"]
        corp_rate = f"{corp.win.mean():.1%}" if len(corp) else "—"
        self.summary.objects = [
            tile("Tournaments", len(self.analysis["events"]), "In this selection"),
            tile("Player entries", len(self.analysis["entries"]), "In the selected cohort"),
            tile("Games", f"{len(games):,}", "After analysis exclusions"),
            tile("Corp win rate", corp_rate, f"{len(corp):,} tracked corp games"),
        ]
        if not len(games):
            self.overview.objects = [
                pn.pane.Alert(
                    "No games in this selection. Load included tournaments above, or adjust the field and analysis options.",
                    alert_type="light",
                )
            ]
        else:
            self.overview.objects = [self.side_view(side) for side in ("corp", "runner")]
        exclusions = ", ".join(
            f"{key.replace('_', ' ')}: {count:,}"
            for key, count in self.analysis["excluded"].items()
            if count
        )
        self.notes.object = f"**Excluded:** {exclusions or 'none'}. Win rate = wins / games; played draws remain in the denominator."
        self.notes.object += " 95% Wilson intervals approximate game-level uncertainty; shared players/events and cohort selection are not accounted for."
        self.refresh_decks()

    def update_intervals(self, *_):
        for pane in self.overview.select(pn.pane.Bokeh):
            for name in ("winrate_interval", "winrate_point"):
                for model in pane.object.select(name=name):
                    model.visible = self.show_intervals.value

    def side_view(self, side):
        stats = identity_stats(self.analysis, side)
        if stats.empty:
            return message(f"No tracked {side} games in this selection.")
        identity = pn.widgets.Select(
            name="",
            options=stats.identity.tolist(),
            width=500,
        )
        chart = winrate_chart(
            stats,
            side.title(),
            on_select=lambda value: setattr(identity, "value", value),
            show_intervals=self.show_intervals.value,
        )
        breakdown = pn.Column(sizing_mode="stretch_width")

        def update(*_):
            matches = matchup_stats(self.analysis, side, identity.value)
            matchup = winrate_chart(
                matches,
                "Against the field",
                show_intervals=self.show_intervals.value,
            )
            chart.height = matchup.height = max(320, 32 * max(len(stats), len(matches)) + 75)
            breakdown.objects = [
                pn.pane.Bokeh(
                    matchup,
                    sizing_mode="stretch_width",
                )
            ]

        identity.param.watch(update, "value")
        update()
        return pn.Card(
            identity,
            message(
                "Click an identity bar to inspect its matchups. Hover for wins, games and draws."
            ),
            pn.Row(
                pn.pane.Bokeh(chart, sizing_mode="stretch_width"),
                breakdown,
                sizing_mode="stretch_width",
            ),
            title=side.title(),
            collapsed=False,
            sizing_mode="stretch_width",
        )

    def refresh_decks(self, *_):
        if self.loading or self.analysis is None:
            return
        self.loading = True
        try:
            side = self.deck_side.value
            identities = self.analysis["entries"][f"{side}_identity"]
            ids = sorted(identities[known_identity(identities)].unique())
            old_identity = self.deck_identity.value
            self.deck_identity.options = {
                "All identities": None,
                **{identity: identity for identity in ids},
            }
            self.deck_identity.value = old_identity if old_identity in ids else None
            decks, cards, coverage = deck_stats(
                self.data, self.analysis, side, self.deck_identity.value
            )
            card_options = {"Any card": None, **dict(zip(cards.title, cards.card_id))}
            old_card = self.deck_card.value
            self.deck_card.options = card_options
            self.deck_card.value = old_card if old_card in card_options.values() else None
            if self.deck_card.value:
                decks, cards, coverage = deck_stats(
                    self.data, self.analysis, side, self.deck_identity.value, self.deck_card.value
                )
            known, eligible = coverage
            coverage_text = f"**{known} / {eligible} {side} lists available** in this identity/cohort selection · {len(decks)} match the card filter. Card inclusion uses these {len(decks)} published lists, not the whole field."
            if decks.empty:
                self.deck_view.objects = [
                    message(coverage_text),
                    pn.pane.Alert(
                        "No matching public decklists. Import with decklists enabled or change the filters.",
                        alert_type="light",
                    ),
                ]
            else:
                display = cards[["title", "decks", "inclusion", "average_copies"]].copy()
                display["inclusion"] = display.inclusion.map(lambda value: f"{value:.1%}")
                display["average_copies"] = display.average_copies.round(2)
                display.columns = [
                    "Card",
                    "Lists",
                    "Inclusion",
                    "Mean Copies",
                ]
                deck_display = decks[
                    [
                        "name_player",
                        "identity",
                        "swiss_rank",
                        "cut_rank",
                        "games",
                        "wins",
                        "winrate",
                    ]
                ].copy()
                deck_display["winrate"] = deck_display.winrate.map(
                    lambda value: f"{value:.1%}" if pd.notna(value) else "—"
                )
                for rank in ("swiss_rank", "cut_rank"):
                    deck_display[rank] = pd.to_numeric(deck_display[rank], errors="coerce").astype(
                        "Int64"
                    )
                deck_display.columns = [
                    "Player",
                    "Identity",
                    "Swiss",
                    "Cut",
                    "Games",
                    "Wins",
                    "Win rate",
                ]
                self.deck_view.objects = [
                    message(coverage_text),
                    pn.Tabs(
                        ("Card inclusion", table(display, compact=True)),
                        (
                            "Deck results",
                            table(
                                deck_display,
                                compact=True,
                                formatters={
                                    "Swiss": NumberFormatter(format="0", nan_format=""),
                                    "Cut": NumberFormatter(format="0", nan_format=""),
                                },
                            ),
                        ),
                        sizing_mode="stretch_width",
                    ),
                ]
            event_names = self.data["tournaments"].set_index("id")["name"].to_dict()
            choices = {}
            for _, row in decks.iterrows():
                if pd.notna(row.cut_rank) and row.cut_rank > 0:
                    placement = f"Cut #{int(row.cut_rank)}"
                elif pd.notna(row.swiss_rank) and row.swiss_rank > 0:
                    placement = f"Swiss #{int(row.swiss_rank)}"
                else:
                    placement = "Unranked"
                label = f"{row.name_player} · {row['name']} · {event_names[row.tournament_id]} [{row.player_id}] · {placement}"
                choices[label] = (row.tournament_id, row.player_id, side)
            previous = self.deck_picker.value
            self.deck_picker.options = choices
            self.deck_picker.value = (
                previous if previous in choices.values() else next(iter(choices.values()), None)
            )
            self.deck_picker.visible = bool(choices)
        finally:
            self.loading = False
        self.show_deck()

    def show_deck(self, *_):
        if self.loading:
            return
        if self.deck_picker.value is None:
            self.deck_detail.objects = []
            return
        tournament_id, player_id, side = self.deck_picker.value
        cards = self.data["deck_cards"]
        cards = cards[
            (cards.tournament_id == tournament_id)
            & (cards.player_id == player_id)
            & (cards.side == side)
        ].sort_values(["card_type", "title"])
        decks = self.data["decks"]
        deck = decks[
            (decks.tournament_id == tournament_id)
            & (decks.player_id == player_id)
            & (decks.side == side)
        ].iloc[0]
        text = (
            deck.identity
            + "\n\n"
            + "\n".join(f"{row.quantity} {row.title}" for row in cards.itertuples())
        )
        self.deck_detail.objects = [
            pn.pane.HTML(
                f'<h3>{escape(deck["name"])}</h3><p>{escape(deck.identity)} · {int(cards.quantity.sum())} cards · <a href="{escape(deck.source_url, quote=True)}" target="_blank" rel="noopener">View on Cobra ↗</a></p>'
            ),
            pn.widgets.TextAreaInput(
                name="",
                description="Decklist text",
                value=text,
                height=300,
                sizing_mode="stretch_width",
            ),
        ]

    def refresh_library(self):
        events = self.data["tournaments"].sort_values("date", ascending=False)
        self.library.objects = [
            pn.pane.Markdown(f"### Local library · {len(events)} tournaments"),
            table(
                events[
                    ["name", "date", "size", "format", "level", "deck_status", "imported_at", "url"]
                ]
            ),
            message(
                f"Database: `{self.path}` · Changes to analysis controls never fetch remote data."
            ),
        ]

    def report(self, text):
        self.status.object = escape(str(text))
        self.log.value = "\n".join((self.log.value.splitlines() + [str(text)])[-100:])

    def busy(self, value):
        self.import_button.disabled = value
        self.discover_button.disabled = value
        self.import_found.disabled = value
        self.refresh_included.disabled = value
        self.refresh_search.disabled = value
        self.include_decks.disabled = value
        self.import_button.loading = value

    async def run_imports(self, events, refresh=False):
        if not events:
            self.status.object = "Select an event or enter a tournament URL first."
            self.status.alert_type = "warning"
            return
        self.busy(True)
        self.status.alert_type = "light"
        loop = asyncio.get_running_loop()

        def progress(text):
            loop.call_soon_threadsafe(self.report, text)

        failures = 0
        ready_ids = set()
        try:
            for event in events:
                try:
                    result = await asyncio.to_thread(
                        import_tournament,
                        event["url"],
                        self.path,
                        metadata={
                            key: event[key]
                            for key in ("name", "date", "format", "level", "location", "banlist")
                            if key in event and pd.notna(event[key])
                        },
                        include_decks=self.include_decks.value,
                        refresh=refresh,
                        progress=progress,
                    )
                    self.report(
                        f"Ready: {result['name']} · {result['size']} entries · {result['deck_status']}"
                    )
                    ready_ids.add(result["id"])
                except Exception as error:
                    failures += 1
                    LOGGER.exception("Import failed: %s", event["url"])
                    self.report(f"Failed {event['url']}: {error}")
            self.data = load_data(self.path)
            self.banlists = cached_banlists(self.path)
            self.filter_events()
            self.refresh_library()
            self.status.alert_type = "warning" if failures else "success"
            self.status.object = f"{len(events) - failures} ready · {failures} failed."
            outside = len(ready_ids - set(self.catalog))
            if outside:
                self.status.object += (
                    f" {outside} loaded events are outside the current discovery filters."
                )
        finally:
            self.busy(False)

    async def import_urls(self, _):
        events = [
            {"url": url.strip()}
            for url in dict.fromkeys(self.urls.value.splitlines())
            if url.strip()
        ]
        await self.run_imports(events)

    async def import_selection(self, _):
        await self.run_imports(
            [self.catalog[key] for key in self.events.included if key in self.catalog]
        )

    async def refresh_selection(self, _):
        await self.run_imports(
            [self.catalog[key] for key in self.events.included if key in self.catalog], refresh=True
        )

    async def refresh_discovery(self, event):
        await self.find_events(event, refresh=True)

    async def find_events(self, _, refresh=False):
        self.busy(True)
        self.status.alert_type = "light"
        loop = asyncio.get_running_loop()

        def progress(text):
            loop.call_soon_threadsafe(self.report, text)

        try:
            if not self.since.value or not self.until.value:
                raise ValueError("Choose both discovery dates")
            self.found, warnings = await asyncio.to_thread(
                discover,
                self.since.value,
                self.until.value,
                self.attendance.value or 0,
                self.format.value,
                tuple(self.event_types.value),
                progress=progress,
                path=self.path,
                refresh=refresh,
            )
            for warning in warnings:
                self.report(warning)
            self.banlists = cached_banlists(self.path)
            self.filter_events()
            self.status.object = f"Found {len(self.found)} tournaments · {len(warnings)} skipped/errors. Choose the field below, then load included events."
            self.status.alert_type = "warning" if warnings else "success"
        except Exception as error:
            LOGGER.exception("Discovery failed")
            self.status.alert_type = "danger"
            self.report(f"Discovery failed: {error}")
        finally:
            self.busy(False)


def create_app(path=DEFAULT_DB, *, feedback=False):
    return Workspace(path).view(feedback=feedback)
