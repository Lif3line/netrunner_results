import argparse
import logging
from pathlib import Path

from analyser import APP_TITLE
from analyser.store import DEFAULT_DB, initialise

FEEDBACK_ASSETS = Path(__file__).resolve().parent.parent / ".opencode" / "feedback" / "dist"


def feedback_assets(enabled):
    """Optional tooling must not prevent the analyser from starting"""
    if not enabled:
        return None
    try:
        for filename in ("loader.js", "overlay.js"):
            with (FEEDBACK_ASSETS / filename).open("rb") as bundle:
                if not bundle.read(1):
                    raise ValueError(f"{filename} is empty")
    except (OSError, ValueError) as error:
        logging.getLogger(__name__).warning(
            "Feedback unavailable (%s); starting without it. "
            "Build with: npm --prefix .opencode/feedback ci && "
            "npm --prefix .opencode/feedback run build",
            error,
        )
        return None
    return FEEDBACK_ASSETS


def main():
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="Open the browser workspace (default)")
    serve.add_argument("--port", type=int, default=5006)
    serve.add_argument("--no-browser", action="store_true")
    serve.add_argument(
        "--feedback", action="store_true", help="Enable the local Agentation toolbar"
    )
    importer = commands.add_parser("import", help="Import or refresh tournament URLs")
    importer.add_argument("urls", nargs="+")
    importer.add_argument("--no-decks", action="store_true")
    importer.add_argument("--refresh", action="store_true", help="Explicitly refetch source data")
    importer.add_argument("--format", help="Override source format when needed")
    args = parser.parse_args()
    initialise(args.db)
    if args.command == "import":
        from analyser.importer import import_tournament

        failed = False
        for url in args.urls:
            try:
                event = import_tournament(
                    url,
                    args.db,
                    metadata={"format": args.format},
                    include_decks=not args.no_decks,
                    refresh=args.refresh,
                    progress=print,
                )
                print(f"Ready: {event['name']} · {event['size']} entries · {event['deck_status']}")
            except Exception as error:
                failed = True
                print(f"Failed {url}: {error}")
        raise SystemExit(1 if failed else 0)
    else:
        import panel as pn

        from analyser.app import create_app

        port = getattr(args, "port", 5006)
        assets = feedback_assets(getattr(args, "feedback", False))
        pn.serve(
            lambda: create_app(args.db, feedback=assets is not None),
            address="127.0.0.1",
            port=port,
            websocket_origin=[f"127.0.0.1:{port}", f"localhost:{port}"],
            show=not getattr(args, "no_browser", False),
            title=APP_TITLE,
            static_dirs={"feedback-assets": str(assets)} if assets else {},
        )


if __name__ == "__main__":
    main()
