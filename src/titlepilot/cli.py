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
        s.add_argument("--no-notify", action="store_true", help="don't send the mapping email or Teams/Slack message")

    s = sub.add_parser("test-email", help="send a sample mapping email to check the .env email settings")
    s.add_argument("to", nargs="?", help="address to send to (default: TITLEPILOT_MAPPING_EMAIL)")

    s = sub.add_parser("score", help="score the latest run of each tract against an answer key folder")
    s.add_argument("answer_dir")

    s = sub.add_parser("index", help="upload a tract's files to Supabase Storage and index them (with the latest "
                                     "run's readings) in the Supabase database")
    s.add_argument("folder", nargs="+", help="one or more tract folders")
    s.add_argument("--run", type=int, help="index this run number instead of the latest")
    s.add_argument("--out", default="output", help="where the run reports are (default: output)")
    s.add_argument("--files-only", action="store_true", help="upload and index the files without any readings")

    s = sub.add_parser("search", help="call up indexed tracts and documents from Supabase")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--tract", help="every document on a tract")
    g.add_argument("--owner", help="documents naming this owner or party (fuzzy)")
    g.add_argument("--text", help="any words: types, references, names, legal descriptions, fields")
    g.add_argument("--ref", help="a recording reference on any tract, e.g. '1279/279' or 'Vol 1279 Pg 279'")
    s.add_argument("--in-tract", help="with --text: only this tract")
    s.add_argument("--links", action="store_true", help="add a link to open each file (valid one hour)")

    a = p.parse_args(argv)

    if a.cmd in ("index", "search"):
        from .supa import Supabase, SupabaseError
        try:
            sb = Supabase()
            if a.cmd == "index":
                from .index import index_tract
                db = None if a.files_only else DB(a.db)
                rules = load_config(a.config).rules if not a.files_only else []
                for folder in a.folder:
                    print(f"Indexing {folder} ...")
                    r = index_tract(folder, sb, db, run_id=a.run, out_dir=a.out, rules=rules)
                    print(f"  {r['tract']}: {r['files']} files ({r['uploaded']} uploaded, {r['unchanged']} unchanged), "
                          f"{r['with_readings']} with readings, {r['parties']} parties"
                          + (f", run {r['run']} with {r['flags']} flags" if r["run"] else ", no run yet")
                          + (f", reports: {', '.join(r['reports'])}" if r["reports"] else ""))
            else:
                _search(sb, a)
        except SupabaseError as e:
            print(f"Supabase: {e}")
            raise SystemExit(1)
        return

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

    if a.cmd == "test-email":
        from .mapping import email
        item = {"instrument_id": "TEST-0001", "reasons": ["Test message: no action needed"], "acres": 1.0,
                "grantors": ["Grantor"], "grantees": ["Grantee"], "legal": "Sample legal description",
                "file_name": None, "filing_date": None}
        try:
            result = email("TEST-TRACT", 0, [item], None, to=a.to)
        except Exception as e:
            print(f"Email FAILED: {e}")
            raise SystemExit(1)
        print(f"Email {result}" if result else "No address: set TITLEPILOT_MAPPING_EMAIL in .env or give one: "
                                               "titlepilot test-email you@segland.com")
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
            res = run_tract(folder, cfg, db, a.out, mock=a.mock, model=a.model, force=a.force,
                            notify_team=not a.no_notify)
            if res:
                owner = ", ".join(res["chain"]["current_owner"]) or "NOT FOUND"
                print(f"  run {res['run_id']}: {res['instruments']} instruments, current owner {owner}, "
                      f"{len(res['flags'])} flags")
                for f in res["flags"]:
                    print(f"    [{f.rule_id} {f.severity}] {f.instrument_id or '-'}: {f.message}")
                if res["skipped"]:
                    print(f"  rules with no check yet: {', '.join(res['skipped'])}")
                if res["mapping"]:
                    from .mapping import describe
                    bar = "  " + "*" * 70
                    print(bar)
                    print(f"  MAPPING NEEDED: {len(res['mapping'])} item(s) for {res['tract_id']}")
                    for m in res["mapping"]:
                        print(f"    - {describe(m)}")
                    print(f"  Added to {res['mapping_queue']}")
                    for channel, result in res["notified"]:
                        print(f"  {channel} {result}")
                    if not res["notified"]:
                        print("  (no message sent: set TITLEPILOT_MAPPING_EMAIL or TITLEPILOT_MAPPING_WEBHOOK in .env)")
                    print(bar)
                print(f"  title report:    {res['report']}")
                print(f"  review workbook: {res['review']}")
        return

    if a.cmd == "score":
        from .score import score
        print(json.dumps(score(db, a.answer_dir), indent=2))


if __name__ == "__main__":
    main()


def _search(sb, a):
    if a.tract:
        rows = sb.rpc("tract_documents", tract=a.tract)
        cols = ["filing_date", "instrument_id", "instrument_type", "kind", "file_name"]
    elif a.owner:
        rows = sb.rpc("search_owner", q=a.owner)
        cols = ["tract_id", "name", "role", "similarity", "filing_date", "instrument_id", "instrument_type"]
    elif a.ref:
        rows = sb.rpc("find_instrument", ref=a.ref)
        cols = ["tract_id", "instrument_id", "instrument_type", "filing_date"]
    else:
        rows = sb.rpc("search_documents", q=a.text, tract=a.in_tract)
        cols = ["tract_id", "filing_date", "instrument_id", "instrument_type", "file_name"]
    if not rows:
        print("Nothing found.")
        return
    def cell(r, c):
        v = r.get(c)
        return f"{v:.2f}" if isinstance(v, float) else ("" if v is None else str(v))
    widths = {c: min(40, max(len(c), *(len(cell(r, c)) for r in rows))) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows:
        print("  ".join(cell(r, c)[:40].ljust(widths[c]) for c in cols))
        if a.links and r.get("storage_path"):
            print("    " + sb.signed_url(r["storage_path"]))
    print(f"{len(rows)} found")
