"""Run one tract folder end to end."""
import json
import time
from collections import Counter
from pathlib import Path

from .chain import build_chain
from .extract import DEFAULT_MODEL, Extractor
from .export import export_review
from .report import write_report
from .intake import acres_in, check_package, find_tract_list, load_package, load_tract_list
from .mapping import mapping_items, send_notifications, update_queue
from .pages import to_pages
from .rules import Ctx, run_rules
from .validate import norm_date, norm_ref, normalize, schema_errors


def run_tract(folder, cfg, db, out_dir="output", mock=False, model=DEFAULT_MODEL, force=False, log=print,
              notify_team=True):
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

    runsheet_acres = None
    for n, rs_path in enumerate(intake.runsheet_paths or ([intake.runsheet_path] if intake.runsheet_path else [])):
        try:
            rs = extractor.read_runsheet(rs_path, work / "pages" / f"_runsheet{n}")
        except Exception as e:
            problems.append(f"Runsheet {rs_path.name} could not be read: {e}")
            db.event(run_id, "runsheet_failed", f"{rs_path.name}: {e}")
            log(f"  Runsheet {rs_path.name} could not be read: {e}")
            continue
        for note in rs.get("_partial", []):
            problems.append(f"Runsheet {rs_path.name}, {note}")
            log(f"  Runsheet {rs_path.name}, {note}")
        kind = rs.get("runsheet_kind") or "tract"
        names = [o for o in rs.get("owners_searched", []) if o]
        for e in rs.get("entries", []):
            intake.runsheet_entries.append({**e, "_kind": kind, "_names": ", ".join(names), "_file": rs_path.name})
        intake.owners += [o for o in names if o not in intake.owners]
        if kind == "tract":
            runsheet_acres = runsheet_acres or acres_in(rs.get("subject_acreage")) or acres_in(rs.get("subject_description"))
        db.add_output(run_id, f"runsheet:{rs_path.name}", rs)
        log(f"  Runsheet {rs_path.name} ({kind} runsheet): {len(rs.get('entries', []))} instruments listed"
            + (f", names searched: {', '.join(names)}" if names else ""))

    # The tract's acreage lets out-sales be told apart from conveyances of the whole tract.
    tract_list = find_tract_list(folder)
    listed = load_tract_list(tract_list).get(intake.tract_id, {}) if tract_list else {}
    if tract_list and not listed:
        log(f"  {tract_list} has no row for tract ID '{intake.tract_id}' (check the tract_id column)")
    intake.county = intake.county or listed.get("county") or None
    intake.legal_description = intake.legal_description or listed.get("legal_description") or None
    intake.aoi = listed.get("aoi") or None
    intake.agent = intake.agent or listed.get("researcher") or None
    researched = None
    if listed.get("researched_from") and listed.get("researched_to"):
        researched = (norm_date(listed["researched_from"]), norm_date(listed["researched_to"]))
    acres_sources = [(str(tract_list), acres_in(f"{listed.get('acreage')} acres") if listed.get("acreage") else None),
                     ("intake form", acres_in(intake.legal_description)), ("tract runsheet", runsheet_acres)]
    acres_source = next(((src, a) for src, a in acres_sources if a), (None, None))
    intake.subject_acres = acres_source[1]
    if intake.subject_acres:
        log(f"  Tract acreage: {intake.subject_acres:g} ac (from {acres_source[0]})")
    else:
        log("  Tract acreage: unknown. No tracts.csv found in the tract folder, the folder holding the tracts, "
            "or the folder you're running from, so out-sales can't be told apart from whole-tract sales")

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
        norm["pages"] = len(pages)
        db.add_instrument(run_id, norm, record, errors)
        if errors:
            db.event(run_id, "schema_errors", f"{doc.file_name}: {len(errors)}")
        instruments.append(norm)
        notes = []
        if record.get("_meta", {}).get("repaired"):
            notes.append("needed a correction round")
        if errors:
            notes.append(f"{len(errors)} schema problems")
        what = norm['type'] or 'unknown type' if norm["recorded"] else f"{norm['kind']} (not a recorded instrument)"
        log(f"  [{n}/{len(docs)}] {doc.file_name}: {what}, "
            f"{len(pages)} page(s), {time.monotonic() - started:.0f}s" + (f" ({'; '.join(notes)})" if notes else ""))

    # Dates the AI couldn't read can come from the agent's runsheet.
    by_ref = {norm_ref(e.get("recording_ref")): e for e in intake.runsheet_entries if e.get("recording_ref")}
    for i in instruments:
        if not i["filing_date"]:
            e = next((by_ref[k] for k in i["ref_keys"] if k in by_ref), None)
            if e and norm_date(e.get("filing_date")):
                i["filing_date"] = norm_date(e.get("filing_date"))
                i["date_from_runsheet"] = True
    if not intake.county:
        counties = [c for c in (i.get("county") for i in instruments if i.get("recorded")) if c]
        intake.county = Counter(c.title() for c in counties).most_common(1)[0][0] if counties else None

    chain = build_chain(instruments, intake.subject_acres)
    db.add_output(run_id, "chain", chain)
    recorded = [i for i in instruments if i.get("recorded", True)]
    flags, skipped = run_rules(cfg.rules, Ctx(recorded, chain, intake))
    db.add_flags(run_id, flags)
    db.event(run_id, "rules_run", json.dumps({"flags": len(flags), "skipped": skipped}))

    review = export_review(work / f"{intake.tract_id}_review.xlsx", intake, run_id, chain, flags, skipped, problems)
    report = write_report(work / f"{intake.tract_id}_title_report.xlsx", intake, chain, flags, skipped, problems,
                          run_id, researched)
    db.event(run_id, "ready_for_review", json.dumps({"review": str(review), "report": str(report)}))

    to_map = mapping_items(chain, intake)
    queue = update_queue(out_dir, intake.tract_id, run_id, to_map, folder)
    notified = []
    if to_map:
        db.event(run_id, "mapping_requested", json.dumps([{"instrument": m["instrument_id"], "reasons": m["reasons"]}
                                                          for m in to_map]))
        if notify_team:
            notified = send_notifications(intake.tract_id, run_id, to_map, report)
            for channel, result in notified:
                db.event(run_id, "mapping_notify_failed" if result.startswith("FAILED") else "mapping_notified",
                         f"{channel}: {result}")
    return {"run_id": run_id, "tract_id": intake.tract_id, "instruments": len(instruments),
            "chain": chain, "flags": flags, "skipped": skipped, "review": review, "report": report,
            "mapping": to_map, "mapping_queue": queue, "notified": notified}
