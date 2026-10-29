"""Score the latest run of each tract against the answer key.

answer_key/ground_truth.csv  tract_id, current_owner   (several owners: separate with ';')
answer_key/encumbrances.csv  tract_id, instrument_id, status (open|released)
answer_key/known_issues.csv  tract_id, rule_id          (optional)
"""
import csv
from pathlib import Path

from .fuzzy import token_sort_ratio

from .validate import norm_ref


def _read(path):
    p = Path(path)
    return list(csv.DictReader(p.open())) if p.exists() else []


def _owners_match(expected: str, got: list[str]) -> bool:
    exp = sorted(x.strip().lower() for x in expected.split(";") if x.strip())
    got = sorted(g.lower() for g in got)
    return len(exp) == len(got) and all(token_sort_ratio(a, b) >= 90 for a, b in zip(exp, got))


def score(db, answer_dir) -> dict:
    truth = _read(Path(answer_dir) / "ground_truth.csv")
    enc_key = _read(Path(answer_dir) / "encumbrances.csv")
    issues = _read(Path(answer_dir) / "known_issues.csv")

    owners_ok = tracts = enc_found = enc_total = status_ok = issues_found = 0
    missing_runs = []
    for t in truth:
        run_id = db.latest_run_id(t["tract_id"])
        if run_id is None:
            missing_runs.append(t["tract_id"])
            continue
        chain = db.output(run_id, "chain")
        tracts += 1
        owners_ok += _owners_match(t["current_owner"], chain["current_owner"])

        got = {norm_ref(e["instrument_id"]): e["status"] for e in chain["encumbrances"]}
        for e in (x for x in enc_key if x["tract_id"] == t["tract_id"]):
            enc_total += 1
            key = norm_ref(e["instrument_id"])
            if key in got:
                enc_found += 1
                status_ok += got[key] == e["status"].strip().lower()

        flagged = {r[0] for r in db.conn.execute("SELECT DISTINCT rule_id FROM flags WHERE run_id = ?", (run_id,))}
        issues_found += sum(i["rule_id"] in flagged for i in issues if i["tract_id"] == t["tract_id"])

    n_issues = sum(1 for i in issues if i["tract_id"] not in missing_runs)
    pct = lambda a, b: round(a / b, 3) if b else None
    return {
        "tracts_scored": tracts,
        "tracts_without_runs": missing_runs,
        "current_owner_correct": pct(owners_ok, tracts),
        "encumbrances_found": pct(enc_found, enc_total),
        "encumbrance_status_correct": pct(status_ok, enc_found),
        "known_issues_flagged": pct(issues_found, n_issues),
    }
