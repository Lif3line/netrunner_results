"""Two-list tournament selection with drag/drop"""

import panel as pn
import param


class TournamentPicker(pn.reactive.ReactiveHTML):
    cards = param.List(default=[])
    included = param.List(default=[])

    _template = """
    <div id="board" class="board">
      <section><h3>Included <span id="active_count"></span></h3>
        <div id="active_list" class="lane" aria-label="Included tournaments"></div>
      </section>
      <section><h3>Skipped <span id="skipped_count"></span></h3>
        <div id="skipped_list" class="lane skipped" aria-label="Skipped tournaments"></div>
      </section>
    </div>
    """

    _stylesheets = [
        """
        .board { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; color: #24364b; }
        h3 { margin: 0 0 10px; font-size: 15px; }
        h3 span { color: #617185; font-weight: normal; }
        .lane { min-height: 100px; max-height: 360px; overflow-y: auto; padding: 10px;
                border: 1px solid #cbdcd9; border-radius: 8px; background: #f1f8f6; }
        .skipped { background: #f4f5f7; border-color: #dce1e7; }
        .event { display: flex; gap: 12px; align-items: center; background: white;
                 border: 1px solid #dce1e7; border-radius: 6px; padding: 12px; margin-bottom: 8px; cursor: grab; }
        .details { flex: 1; min-width: 0; }
        .name { font-weight: 550; overflow-wrap: anywhere; }
        .meta { color: #617185; font-size: 12px; margin-top: 5px; }
        .empty { color: #617185; padding: 12px; font-size: 13px; }
        @media (max-width: 700px) { .board { grid-template-columns: 1fr; } }
    """
    ]

    _scripts = {
        "render": "self.draw()",
        "cards": "self.draw()",
        "included": "self.draw()",
        "draw": """
            const selected = new Set(data.included);
            function move(id, include) {
                if (!data.cards.some(card => card.id === id)) return;
                const next = new Set(data.included);
                if (include) next.add(id); else next.delete(id);
                data.included = [...next];
            }
            for (const [lane, include] of [[active_list, true], [skipped_list, false]]) {
                lane.replaceChildren();
                lane.ondragover = event => { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; };
                lane.ondrop = event => {
                    event.preventDefault();
                    move(event.dataTransfer.getData('text/plain'), include);
                };
                const cards = data.cards.filter(card => selected.has(card.id) === include);
                for (const card of cards) {
                    const item = document.createElement('div');
                    item.className = 'event';
                    item.draggable = true;
                    item.dataset.tournamentId = card.id;
                    item.ondragstart = event => {
                        event.dataTransfer.setData('text/plain', card.id);
                        event.dataTransfer.effectAllowed = 'move';
                    };
                    const details = document.createElement('div');
                    details.className = 'details';
                    const name = document.createElement('div');
                    name.className = 'name';
                    name.textContent = card.name;
                    const meta = document.createElement('div');
                    meta.className = 'meta';
                    meta.textContent = `${card.date} · ${card.size} players · Banlist ${card.banlist} · ${card.cache}`;
                    details.append(name, meta);
                    item.append(details);
                    lane.append(item);
                }
                if (!cards.length) {
                    const empty = document.createElement('div');
                    empty.className = 'empty';
                    empty.textContent = include ? 'Find tournaments or include an event from the right.' : 'Drop tournaments here to leave them out of the analysis.';
                    lane.append(empty);
                }
            }
            active_count.textContent = `(${data.cards.filter(card => selected.has(card.id)).length})`;
            skipped_count.textContent = `(${data.cards.filter(card => !selected.has(card.id)).length})`;
        """,
    }
