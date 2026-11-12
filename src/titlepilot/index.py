"""Index a tract in Supabase: upload its files to Storage and record them, with what the program read from
each one, in the database (see supabase/schema.sql).

  Storage   tracts/<tract>/originals/<path as uploaded>      every file in the tract folder, unchanged
            tracts/<tract>/reports/run-<n>/<report>.xlsx     the title report and review workbook of the run
  Database  tracts, documents, parties, runs, flags

A tract can be indexed before it is run (files only, kind 'not read yet') and again after each run (the
readings fill in). Files whose fingerprint hasn't changed are not uploaded again. Safe to repeat.
"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .intake import is_runsheet, load_package, rel_name

CONTENT_TYPES = {".pdf": "application/pdf", ".tif": "image/tiff", ".tiff": "image/tiff", ".png": "image/png",
                 ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".csv": "text/csv", ".txt": "text/plain",
                 ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 ".xls": "application/vnd.ms-excel",
                 ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
SKIP = re.compile(r"(^|/)(\.|~\$)|\.mock\.json$|(^|/)Thumbs\.db$|\.DS_Store$", re.I)
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def package_files(folder: Path) -> list[Path]:
    return sorted(p for p in Path(folder).rglob("*")
                  if p.is_file() and not SKIP.search(p.relative_to(folder).as_posix()))


def run_uuid(tract_id, local_run_id, started_at) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"titlepilot:{tract_id}:{local_run_id}:{started_at}"))


def _date(v):
    return v if isinstance(v, str) and ISO.match(v) and not v.endswith("-00") and "-00-" not in v else None


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def load_run(db, tract_id, run_id=None):
    """The latest (or given) run of the tract from runs.sqlite: run row, chain, records by file, flags."""
    run_id = run_id or db.latest_run_id(tract_id)
    if not run_id:
        return None
    cols = [c[1] for c in db.conn.execute("PRAGMA table_info(runs)")]
    row = dict(zip(cols, db.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()))
    records = {fn: json.loads(raw) for fn, raw in db.conn.execute(
        "SELECT file_name, raw_json FROM run_instruments WHERE run_id = ? ORDER BY id", (run_id,))}
    flags = db.conn.execute("SELECT rule_id, severity, instrument_id, message FROM flags WHERE run_id = ?",
                            (run_id,)).fetchall()
    runsheets = [k.split(":", 1)[1] for (k,) in db.conn.execute(
        "SELECT kind FROM run_outputs WHERE run_id = ? AND kind LIKE 'runsheet:%'", (run_id,))]
    return {"id": run_id, "row": row, "chain": db.output(run_id, "chain") or {}, "records": records,
            "flags": flags, "runsheets": set(runsheets)}


def _parties(record, role):
    out = []
    for p in ((record or {}).get(role + "s") or {}).get("value") or []:
        if isinstance(p, dict) and (p.get("name") or "").strip():
            out.append({"role": role, "name": p["name"].strip(), "capacity": p.get("capacity") or None,
                        "marital_status": p.get("marital_status") or None, "share": p.get("share") or None})
    return out


def document_row(tract_id, key, path, rel, digest, reading, record, run_uuid_, kind_default):
    r = reading or {}
    names = lambda side: r.get(side) or [p["name"] for p in _parties(record, side[:-1])]
    return {
        "tract_id": tract_id, "storage_path": key, "file_name": path.name,
        "folder": str(Path(rel).parent).replace("\\", "/") if "/" in rel.replace("\\", "/") else None,
        "sha256": digest, "bytes": path.stat().st_size,
        "content_type": CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        "kind": r.get("kind") or ("recorded instrument" if r.get("recorded") else None) or kind_default,
        "instrument_id": r.get("instrument_id"), "ref_keys": sorted(r.get("ref_keys") or []) or None,
        "instrument_type": r.get("type"), "category": r.get("category"),
        "execution_date": _date(r.get("execution_date")), "filing_date": _date(r.get("filing_date")),
        "acreage": _num(r.get("acreage")), "legal": r.get("legal"),
        "grantors": names("grantors") or None, "grantees": names("grantees") or None,
        "fields": r.get("values") or None, "run_id": run_uuid_ if reading else None,
    }


def index_tract(folder, sb, db=None, run_id=None, out_dir="output", rules=None, log=print) -> dict:
    """sb: a supa.Supabase client. db: the runs.sqlite DB (optional: without it only files are indexed)."""
    from .supa import object_key
    folder = Path(folder)
    intake = load_package(folder)
    tract = intake.tract_id
    sb.check_bucket()
    run = load_run(db, tract, run_id) if db else None
    chain = (run or {}).get("chain") or {}
    readings = {}
    for i in list(chain.get("instruments", [])) + list(chain.get("supporting", [])):
        readings.setdefault(i.get("file_name"), i)
    actions = {r["id"]: r.get("reviewer_action") for r in (rules or [])}

    # tract and run rows first: documents and flags point at them
    ra, tax = chain.get("remaining_acres") or {}, chain.get("tax") or {}
    tract_row = {"tract_id": tract, "project": getattr(intake, "project", None), "county": getattr(intake, "county", None),
                 "acreage": _num(getattr(intake, "subject_acres", None)),
                 "current_owner": chain.get("current_owner") or None, "vesting_instrument": chain.get("vesting_instrument"),
                 "remaining_acres": _num(ra.get("remaining")), "cad_property_id": tax.get("cad_property_id"),
                 "notice_address": tax.get("notice_address"), "status": "run" if run else "indexed",
                 "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    sb.upsert("tracts", [{k: v for k, v in tract_row.items() if v is not None or k in ("status",)}], "tract_id")
    rid = None
    if run:
        row = run["row"]
        rid = run_uuid(tract, run["id"], row.get("started_at"))
        sb.upsert("runs", [{"run_id": rid, "tract_id": tract, "local_run_id": run["id"], "started_at": row.get("started_at"),
                            "program_version": __version__, "model": row.get("model"), "flag_count": len(run["flags"])}],
                  "run_id")

    existing = {d["storage_path"]: d["sha256"] for d in sb.select("documents", tract_id=tract, select="storage_path,sha256")}
    rows, parties_for, uploaded, skipped = [], {}, 0, 0
    files = package_files(folder)
    for n, path in enumerate(files, 1):
        rel = rel_name(folder, path)
        key = object_key("tracts", tract, "originals", path.relative_to(folder).as_posix())
        digest = sha256(path)
        if existing.get(key) == digest:
            skipped += 1
        else:
            sb.upload(key, path.read_bytes(), CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"))
            uploaded += 1
        reading = readings.get(rel) or readings.get(path.name)
        record = (run or {}).get("records", {}).get(rel) or (run or {}).get("records", {}).get(path.name)
        if run and (rel in run["runsheets"] or path.name in run["runsheets"]) or is_runsheet(path):
            default = "runsheet"
        elif path.name.lower() == "tracts.csv":
            default = "tract list"
        elif "intake" in path.name.lower() and path.suffix.lower() in (".xlsx", ".xls"):
            default = "intake form"
        else:
            default = "not read yet" if not run else "not read (not a document the run used)"
        rows.append(document_row(tract, key, path, rel, digest, reading, record, rid, default))
        parties_for[key] = _parties(record, "grantor") + _parties(record, "grantee")
        if n % 25 == 0:
            log(f"  {n}/{len(files)} files")

    # reports of the run
    report_paths = {}
    if run:
        work = Path(out_dir) / tract / f"run_{run['id']}"
        for name, kind in ((f"{tract}_title_report.xlsx", "title report"), (f"{tract}_review.xlsx", "review workbook")):
            p = work / name
            if p.exists():
                key = object_key("tracts", tract, "reports", f"run-{run['id']}", name)
                digest = sha256(p)
                if existing.get(key) == digest:
                    skipped += 1
                else:
                    sb.upload(key, p.read_bytes(), CONTENT_TYPES[".xlsx"])
                    uploaded += 1
                report_paths[kind] = key
                rows.append(document_row(tract, key, p, name, digest, None, None, None, kind) | {"run_id": rid})
                parties_for[key] = []

    saved = sb.upsert("documents", rows, "storage_path")
    ids = {d["storage_path"]: d["id"] for d in saved}
    sb.delete("parties", "document_id", ids.values())
    sb.insert("parties", [dict(p, document_id=ids[k], tract_id=tract) for k, ps in parties_for.items() if k in ids for p in ps])
    if run:
        sb.upsert("runs", [{"run_id": rid, "tract_id": tract, "report_path": report_paths.get("title report"),
                            "review_path": report_paths.get("review workbook")}], "run_id")
        sb.delete("flags", "run_id", [rid])
        sb.insert("flags", [{"run_id": rid, "tract_id": tract, "rule_id": r, "severity": s, "instrument_id": i,
                             "message": m, "what_to_do": actions.get(r)} for r, s, i, m in run["flags"]])
        sb.upsert("tracts", [{"tract_id": tract, "latest_run_id": rid}], "tract_id")
    read = sum(1 for r in rows if r.get("fields"))
    return {"tract": tract, "files": len(files), "uploaded": uploaded, "unchanged": skipped, "with_readings": read,
            "parties": sum(len(v) for v in parties_for.values()), "run": run["id"] if run else None,
            "flags": len(run["flags"]) if run else 0, "reports": list(report_paths)}
