"""Find tracts and out-sales that need to be mapped, and tell the mapping team.

An instrument goes on the mapping list when:
- it carves land out of a larger tract (an out-sale from the subject tract, or the deed
  that created the subject tract out of a parent tract), or
- its legal description is by metes and bounds (courses and distances, a point of
  beginning, field notes), so the tract can't be drawn from a survey and abstract alone, or
- the tract's own legal description excepts land ("LESS AND EXCEPT 1 acre described in ...").

Each run writes the items to the report (AI Mapping sheet), to a MP01 flag, and to
mapping_queue.csv in the output folder, which holds every tract's open mapping work.
If TITLEPILOT_MAPPING_WEBHOOK is set (in .env), a message is also posted to Teams or Slack.
"""
import csv
import json
import os
import re
import urllib.request
from datetime import date
from pathlib import Path

from .chain import PARTIAL_AREA, is_partial_area
from .validate import norm_ref

_STRONG = re.compile(r"metes\s*(?:and|&)\s*bounds|field\s*notes|point\s+of\s+beginning|\bP\.\s?O\.\s?B\b", re.I)
_COURSE = re.compile(
    r"\bthence\b"
    r"|\b(?:[NS]|north|south)\.?\s*\d{1,3}\s*(?:°|º|deg\.?|degrees)\s*(?:\d{1,2}\s*(?:'|’|min\.?|minutes)?)?"
    r"|\bbeginning\s+at\b"
    r"|\b\d+(?:\.\d+)?\s*(?:feet|ft\.?|varas|vrs\.?)\s+to\s+(?:a|an|the)\b", re.I)
_EXCEPT = re.compile(r"(?:less|save)\s*(?:and|&)\s*except[^;]*", re.I)
_ACRES = re.compile(r"(\d+(?:\.\d+)?)\s*(?:acres?|ac\.?)\b", re.I)
_DOCREF = re.compile(r"(?:document|doc|instrument|inst)\.?\s*(?:number|no\.?|#)?\s*(\d{4}-?\d{3,7})"
                     r"|vol(?:ume)?\.?\s*(\d+)\s*,?\s*(?:page|pg|p)\.?\s*(\d+)", re.I)

QUEUE_COLUMNS = ["tract_id", "run_id", "added", "instrument", "reason", "acres", "grantor", "grantee",
                 "legal_description", "file", "status", "mapped_by", "mapped_date", "note"]


def is_metes_and_bounds(text) -> bool:
    t = str(text or "")
    return bool(_STRONG.search(t)) or len(_COURSE.findall(t)) >= 2


def mapping_items(chain: dict, intake) -> list[dict]:
    subject = chain.get("subject_acres") or getattr(intake, "subject_acres", None)
    items, by_id = [], {}

    def add(inst, reason):
        key = inst["instrument_id"] if inst else reason
        if key not in by_id:
            by_id[key] = {"instrument_id": inst["instrument_id"] if inst else None, "reasons": [],
                          "acres": inst.get("acreage") if inst else None,
                          "grantors": inst.get("grantors", []) if inst else [],
                          "grantees": inst.get("grantees", []) if inst else [],
                          "legal": inst.get("legal") if inst else None,
                          "file_name": inst.get("file_name") if inst else None,
                          "filing_date": inst.get("filing_date") if inst else None}
            items.append(by_id[key])
        if reason not in by_id[key]["reasons"]:
            by_id[key]["reasons"].append(reason)

    conveyances = chain.get("conveyances", [])
    prev_acres = None
    for c in conveyances:
        a = c.get("acreage")
        if subject and is_partial_area(c, subject) and c["instrument_id"] != chain.get("vesting_instrument") \
                and _after_vesting(c, chain):
            add(c, f"Out-sale: {a:g} ac out of the {subject:g} ac tract")
        elif a and prev_acres and a < prev_acres * PARTIAL_AREA:
            add(c, f"Out-sale: {a:g} ac carved out of a {prev_acres:g} ac parent tract")
        if a:
            prev_acres = a

    for i in chain.get("instruments", []):
        if is_metes_and_bounds(i.get("legal")):
            add(i, "Metes and bounds description")

    # Land excepted in the tract's own legal description.
    by_ref = {k: i for i in chain.get("instruments", []) for k in (i.get("ref_keys") or ())}
    for clause in _EXCEPT.findall(getattr(intake, "legal_description", None) or ""):
        acres = _ACRES.search(clause)
        refs = [norm_ref(m.group(1) or f"Vol {m.group(2)} Page {m.group(3)}") for m in _DOCREF.finditer(clause)]
        inst = next((by_ref[r] for r in refs if r in by_ref), None)
        what = f"{acres.group(1)} ac" if acres else "land"
        if inst:
            add(inst, f"Excepted from the tract ({what}): map the exception")
        else:
            add(None, f"Excepted from the tract ({what}) but the instrument is not in the package: {clause.strip()[:160]}")
    return items


def _after_vesting(c, chain):
    order = [x["instrument_id"] for x in chain.get("conveyances", [])]
    v = chain.get("vesting_instrument")
    return v not in order or order.index(c["instrument_id"]) > order.index(v)


def describe(item) -> str:
    return f"{item['instrument_id'] or '(not in package)'}: {'; '.join(item['reasons'])}"


def update_queue(out_dir, tract_id, run_id, items, folder=None) -> Path:
    """Keep one open row per tract and instrument. Rows a mapper has marked (status other than
    'open', or mapped_by filled in) are kept as they are."""
    path = Path(out_dir) / "mapping_queue.csv"
    rows = []
    if path.exists():
        with path.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    done = {(r["tract_id"], r["instrument"]) for r in rows
            if r.get("tract_id") == tract_id and ((r.get("status") or "open").lower() != "open" or r.get("mapped_by"))}
    keep = [r for r in rows if r.get("tract_id") != tract_id or (r["tract_id"], r["instrument"]) in done]
    for it in items:
        inst = it["instrument_id"] or "(not in package)"
        if (tract_id, inst) in done:
            continue
        keep.append({"tract_id": tract_id, "run_id": run_id, "added": date.today().isoformat(), "instrument": inst,
                     "reason": "; ".join(it["reasons"]), "acres": f"{it['acres']:g}" if it["acres"] else "",
                     "grantor": ", ".join(it["grantors"]), "grantee": ", ".join(it["grantees"]),
                     "legal_description": it["legal"] or "",
                     "file": str(Path(folder) / it["file_name"]) if folder and it["file_name"] else (it["file_name"] or ""),
                     "status": "open", "mapped_by": "", "mapped_date": "", "note": ""})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=QUEUE_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(keep)
    return path


def notify(tract_id, run_id, items, report_path=None, url=None, fmt=None):
    """Post to a Teams Workflows or Slack incoming webhook. Returns None when no webhook is set."""
    url = url or os.environ.get("TITLEPILOT_MAPPING_WEBHOOK")
    if not url or not items:
        return None
    fmt = (fmt or os.environ.get("TITLEPILOT_WEBHOOK_FORMAT") or "teams").lower()
    title = f"Mapping needed: {tract_id} ({len(items)} item{'s' if len(items) != 1 else ''}, run {run_id})"
    lines = [describe(i) for i in items]
    if fmt == "slack":
        payload = {"text": "\n".join([f"*{title}*"] + [f"• {l}" for l in lines]
                                     + ([f"Report: {report_path}"] if report_path else []))}
    else:
        body = [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True}]
        body += [{"type": "TextBlock", "text": f"- {l}", "wrap": True} for l in lines]
        if report_path:
            body.append({"type": "TextBlock", "text": f"Report: {report_path}", "wrap": True, "isSubtle": True})
        payload = {"type": "message", "text": title, "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {"type": "AdaptiveCard", "version": "1.4",
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "body": body}}]}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status
