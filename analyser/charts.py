"""Bokeh charts adapted from the overview's identity and matchup drill-downs."""

from bokeh.models import (
    ColumnDataSource,
    FixedTicker,
    HoverTool,
    NumeralTickFormatter,
    Span,
    TeeHead,
    Whisker,
)
from bokeh.plotting import figure

FACTION_COLOURS = {
    "Anarch": "#ee7867",
    "Criminal": "#63a6e8",
    "Shaper": "#61b99c",
    "HB": "#a48bdd",
    "Jinteki": "#ee7867",
    "Weyland": "#61b99c",
    "NBN": "#dcb75a",
    "Mini-faction": "#95a3b6",
}


def winrate_chart(stats, title, on_select=None, *, show_intervals=True):
    stats = stats.sort_values(["winrate", "games", "identity"]).copy()
    stats["colour"] = stats.faction.map(FACTION_COLOURS).fillna("#95a3b6")
    source = ColumnDataSource(stats)
    plot = figure(
        title=title,
        height=max(320, 32 * len(stats) + 75),
        min_width=350,
        sizing_mode="stretch_width",
        y_range=stats.identity.tolist(),
        x_range=(-0.03, 1.03),
        tools="tap,reset,save" if on_select else "reset,save",
        toolbar_location="above",
        background_fill_color="#ffffff",
        border_fill_color="#ffffff",
    )
    bars = plot.hbar(
        y="identity",
        right="winrate",
        height=0.65,
        color="colour",
        alpha=0.95,
        source=source,
        selection_color="#24364b",
        nonselection_alpha=0.35,
    )
    plot.add_layout(
        Whisker(
            source=source,
            base="identity",
            lower="ci_lower",
            upper="ci_upper",
            dimension="width",
            line_color="#24364b",
            line_width=1.2,
            line_alpha=0.85,
            visible=show_intervals,
            lower_head=TeeHead(size=7, line_color="#24364b"),
            upper_head=TeeHead(size=7, line_color="#24364b"),
            name="winrate_interval",
            level="overlay",
        )
    )
    # A point remains hoverable even for identities with no wins
    points = plot.scatter(
        x="winrate",
        y="identity",
        size=5,
        source=source,
        color="#24364b",
        visible=show_intervals,
        nonselection_alpha=0.4,
        name="winrate_point",
    )
    plot.add_tools(
        HoverTool(
            renderers=[bars, points],
            tooltips=[
                ("Identity", "@identity"),
                ("Win rate", "@winrate{0.0%}"),
                ("95% Wilson interval", "@ci_lower{0.0%} – @ci_upper{0.0%}"),
                ("Wins / games", "@wins / @games"),
                ("Draws", "@draws"),
            ],
        )
    )
    plot.add_layout(
        Span(location=0.5, dimension="height", line_color="#cad3df", line_dash="dashed")
    )
    plot.xaxis.formatter = NumeralTickFormatter(format="0%")
    plot.xaxis.ticker = FixedTicker(ticks=[0, 0.2, 0.4, 0.6, 0.8, 1])
    plot.xaxis.axis_label = "Wins / games · draws count as games"
    plot.axis.axis_line_color = None
    plot.axis.major_tick_line_color = None
    plot.axis.minor_tick_line_color = None
    plot.axis.major_label_text_color = "#516174"
    plot.yaxis.major_label_overrides = {
        identity: identity.split(":", 1)[0][:30] for identity in stats.identity
    }
    plot.axis.axis_label_text_color = "#516174"
    plot.ygrid.grid_line_color = None
    plot.xgrid.grid_line_color = "#eef1f5"
    plot.outline_line_color = None
    plot.title.text_color = "#24364b"
    plot.title.text_font_size = "14px"
    if on_select:

        def selected(attr, old, new):
            if new:
                on_select(source.data["identity"][new[0]])

        source.selected.on_change("indices", selected)
    return plot
