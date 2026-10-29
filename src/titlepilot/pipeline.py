"""Run one tract folder end to end."""
import json
from pathlib import Path

from .chain import build_chain
from .extract import DEFAULT_MODEL, Extractor
from .export import export_review
from .intake import check_package, find_intake_file, read_intake
from .pages import to_pages
from .rules import Ctx, run_rules
from .validate import normalize, schema_errors


def run_tract(folder, cfg, db, out_dir="output", mock=False, model=DEFAULT_MODEL, force=False, log=print):
    folder = Path(folder)
    intake_path = find_intake_file(folder)
    if not intake_path:
        raise SystemExit(f"{folder}: no intake workbook with 'Intake Form' and 'Document List' sheets")
    intake = read_intake(intake_path)

    problems = check_package(folder, intake)
    for p in problems:
        log(f"  PACKAGE: {p}")
    if problems and not force:
        log("  Not processed. Send these back to the title agent, or re-run with --force.")
        return None

    run_id = db.start_run(intake, cfg, "mock" if mock else model)
    db.event(run_id, "package_checked", json.dumps(problems))
    extractor = Extractor(cfg.schema, cfg.prompt, model=model, mock=mock)
    work = Path(out_dir) / intake.tract_id / f"run_{run_id}"

    instruments = []
    for doc in intake.documents:
        path = folder / doc.file_name
        if not path.exists():
            continue
        try:
            pages = to_pages(path, work / "pages" / path.stem)
            record = extractor.extract(path, pages)
        except Exception as e:  # one bad document should not stop the tract
            db.event(run_id, "extraction_failed", f"{doc.file_name}: {e}")
            log(f"  FAILED: {doc.file_name}: {e}")
            continue
        errors = schema_errors(record, cfg.schema)
        norm = normalize(record, doc)
        db.add_instrument(run_id, norm, record, errors)
        if errors:
            db.event(run_id, "schema_errors", f"{doc.file_name}: {len(errors)}")
        instruments.append(norm)

    chain = build_chain(instruments)
    db.add_output(run_id, "chain", chain)
    flags, skipped = run_rules(cfg.rules, Ctx(instruments, chain, intake))
    db.add_flags(run_id, flags)
    db.event(run_id, "rules_run", json.dumps({"flags": len(flags), "skipped": skipped}))

    review = export_review(work / f"{intake.tract_id}_review.xlsx", intake, run_id, chain, flags, skipped, problems)
    db.event(run_id, "ready_for_review", str(review))
    return {"run_id": run_id, "tract_id": intake.tract_id, "instruments": len(instruments),
            "chain": chain, "flags": flags, "skipped": skipped, "review": review}
