"""Score a title report (this program's workbook) against SEG's finished surface ownership report (COT).

SEG's workbook: an 'SOR' sheet (surface ownership table, gross acres, title start and end dates) and a
'SURFACE-COT' sheet (chain of title, one row per instrument: NO., VOL/PAGE, TYPE, DATE, DATE FILED, GRANTOR,
GRANTEE, ...). Sheets and columns are found by their labels, not fixed cells.

Our workbook: the Ownership sheet (gross acres, vesting deed), Ownership BT Data (one row per owner),
AI Taxes & CAD (property ID) and the Runsheet (every instrument).

    titlepilot score-seg "<our report>.xlsx" "<SEG COT>.xlsx" [--out scoring.md]

Instruments are matched by recording reference first; probates and others SEG cites by cause number are
matched by type and grantor. SEG's surface COT lists the chain only, so easements and other instruments the
report has that SEG doesn't are listed as 'not on SEG's COT', not counted as errors.
"""
import re
from datetime import date, datetime
from pathlib import Path

import openpyxl

from .fuzzy import token_sort_ratio
from .validate import norm_date, norm_ref


def _s(v) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def _iso(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return norm_date(v) if v else None


def _find(ws, pattern):
    """First cell whose text matches pattern (case-insensitive)."""
    rx = re.compile(pattern, re.I)
    for row in ws.iter_rows():
        for c in row:
            if c.value is not None and rx.search(_s(c.value)):
                return c
    return None


def _right_of(ws, cell, max_cols=8):
    for col in range(cell.column + 1, cell.column + 1 + max_cols):
        v = ws.cell(cell.row, col).value
        if v not in (None, ""):
            return v
    return None


def _sheet(wb, *patterns):
    for name in wb.sheetnames:
        if any(re.search(p, name, re.I) for p in patterns):
            return wb[name]
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        m = re.search(r"\d+(?:\.\d+)?", str(v or ""))
        return float(m.group()) if m else None


def _ref_key(v) -> str:
    first = str(v or "").strip().split("\n")[0]
    m = re.search(r"cause\s*no\.?\s*([\d,]+)", first, re.I)
    if m:
        return "CAUSE" + m.group(1).replace(",", "")
    return norm_ref(first)


def read_seg(path) -> dict:
    wb = openpyxl.load_workbook(path, data_only=True)
    out = {"owners": [], "instruments": []}
    sor = _sheet(wb, r"^SOR$", r"ownership report", r"\bSOR\b")
    if sor is not None:
        hdr = _find(sor, r"^surface ownership$")
        if hdr is not None:
            cols = {_s(sor.cell(hdr.row, c).value).lower(): c for c in range(1, sor.max_column + 1)
                    if sor.cell(hdr.row, c).value}
            col = lambda word: next((c for k, c in cols.items() if word in k), None)
            r = hdr.row + 1
            while r <= sor.max_row:
                name = sor.cell(r, hdr.column).value
                if name and _s(name).upper().startswith("TOTAL"):
                    break
                if name:
                    lines = [ln.strip() for ln in str(name).split("\n") if ln.strip()]
                    out["owners"].append({
                        "name": re.sub(r",?\s+an?\s+.*$", "", lines[0]) if lines else "",
                        "name_as_written": lines[0] if lines else "",
                        "address": "\n".join(lines[1:]),
                        "decimal": _num(sor.cell(r, col("decimal")).value) if col("decimal") else None,
                        "net_acres": _num(sor.cell(r, col("net")).value) if col("net") else None,
                        "property_id": _s(sor.cell(r, col("property")).value) if col("property") else ""})
                r += 1
        for key, pat in (("gross_acres", r"^gross acres"), ("start", r"^title start date"),
                         ("end", r"^title end date"), ("legal", r"^legal description$")):
            c = _find(sor, pat)
            if c is None:
                continue
            if key == "legal":
                out[key] = _s(sor.cell(c.row + 1, c.column).value)
            else:
                v = _right_of(sor, c)
                out[key] = _num(v) if key == "gross_acres" else _iso(v)
    cot = _sheet(wb, r"cot", r"chain")
    if cot is not None:
        hdr = _find(cot, r"^vol\s*/\s*page$")
        if hdr is not None:
            cols = {}
            for c in range(1, cot.max_column + 1):
                k = _s(cot.cell(hdr.row, c).value).lower().replace(" ", "")
                if k:
                    cols[k] = c
            get = lambda r, k: cot.cell(r, cols[k]).value if k in cols else None
            for r in range(hdr.row + 1, cot.max_row + 1):
                ref = get(r, "vol/page")
                if not ref:
                    continue
                raw_date = get(r, "date")
                out["instruments"].append({
                    "no": get(r, "no."), "ref": _s(ref), "key": _ref_key(ref), "type": _s(get(r, "type")),
                    "executed": None if "death" in _s(raw_date).lower() else _iso(raw_date),
                    "date_of_death": _iso(raw_date) if "death" in _s(raw_date).lower() else None, "filed": _iso(get(r, "datefiled")),
                    "grantor": _s(get(r, "grantor")), "grantee": _s(get(r, "grantee"))})
    return out


def read_report(path) -> dict:
    wb = openpyxl.load_workbook(path, data_only=True)
    out = {"owners": [], "instruments": []}
    own = _sheet(wb, r"^ownership\s*$")
    if own is not None:
        out["gross_acres"] = _num(own["E16"].value)
        out["start"], out["end"] = _iso(own["C17"].value), _iso(own["E17"].value)
        out["vesting"] = _s(own["C28"].value)
        out["owner_block"] = str(own["B28"].value or "")
        out["legal"] = _s(own["B22"].value)
    bt = _sheet(wb, r"ownership bt data")
    if bt is not None:
        for r in range(2, bt.max_row + 1):
            last, first, middle = (bt.cell(r, c).value for c in (3, 4, 5))
            if last:
                out["owners"].append({"name": " ".join(_s(x) for x in (first, middle, last) if x),
                                      "decimal": (_num(bt.cell(r, 2).value) or 0) / 100})
    tx = _sheet(wb, r"taxes & cad")
    if tx is not None:
        for r in range(1, tx.max_row + 1):
            if _s(tx.cell(r, 1).value).lower() == "cad property id":
                out["property_id"] = _s(tx.cell(r, 2).value)
    rs = _sheet(wb, r"^runsheet\s*$")
    if rs is not None:
        for r in range(13, rs.max_row + 1):
            ref = rs.cell(r, 2).value
            if not ref:
                continue
            out["instruments"].append({
                "ref": _s(ref), "key": _ref_key(ref), "type": _s(rs.cell(r, 3).value),
                "grantor": _s(rs.cell(r, 4).value), "grantee": _s(rs.cell(r, 5).value),
                "executed": _iso(rs.cell(r, 6).value), "filed": _iso(rs.cell(r, 7).value)})
    return out


def _same_name(a, b) -> bool:
    a, b = (re.sub(r"[^a-z0-9 ]", " ", x.lower()) for x in (a, b))
    return token_sort_ratio(a, b) >= 88 or (len(a) > 6 and (a in b or b in a))


def _kind(t) -> str:
    t = t.lower()
    for k in ("probate", "affidavit", "partition", "ratification", "guardian", "distribution"):
        if k in t or (k == "probate" and t.startswith("prob")):
            return k
    return t


def _party(x) -> str:
    x = re.sub(r"\b(the|estate of|deceased|dec'd|in the matter of|guardianship of)\b", " ", x.lower())
    return re.sub(r"[^a-z0-9 ]", " ", x)


def _party_score(a, b) -> float:
    """Grantor similarity; a name whose words all appear in the other ('Norma Wrage' in 'Norma Rohde Wrage') counts."""
    a, b = _party(a), _party(b)
    ta, tb = set(a.split()), set(b.split())
    if ta and tb and (ta <= tb or tb <= ta) and min(len(ta), len(tb)) >= 2:
        return 100.0
    return token_sort_ratio(a, b)


def _match(seg_items, ours):
    """Pairs (SEG row, report row): by recording reference first, then (for cause numbers and odd references)
    same kind of instrument and the same grantor."""
    by_key = {}
    for o in ours:
        by_key.setdefault(o["key"], []).append(o)
    used, found = set(), {}
    for n, s in enumerate(seg_items):
        hit = next((o for o in by_key.get(s["key"], []) if id(o) not in used), None)
        if hit is not None:
            used.add(id(hit))
            found[n] = hit
    for n, s in enumerate(seg_items):
        if n in found:
            continue
        hit = max((o for o in ours if id(o) not in used and _kind(o["type"]) == _kind(s["type"])),
                  key=lambda o: _party_score(s["grantor"], o["grantor"]), default=None)
        if hit is not None and _party_score(s["grantor"], hit["grantor"]) >= 80:
            used.add(id(hit))
            found[n] = hit
    pairs = [(s, found[n]) for n, s in enumerate(seg_items) if n in found]
    missed = [s for n, s in enumerate(seg_items) if n not in found]
    extra = [o for o in ours if id(o) not in used]
    return pairs, missed, extra


def compare(report_path, seg_path) -> dict:
    seg, ai = read_seg(seg_path), read_report(report_path)
    checks = []

    def check(item, seg_v, ai_v, ok):
        checks.append({"item": item, "seg": seg_v, "ai": ai_v, "match": bool(ok)})

    seg_names = sorted(o["name"] for o in seg["owners"])
    ai_names = sorted(o["name"] for o in ai["owners"])
    check("Current surface owner(s)", "; ".join(seg_names), "; ".join(ai_names),
          len(seg_names) == len(ai_names) and all(_same_name(a, b) for a, b in zip(seg_names, ai_names)))
    seg_dec = sum(o["decimal"] or 0 for o in seg["owners"])
    ai_dec = sum(o["decimal"] or 0 for o in ai["owners"])
    check("Total decimal interest", seg_dec, ai_dec, abs(seg_dec - ai_dec) < 1e-4)
    check("Gross acres", seg.get("gross_acres"), ai.get("gross_acres"),
          seg.get("gross_acres") and ai.get("gross_acres") and abs(seg["gross_acres"] - ai["gross_acres"]) < 0.01)
    seg_pid = next((o["property_id"] for o in seg["owners"] if o["property_id"]), "")
    if seg_pid:
        pid = lambda x: re.sub(r"\.0$", "", (_s(x).split() or [""])[0])
        check("CAD property ID", seg_pid, ai.get("property_id"), pid(seg_pid) == pid(ai.get("property_id")))
    seg_addr = next((o["address"] for o in seg["owners"] if o["address"]), "")
    if seg_addr:
        norm = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
        one_line = lambda x: ", ".join(ln.strip().rstrip(",") for ln in x.split("\n") if ln.strip())
        check("Owner address", one_line(seg_addr), one_line(ai.get("owner_block", "")),
              norm(seg_addr) in norm(ai.get("owner_block", "")))
    deeds = [i for i in seg["instruments"] if re.search(r"deed|partition|judgment", i["type"], re.I)]
    vest_seg = (deeds or seg["instruments"] or [{}])[-1].get("ref")   # SEG lists the vesting deed last
    if vest_seg:
        check("Vesting deed", vest_seg, ai.get("vesting"), _ref_key(vest_seg) == _ref_key(ai.get("vesting")))
    check("Title start date", seg.get("start"), ai.get("start"), seg.get("start") == ai.get("start"))
    check("Title end date", seg.get("end"), ai.get("end"), seg.get("end") == ai.get("end"))
    if seg.get("legal"):
        ref_in = vest_seg and re.sub(r"\D", "", vest_seg.split("/")[0]) in (ai.get("legal") or "")
        check("Legal description ties to vesting deed", "yes", "yes" if ref_in else "no", ref_in)

    pairs, missed, extra = _match(seg["instruments"], ai["instruments"])
    rows = []
    for s, o in pairs:
        ex_ok = not s["executed"] or not o["executed"] or s["executed"] == o["executed"]
        fi_ok = not s["filed"] or not o["filed"] or s["filed"] == o["filed"]
        rows.append({"seg": s, "ai": o, "executed_match": ex_ok, "filed_match": fi_ok})
    n = len(seg["instruments"])
    return {
        "checks": checks, "instruments": rows, "missed": missed, "not_on_seg": extra,
        "summary": {
            "header_checks_matched": sum(c["match"] for c in checks), "header_checks": len(checks),
            "seg_instruments": n, "found": len(pairs), "missed": len(missed),
            "dates_agree": sum(r["executed_match"] and r["filed_match"] for r in rows),
            "not_on_seg": len(extra),
            "chain_recall": round(len(pairs) / n, 3) if n else None}}


def to_markdown(res, report_path=None, seg_path=None) -> str:
    sm = res["summary"]
    yn = lambda b: "MATCH" if b else "DIFFERENT"
    out = ["# Scoring against SEG's COT", ""]
    if report_path:
        out += [f"Report: {Path(report_path).name}  ", f"SEG: {Path(seg_path).name}", ""]
    out += [f"- Header checks matched: {sm['header_checks_matched']} of {sm['header_checks']}",
            f"- SEG chain instruments found: {sm['found']} of {sm['seg_instruments']} (missed {sm['missed']})",
            f"- Dates agree on {sm['dates_agree']} of {sm['found']} found",
            f"- In the report but not on SEG's COT (easements, releases, etc.; not errors): {sm['not_on_seg']}", "",
            "Probate dates often differ by convention (SEG may give the date of death or the filing of the certified "
            "copy), so check those by hand.", "",
            "## Header", "", "| Item | SEG | Report | Result |", "|---|---|---|---|"]
    for c in res["checks"]:
        out.append(f"| {c['item']} | {c['seg']} | {c['ai']} | {yn(c['match'])} |")
    out += ["", "## Chain of title", "", "| SEG # | SEG ref | Report ref | Type (SEG / report) | Executed | Filed |",
            "|---|---|---|---|---|---|"]
    for r in res["instruments"]:
        s, o = r["seg"], r["ai"]
        ex = ("n/a (SEG gives date of death)" if s.get("date_of_death") else
              "ok" if r["executed_match"] else f"SEG {s['executed']} / report {o['executed']}")
        fi = "ok" if r["filed_match"] else f"SEG {s['filed']} / report {o['filed']}"
        out.append(f"| {s['no'] or ''} | {s['ref']} | {o['ref']} | {s['type']} / {o['type']} | {ex} | {fi} |")
    out += ["", "## On SEG's COT, not found in the report", ""]
    out += [f"- {s['no'] or ''} {s['ref']} {s['type']} ({s['grantor']} to {s['grantee']})" for s in res["missed"]] or ["- none"]
    out += ["", "## In the report, not on SEG's COT", ""]
    out += [f"- {o['ref']} {o['type']} ({o['grantor']} to {o['grantee']})" for o in res["not_on_seg"]] or ["- none"]
    return "\n".join(out) + "\n"
