"""Command line: titlepilot <command> ..."""
import argparse
import json
import os
from pathlib import Path

from . import __version__
from .config import load_config
from .db import DB
from .extract import DEFAULT_MODEL


def _load_dotenv(path=".env"):
    """Minimal .env loader so the API key can live in a file kept out of git."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main(argv=None):
    _load_dotenv()
    p = argparse.ArgumentParser(prog="titlepilot", description="Texas surface title extraction pilot")
    p.add_argument("--config", default="config", help="config folder (default: config)")
    p.add_argument("--db", default="runs.sqlite", help="run history database (default: runs.sqlite)")
    p.add_argument("--version", action="version", version=f"titlepilot {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("import-config", help="build fields.yaml and rules.yaml from the Fields and Rules workbook")
    s.add_argument("workbook")

    s = sub.add_parser("sample-tract", help="create a synthetic tract and answer key for testing")
    s.add_argument("dest", nargs="?", default="sample_data")
    s.add_argument("--mode", choices=["intake", "runsheet", "folder"], default="intake",
                   help="what the agent delivers: intake workbook, their own runsheet, or documents only")

    for name, helptext in [("run", "process one tract folder"), ("run-all", "process every tract folder in a parent folder")]:
        s = sub.add_parser(name, help=helptext)
        s.add_argument("folder")
        s.add_argument("--out", default="output")
        s.add_argument("--mock", action="store_true", help="use <file>.mock.json instead of calling the API")
        s.add_argument("--model", default=DEFAULT_MODEL)
        s.add_argument("--force", action="store_true", help="process even if the package check finds problems")

    s = sub.add_parser("score", help="score the latest run of each tract against an answer key folder")
    s.add_argument("answer_dir")

    a = p.parse_args(argv)

    if a.cmd == "import-config":
        from .import_config import import_workbook
        counts = import_workbook(a.workbook, a.config)
        print(f"Wrote {counts['fields']} fields and {counts['rules']} rules to {a.config}/")
        return

    if a.cmd == "sample-tract":
        from .sample import make_sample_tract
        dest = make_sample_tract(a.dest, a.mode)
        print(f"Sample tract in {dest}/. Try: titlepilot run {dest}/TX-SAMPLE-001 --mock")
        return

    db = DB(a.db)

    if a.cmd in ("run", "run-all"):
        from .pipeline import run_tract
        cfg = load_config(a.config)
        print(f"titlepilot {__version__}")
        folders = [Path(a.folder)] if a.cmd == "run" else sorted(
            d for d in Path(a.folder).iterdir() if d.is_dir() and d.name != "answer_key")
        for folder in folders:
            print(f"{folder.name}:")
            res = run_tract(folder, cfg, db, a.out, mock=a.mock, model=a.model, force=a.force)
            if res:
                owner = ", ".join(res["chain"]["current_owner"]) or "NOT FOUND"
                print(f"  run {res['run_id']}: {res['instruments']} instruments, current owner {owner}, "
                      f"{len(res['flags'])} flags")
                for f in res["flags"]:
                    print(f"    [{f.rule_id} {f.severity}] {f.instrument_id or '-'}: {f.message}")
                if res["skipped"]:
                    print(f"  rules with no check yet: {', '.join(res['skipped'])}")
                print(f"  review workbook: {res['review']}")
        return

    if a.cmd == "score":
        from .score import score
        print(json.dumps(score(db, a.answer_dir), indent=2))


if __name__ == "__main__":
    main()
