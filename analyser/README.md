# Tournament Analyser

One stop shop for exploring tournament stuff.

## Getting started

Python 3.10+; From root:

```sh
python -m venv analyser/.venv
source analyser/.venv/bin/activate
python -m pip install -r analyser/requirements.txt
python -m analyser
```

CLI imports:

```sh
python -m analyser import https://tournaments.nullsignal.games/tournaments/4990
python -m analyser import https://tournaments.nullsignal.games/tournaments/4990 --refresh
python -m analyser --db analyser/data/other.sqlite3 serve --no-browser --port 5007
```

Imports support Cobra and Aesop's results, plus public Cobra decklists. Use `--no-decks` for results-only. Discovery uses the [Always Be Running API](https://alwaysberunning.net/apidoc).

## Checks

```sh
python -B -m unittest discover -s analyser/tests -v
ruff check analyser
ruff format --check analyser
```
