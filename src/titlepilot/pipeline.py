"""Run one tract folder end to end."""
import json
import time
from pathlib import Path

from .chain import build_chain
from .extract import DEFAULT_MODEL, Extractor
from .export import export_review
from .intake import check_package, load_package
from .pages import to_pages
from .rules import Ctx, run_rules
from .validate import normalize, schema_errors


def run_tract(folder, cfg, db, out_dir="output", mock=False, model=DEFAULT_MODEL, force=False, log=print):
    folder = Path(folder)
    intake = load_package(folder)
    source = intake.mode + (f" ({intake.runsheet_path.name})" if intake.runsheet_path else "")
    log(f"  Package: {source}")

    problems = check_package(folder, intake)
    for p in problems:
        log(f"  PACKAGE: {p}")
    if problems and not force:
        log("  Not processed. Send these back to the title agent, or re-run with --force.")
        return None

    run_id = db.start_run(intake, cfg, "mock" if mock else model)
    db.event(run_id, "package_source", source)
    db.event(run_id, "package_checked", json.dumps(problems))
    extractor = Extractor(cfg.schema, cfg.prompt, model=model, mock=mock)
    work = Path(out_dir) / intake.tract_id / f"run_{run_id}"

    if intake.runsheet_path:
        try:
            rs = extractor.read_runsheet(intake.runsheet_path, work / "pages" / "_runsheet")
            intake.runsheet_entries = rs.get("entries", [])
            if not intake.owners:
                intake.owners = [o for o in rs.get("owners_searched", []) if o]
            db.add_output(run_id, "runsheet", rs)
            log(f"  Runsheet: {len(intake.runsheet_entries)} instruments listed, "
                f"{len(intake.owners)} owners shown as name-searched")
        except Exception as e:
            problems.append(f"Runsheet {intake.runsheet_path.name} could not be read: {e}")
            db.event(run_id, "runsheet_failed", str(e))
            log(f"  Runsheet could not be read: {e}")

    instruments = []
    docs = [d for d in intake.documents if (folder / d.file_name).exists()]
    log(f"  Reading {len(docs)} documents (run {run_id})...")
    for n, doc in enumerate(docs, 1):
        path = folder / doc.file_name
        started = time.monotonic()
        try:
            pages = to_pages(path, work / "pages" / Path(doc.file_name).with_suffix("").as_posix().replace("/", "__"))
            record = extractor.extract(path, pages)
        except Exception as e:  # one bad document should not stop the tract
            db.event(run_id, "extraction_failed", f"{doc.file_name}: {e}")
            log(f"  [{n}/{len(docs)}] {doc.file_name}: FAILED: {e}")
            continue
        errors = schema_errors(record, cfg.schema)
        norm = normalize(record, doc)
        db.add_instrument(run_id, norm, record, errors)
        if errors:
            db.event(run_id, "schema_errors", f"{doc.file_name}: {len(errors)}")
        instruments.append(norm)
        notes = []
        if record.get("_meta", {}).get("repaired"):
            notes.append("needed a correction round")
        if errors:
            notes.append(f"{len(errors)} schema problems")
        log(f"  [{n}/{len(docs)}] {doc.file_name}: {norm['type'] or 'unknown type'}, "
            f"{len(pages)} page(s), {time.monotonic() - started:.0f}s" + (f" ({'; '.join(notes)})" if notes else ""))

    chain = build_chain(instruments)
    db.add_output(run_id, "chain", chain)
    flags, skipped = run_rules(cfg.rules, Ctx(instruments, chain, intake))
    db.add_flags(run_id, flags)
    db.event(run_id, "rules_run", json.dumps({"flags": len(flags), "skipped": skipped}))

    review = export_review(work / f"{intake.tract_id}_review.xlsx", intake, run_id, chain, flags, skipped, problems)
    db.event(run_id, "ready_for_review", str(review))
    return {"run_id": run_id, "tract_id": intake.tract_id, "instruments": len(instruments),
            "chain": chain, "flags": flags, "skipped": skipped, "review": review}
