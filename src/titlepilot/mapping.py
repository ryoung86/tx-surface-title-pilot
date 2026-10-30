"""Find tracts and out-sales that need to be mapped, and tell the mapping team.

An instrument goes on the mapping list when:
- it carves land out of a larger tract (an out-sale from the subject tract, or the deed
  that created the subject tract out of a parent tract), or
- its legal description is by metes and bounds (courses and distances, a point of
  beginning, field notes), so the tract can't be drawn from a survey and abstract alone, or
- the tract's own legal description excepts land ("LESS AND EXCEPT 1 acre described in ...").

Each run writes the items to the report (AI Mapping sheet), to a MP01 flag, and to
mapping_queue.csv in the output folder, which holds every tract's open mapping work.
If TITLEPILOT_MAPPING_WEBHOOK is set (in .env), a message is also posted to Teams or Slack, and if
TITLEPILOT_MAPPING_EMAIL is set, an email is sent (through Outlook on this PC, or an SMTP server).
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

    # Metes and bounds on a deed that defines this tract or part of it. A deed of a much larger parent
    # tract (a whole ranch described by metes and bounds) isn't something to map for this tract, and
    # easements are covered by their own rules.
    for i in chain.get("instruments", []):
        if i.get("category") not in ("conveyance", "pore space") or not is_metes_and_bounds(i.get("legal")):
            continue
        a = i.get("acreage")
        if subject and a and a > subject * 2:
            continue
        add(i, "Metes and bounds description")

    # The tract is only described as a portion of a larger vesting tract: its own boundary has to be drawn.
    legal = getattr(intake, "legal_description", None) or ""
    vest = next((c for c in conveyances if c["instrument_id"] == chain.get("vesting_instrument")), None)
    if (subject and vest and vest.get("acreage") and vest["acreage"] > subject / PARTIAL_AREA
            and re.search(r"\b(?:a\s+)?(?:portion|part)\s+of\b|\bout\s+of\s+(?:the|that|a)\b", legal, re.I)
            and not is_metes_and_bounds(legal)):
        add(vest, f"Tract ({subject:g} ac) is described only as a portion of the {vest['acreage']:g} ac vesting tract, "
                  "with no metes and bounds of its own: map the tract boundary")

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


def _recipients(value) -> list[str]:
    return [a.strip() for a in re.split(r"[;,]", value or "") if a.strip()]


def _email_text(tract_id, run_id, items, report_path):
    subject = f"Mapping needed: {tract_id} ({len(items)} item{'s' if len(items) != 1 else ''})"
    lines = [f"Title run {run_id} for tract {tract_id} found land that needs to be mapped:", ""]
    for i in items:
        lines.append(f"- {describe(i)}")
        if i.get("acres"):
            lines.append(f"    Acres: {i['acres']:g}")
        if i.get("grantors") or i.get("grantees"):
            lines.append(f"    {', '.join(i.get('grantors') or []) or '?'} to {', '.join(i.get('grantees') or []) or '?'}")
        if i.get("legal"):
            lines.append(f"    Legal: {i['legal']}")
        if i.get("file_name"):
            lines.append(f"    File: {i['file_name']}")
    lines += ["", "The title report is attached; see its AI Mapping sheet. When mapped, set the row's status to "
              "'mapped' in mapping_queue.csv.", "", "Sent automatically by titlepilot. This is an AI draft for review."]
    return subject, "\n".join(lines)


def email(tract_id, run_id, items, report_path=None, to=None, method=None):
    """Email the mapping list. Returns None when no address is set.

    method 'outlook' sends from the Outlook account signed in on this PC (no password needed;
    Windows only, uses pywin32). method 'smtp' uses TITLEPILOT_SMTP_HOST/PORT/USER/PASSWORD/FROM.
    Default: smtp when TITLEPILOT_SMTP_HOST is set, otherwise outlook."""
    to = _recipients(to or os.environ.get("TITLEPILOT_MAPPING_EMAIL"))
    if not to or not items:
        return None
    env = os.environ.get
    method = (method or env("TITLEPILOT_EMAIL_METHOD") or ("smtp" if env("TITLEPILOT_SMTP_HOST") else "outlook")).lower()
    subject, body = _email_text(tract_id, run_id, items, report_path)
    attach = Path(report_path) if report_path and Path(report_path).exists() else None

    if method == "outlook":
        try:
            import win32com.client  # pywin32, installed with titlepilot on Windows
        except ImportError as e:
            raise RuntimeError("Outlook sending needs Windows with Outlook and pywin32 "
                               "(pip install pywin32), or set TITLEPILOT_SMTP_HOST to use SMTP") from e
        mail = win32com.client.Dispatch("Outlook.Application").CreateItem(0)
        mail.To, mail.Subject, mail.Body = "; ".join(to), subject, body
        if attach:
            mail.Attachments.Add(str(attach.resolve()))
        mail.Send()
        return f"sent to {', '.join(to)} (Outlook)"

    import smtplib
    from email.message import EmailMessage
    host, port = env("TITLEPILOT_SMTP_HOST"), int(env("TITLEPILOT_SMTP_PORT") or 587)
    user, password = env("TITLEPILOT_SMTP_USER"), env("TITLEPILOT_SMTP_PASSWORD")
    sender = env("TITLEPILOT_EMAIL_FROM") or user
    if not host or not sender:
        raise RuntimeError("SMTP sending needs TITLEPILOT_SMTP_HOST and TITLEPILOT_EMAIL_FROM (or TITLEPILOT_SMTP_USER)")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, ", ".join(to), subject
    msg.set_content(body)
    if attach:
        msg.add_attachment(attach.read_bytes(), maintype="application",
                           subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=attach.name)
    with (smtplib.SMTP_SSL(host, port, timeout=30) if port == 465 else smtplib.SMTP(host, port, timeout=30)) as smtp:
        if port != 465:
            smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.send_message(msg)
    return f"sent to {', '.join(to)}"


def send_notifications(tract_id, run_id, items, report_path=None) -> list[tuple[str, str]]:
    """Send every notification that is set up. Returns (channel, result) pairs; a failure is
    reported, not raised, so the run is never lost over a message."""
    results = []
    for channel, fn in (("Teams/Slack message", notify), ("Email", email)):
        try:
            r = fn(tract_id, run_id, items, report_path)
            if r is not None:
                results.append((channel, "sent" if channel != "Email" else r))
        except Exception as e:
            results.append((channel, f"FAILED: {e}"))
    return results
