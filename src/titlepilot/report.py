"""Write the title report in SEG's format, from the blank template in templates/.

Sheets filled, matching the SEG report layout:
  Ownership          header block, property description, surface and pore space ownership
  Ownership BT Data  one row per owner
  Encumbrances       mortgages/liens table, roads, railroads, all other current encumbrances
  Flowchart          the chain of title drawn in cells (the template's drawn boxes can't be copied)
  Names Examined     owners in the chain with their ownership periods
  Index to All Documents, Runsheet   every recorded instrument in filing order
Mineral, NPRI and non-executive sections are left blank: this is a surface report.
AI sheets are added at the end: AI Review (summary and ownership notes), AI Mapping (out-sales and
metes and bounds tracts to map), AI Flags, AI Liens, AI Easements & Leases, AI Taxes & CAD, AI Legal Description
(preliminary, approved by a named reviewer), AI All Fields (every field read, so fields added to the workbook show
up with no code change), AI Supporting Docs. Everything here is a draft for a landman to check and sign.
"""
import re
from copy import copy
from datetime import date
from pathlib import Path

import openpyxl
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .mapping import mapping_items
from .validate import norm_ref

TEMPLATE = Path(__file__).with_name("templates") / "surface_title_report.xlsx"

ABBREVIATIONS = [  # (keyword in the instrument type, abbreviation used on SEG runsheets); first match wins
    ("carbon dioxide storage", "CO2 Storage Lease"), ("storage lease", "Storage Lease"), ("solar easement", "Solar Lease"),
    ("wind energy", "Wind Lease"), ("ground lease", "Lease"), ("land lease", "Lease"), ("timber", "Timber Deed"),
    ("accommodation", "Accom. Agmt."), ("possession and use", "P&U Agmt."), ("surface agreement", "Surface Agmt."),
    ("special warranty", "SWD"), ("warranty deed", "WD"), ("deed of gift", "GD"), ("gift deed", "GD"),
    ("quitclaim", "QCD"), ("deed without warranty", "DWW"), ("deed of trust", "DT"), ("release", "Rel"),
    ("right-of-way", "ROW"), ("right of way", "ROW"), ("easement", "Esmt"), ("ucc", "UCC"),
    ("financing statement", "UCC"), ("abstract of judgment", "AJ"), ("judgment", "Jdgmt"), ("lis pendens", "LP"),
    ("affidavit of heirship", "AOH"), ("correction", "Corr. Affd."), ("affidavit", "Affd."),
    ("oil, gas", "OGML"), ("oil and gas", "OGML"), ("mineral lease", "OGML"), ("probate", "Prob."),
    ("tax deed", "Tax Deed"), ("sheriff", "Sheriff's Deed"), ("lien", "Lien"), ("plat", "Plat"),
]

RED = PatternFill("solid", fgColor="F8D7DA")


def abbrev(itype: str | None) -> str:
    t = (itype or "").lower()
    for key, short in ABBREVIATIONS:
        if key in t:
            return short
    return (itype or "").title()


def display_ref(inst) -> str:
    """'2011-9176' -> '2011009176', 'V708P1' -> '708/1', as written on SEG runsheets."""
    key = norm_ref(inst.get("instrument_id"))
    m = re.fullmatch(r"(\d{4})-(\d+)", key)
    if m:
        return f"{m.group(1)}{int(m.group(2)):06d}"
    m = re.fullmatch(r"V(\d+)P(\d+)", key)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return inst.get("instrument_id") or ""


MAJOR = (("carbon", "CARBON"), ("co2", "CARBON"), ("sequestration", "CARBON"), ("storage lease", "CARBON"),
         ("solar", "SOLAR"), ("wind", "WIND"), ("grazing", "GRAZING"), ("agricultur", "GRAZING"),
         ("oil, gas", "OGML"), ("oil and gas", "OGML"))


def is_lease_paper(e) -> bool:
    """An amendment, assignment or other follow-on paper for a lease (listed in the Index, not the tables)."""
    t = e["type"]
    return any(k in t for k in ("amendment", "assignment", "advisory", "consulting")) and (
        "lease" in t or e["category"] in ("pore space agreement", "surface agreement") or any(k in t for k, _ in MAJOR))


def material_lease(e) -> bool:
    t = e["type"]
    return (not is_lease_paper(e) and e["category"] in ("pore space agreement", "surface agreement")
            and "accommodation" not in t and any(k in t for k, _ in MAJOR))


def lease_label(e) -> str:
    return next((label for k, label in MAJOR if k in e["type"]), abbrev(e["type"]))


def record_type(inst) -> str:
    """The agent's own label when the file name starts with one (DR_..., OPR_...); otherwise Deed Records
    for volume/page references and Official Public Records for instrument numbers."""
    m = re.match(r"(DR|OPR|OR|DT|PR)[ _]", Path(str(inst.get("file_name") or "")).name, re.I)
    if m:
        return m.group(1).upper()
    return "DR" if norm_ref(inst.get("instrument_id")).startswith("V") else "OPR"


def as_date(iso):
    try:
        return date.fromisoformat(iso) if iso else None
    except ValueError:
        return None


def fmt_date(iso) -> str:
    d = as_date(iso)
    return f"{d.month}/{d.day}/{d.year}" if d else "undated"


def names(lst) -> str:
    return ", ".join(n for n in (lst or []) if n)


RECORD_NAMES = {"DR": "Deed Records", "OPR": "Official Public Records", "OR": "Official Records",
                "DT": "Deed of Trust Records", "PR": "Probate Records"}
_STREET_END = re.compile(r"^(.*\b(?:P\.?\s*O\.?\s*BOX\s+\d+|(?:ST|STREET|AVE|AVENUE|RD|ROAD|DR|DRIVE|BLVD|LN|LANE|HWY|"
                         r"HIGHWAY|PKWY|CT|CIR|WAY|PL|TRL|FM\s*\d+)\.?(?:\s+(?:STE|SUITE|#|APT|UNIT)\.?\s*\w+)?))\s+"
                         r"([A-Za-z .]+?),?\s+(TX|TEXAS)\.?\s+(\d{5}(?:-\d{4})?)$", re.I)


def format_address(addr) -> str | None:
    """'PO BOX 52267 MIDLAND TX 79710' -> 'P.O. Box 52267\\nMidland, Texas 79710' (SEG's style). Addresses
    that don't parse are returned as written, one line."""
    if not addr:
        return None
    a = re.sub(r"\s*\(from [^)]*\)\s*$", "", str(addr)).strip()
    a = re.sub(r"\s+", " ", a)
    m = _STREET_END.match(a)
    if not m:
        return a
    street = re.sub(r"\bP\.?\s*O\.?\s*BOX\b", "P.O. Box", m.group(1).title(), flags=re.I)
    return f"{street}\n{m.group(2).strip().title()}, Texas {m.group(4)}"


def owner_full_name(name, vesting) -> str:
    """The owner as the vesting deed names it, with its description ('..., LLC, a Texas limited liability
    company'), when the grantee there starts with the short name."""
    short = re.sub(r"[\s,.]+$", "", name or "").lower()
    for g in (vesting or {}).get("grantees") or []:
        if g and g.lower().startswith(short) and len(g) > len(short) + 3:
            return g.strip()
    return name


def owner_block(name, vesting, chain) -> str:
    """Name (as vested) and mailing address, as on SEG's ownership table. Address from chain['owner_addresses']
    (name -> address) or, for a sole owner, the appraisal district mailing address."""
    full = owner_full_name(name, vesting)
    addr = (chain.get("owner_addresses") or {}).get(name)
    if not addr and len(chain.get("current_owner") or []) == 1:
        tx = chain.get("tax") or {}
        addr = tx.get("cad_mailing_address") or tx.get("notice_address")
    addr = format_address(addr)
    if addr and re.search(r",\s*an?\s", full):
        full = full.replace(", a ", ",\na ", 1).replace(", an ", ",\nan ", 1)
    return f"{full}\n{addr}" if addr else full


def _long_date(iso) -> str | None:
    d = as_date(iso)
    return f"{d:%B} {d.day}, {d.year}" if d else None


def tied_legal(land, acres, vesting, county) -> str:
    """Property description tied to the vesting deed, SEG style: '642.3 acres, more or less, of <land>, being
    more particularly described in that certain Special Warranty Deed dated ..., recorded in Volume 346, Page 115,
    of the Official Public Records of ... County, Texas, to which reference is made for a more complete description.'"""
    land = re.sub(r"\s+", " ", (land or "").strip()).rstrip(".;, ")
    if not vesting:
        return land
    if "more particularly described" in land.lower():
        return land
    if acres and not re.search(r"\bacres?\b", land, re.I):
        land = re.sub(r"^all of\s+", "", land, flags=re.I)
        land = f"{acres:g} acres, more or less, of {land}"
    key = norm_ref(vesting.get("instrument_id"))
    m = re.fullmatch(r"V(\d+)P(\d+)", key)
    where = (f"in Volume {m.group(1)}, Page {m.group(2)}" if m
             else f"under Instrument No. {vesting.get('instrument_no') or display_ref(vesting)}")
    rec = RECORD_NAMES.get(record_type(vesting), "Official Public Records")
    cty = (vesting.get("county") or county or "").replace(" County", "").strip()
    dated, filed = _long_date(vesting.get("execution_date")), _long_date(vesting.get("filing_date"))
    gtor, gtee = vesting.get("grantors") or [], vesting.get("grantees") or []
    kind = (vesting.get("type") or "deed").title()
    parts = [f"that certain {kind}"]
    if dated or filed:
        parts.append(f"dated {dated}," if dated else f"filed {filed},")
    if gtor:
        parts.append(f"from {names(gtor)}, as Grantor{'s' if len(gtor) > 1 else ''},")
    if gtee:
        parts.append(f"to {names(gtee)}, as Grantee{'s' if len(gtee) > 1 else ''},")
    parts.append(f"recorded {where}, of the {rec}" + (f" of {cty} County, Texas" if cty else ""))
    text = " ".join(parts)
    return f"{land}, being more particularly described in {text}, to which reference is made for a more complete description."


def abstractor_note(inst) -> str:
    """Short note in the style of the SEG runsheet: what it conveys, what it reserves."""
    parts = []
    cat = inst.get("category")
    if cat == "conveyance":
        parts.append(f"Conveys {inst['interest']}" if inst.get("interest") else "Conveys")
        if inst.get("mineral_exception"):
            parts.append("reserves/excepts minerals")
    elif cat == "easement":
        parts.append(inst.get("easements") or "Easement")
    elif cat in ("lien", "judgment"):
        parts.append(inst.get("liens") or inst.get("type", "").title())
    elif inst.get("notes"):
        parts.append(inst["notes"])
    return "; ".join(p for p in parts if p)[:250]


def _copy_row_style(ws, src_row, dst_row, c1, c2):
    for c in range(c1, c2 + 1):
        ws.cell(dst_row, c)._style = copy(ws.cell(src_row, c)._style)
    ws.row_dimensions[dst_row].height = ws.row_dimensions[src_row].height


def _fit_height(ws, row, c1, c2, min_height=24, pt=9):
    """Row height from the longest wrapped value in the row (template heights belong to other rows)."""
    lines = 1
    for c in range(c1, c2 + 1):
        v = ws.cell(row, c).value
        if v is None or not isinstance(v, str):
            continue
        width = ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width or 10
        per_line = max(8, int(width * 1.15))
        lines = max(lines, sum(1 + len(part) // per_line for part in v.split("\n")))
    ws.row_dimensions[row].height = max(min_height, lines * (pt + 3) + 4)


def _fit_width(ws, landscape=None):
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    if landscape is not None:
        ws.page_setup.orientation = "landscape" if landscape else "portrait"


def _write_rows(ws, first_row, c1, c2, rows):
    """Write rows starting at first_row, styling any rows beyond the template's like the first one."""
    styled_until = first_row
    while styled_until <= ws.max_row and ws.cell(styled_until, c1).has_style:
        styled_until += 1
    for n, values in enumerate(rows):
        r = first_row + n
        if r >= styled_until:
            _copy_row_style(ws, first_row, r, c1, c2)
        for c, v in enumerate(values, c1):
            ws.cell(r, c).value = v
        _fit_height(ws, r, c1, c2)
    return first_row + len(rows) - 1 if rows else first_row


_CAPACITY = re.compile(r"\(.*?\)|,\s*an?\s+[a-z]+\s+(?:limited|general|corporation|company|nonprofit)\b.*$|"
                       r",?\s*\b(?:co-?trustees?|trustees?|independent executor|executor|executrix|"
                       r"administrator\b.*|guardian\b.*|a/k/a\b.*|deceased|dec'd)\b\.?", re.I)
_SUFFIX = {"jr", "sr", "ii", "iii", "iv"}


def _name_key(n: str) -> str:
    """Matching key for a person or entity: no capacity words, 'Last, First' turned round, no punctuation."""
    n = _CAPACITY.sub(" ", n or "")
    n = re.sub(r"^(?:the\s+)?estate of\s+", "", n.strip(), flags=re.I)
    m = re.fullmatch(r"\s*([A-Za-z'\-]+),\s*([^,]+?)\s*", n)
    if m and not re.search(r"\b(llc|inc|ltd|lp|co|company|corp)\b", n, re.I):
        n = f"{m.group(2)} {m.group(1)}"
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", n.lower()).split())


def _same_person(a: str, b: str) -> bool:
    """Same name allowing a missing middle name ('Kay Wrage Gunn' / 'Kay Lee Wrage Gunn'); Jr./Sr. must agree."""
    ka, kb = _name_key(a), _name_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    ta, tb = ka.split(), kb.split()
    sa, sb = {t for t in ta if t in _SUFFIX}, {t for t in tb if t in _SUFFIX}
    if sa != sb:
        return False
    ta, tb = [t for t in ta if t not in _SUFFIX], [t for t in tb if t not in _SUFFIX]
    if len(ta) >= 2 and len(tb) >= 2 and ta[0] == tb[0] and ta[-1] == tb[-1]:
        short, long_ = sorted((ta[1:-1], tb[1:-1]), key=len)
        # middle names may be missing or initials: 'John B. Gunn' / 'John Bridge Gunn'
        if all(any(m == x or (len(m) == 1 and x.startswith(m)) or (len(x) == 1 and m.startswith(x)) for x in long_)
               for m in short):
            return True
    from .fuzzy import token_sort_ratio
    return token_sort_ratio(ka, kb) >= 90


def _owner_periods(chain, r_from=None, r_to=None):
    """FROM and TO dates for every name in the chain (for Names Examined).

    FROM: when the name took title (grantee of a conveyance, or named as heir/devisee in an affidavit,
    probate or distribution). A name with no recorded acquisition (an heir, a joining spouse) held under the
    owner of record at the time, so FROM is the filing of the last conveyance before the name first appears;
    a name before any conveyance in the package gets the start of the search.
    TO: the name's last conveyance as grantor. Current owners run to the end of the search. A name that never
    conveyed (an owner who died) runs to the probate or affidavit dealing with the estate, else to the next
    conveyance by others."""
    from .chain import is_fractional, is_partial_area
    instruments = sorted((i for i in chain.get("instruments") or chain["conveyances"] if i.get("filing_date")),
                         key=lambda i: i["filing_date"])
    convs = sorted((c for c in chain["conveyances"] if c.get("filing_date")), key=lambda c: c["filing_date"])
    whole = [c for c in convs if not is_fractional(c) and not is_partial_area(c, chain.get("subject_acres"))]
    estate_docs = [i for i in instruments if re.search(r"probate|affidavit|heirship|distribution|guardian|will\b",
                                                       i.get("type") or "", re.I)]
    fiduciary = re.compile(r"administrat|executor|executrix|guardian|trustee", re.I)
    names = []
    for n in chain.get("owners") or [x for c in convs for x in (c.get("grantors") or []) + (c.get("grantees") or [])]:
        if n and not any(_same_person(n, m) for m in names):
            names.append(n)
    involved = lambda n, i: any(_same_person(n, x) for x in (i.get("grantors") or []) + (i.get("grantees") or []))
    current = chain.get("current_owner") or []
    periods = {}
    for n in names:
        took = [c["filing_date"] for c in convs if any(_same_person(n, g) for g in c.get("grantees") or [])]
        took += [d["filing_date"] for d in estate_docs if any(_same_person(n, g) for g in d.get("grantees") or [])]
        gave = [c["filing_date"] for c in convs if any(_same_person(n, g) for g in c.get("grantors") or [])]
        first_seen = min((i["filing_date"] for i in instruments if involved(n, i)), default=None)
        start = min(took) if took else None
        if start is None and first_seen:
            # no recorded acquisition: a fiduciary from the estate paper before it acts; an heir or joining
            # spouse from the last conveyance of the whole tract before the name appears
            pool = estate_docs if fiduciary.search(n) else whole
            before = [c["filing_date"] for c in pool if c["filing_date"] < first_seen]
            start = max(before) if before else r_from
        if any(_same_person(n, o) for o in current):
            end = None            # filled with the end of the search
        elif gave:
            end = max(gave)
        else:
            later = [d["filing_date"] for d in estate_docs if start and d["filing_date"] >= start and
                     any(_same_person(n, x) or _name_key(n) in _name_key(" ".join(d.get("grantors") or []) + " " + (d.get("type") or ""))
                         for x in (d.get("grantors") or []))]
            nxt = [c["filing_date"] for c in convs if start and c["filing_date"] > start and not involved(n, c)]
            end = min(later) if later else (min(nxt) if nxt else None)
        periods[n] = [start or r_from, end]
    return periods


def _split_name(n):
    if re.search(r"\b(llc|inc|co|company|corp|corporation|trust|ltd|lp|partnership|bank|estate)\b\.?", n, re.I):
        return n, "", ""
    parts = n.replace(",", " ").split()
    suffix = [p for p in parts if p.rstrip(".").lower() in ("jr", "sr", "ii", "iii", "iv")]
    core = [p for p in parts if p not in suffix]
    if len(core) < 2:
        return n, "", ""
    return " ".join(core[-1:] + suffix), core[0], " ".join(core[1:-1])


def write_report(path, intake, chain, flags, skipped, problems, run_id, researched=None) -> Path:
    """researched: (from_iso, to_iso) for the search period, if known."""
    wb = openpyxl.load_workbook(TEMPLATE)
    instruments = chain["instruments"]
    acres = chain.get("subject_acres")
    owners = chain["current_owner"]
    vesting = next((i for i in instruments if i["instrument_id"] == chain.get("vesting_instrument")), None)
    dated = [i["filing_date"] for i in instruments if i["filing_date"]]
    r_from, r_to = researched or (min(dated) if dated else None, max(dated) if dated else None)
    certified = getattr(intake, "certified_through", None)
    if certified:            # title end date = the date the county's records are certified through
        r_to = certified
    client = (intake.tract_id or "").split(".")[0].upper()
    legal = tied_legal(intake.legal_description or (vesting or {}).get("legal") or "", acres, vesting, intake.county)

    # ---- Ownership ----------------------------------------------------------------------------
    ws = wb["Ownership "]
    if client:
        ws["B5"] = f"{client} TITLE OWNERSHIP REPORT"
    ws["B8"] = f"REPORT DATE: {date.today().month}/{date.today().day}/{date.today().year}"
    ws["B9"] = "REVISED DATE: N/A"
    ws["B11"] = names(owners) or "OWNER NOT DETERMINED"
    ws["C14"] = intake.tract_id
    ws["E14"] = getattr(intake, "aoi", None)
    ws["C15"] = (intake.county or "").replace(" County", "")
    ws["C16"] = intake.agent or "AI draft - landman review required"
    ws["E16"] = acres
    ws["C17"], ws["E17"] = as_date(r_from), as_date(r_to)
    ws["B22"] = legal
    ws["B22"].alignment = Alignment(wrap_text=True, vertical="top", horizontal="left")
    ws.row_dimensions[22].height = max(ws.row_dimensions[22].height or 15, 13 * (1 + len(legal) // 95) + 6)
    ps = chain.get("pore_space") or {"owners": owners, "vesting_instrument": chain.get("vesting_instrument")}
    ps_vesting = next((i for i in instruments if i["instrument_id"] == ps.get("vesting_instrument")), None)
    for row, who, deed in ((28, owners, vesting), (35, ps.get("owners") or [], ps_vesting)):
        ws[f"B{row}"] = "\n\n".join(owner_block(o, deed or vesting, chain) for o in who) if who else "OWNER NOT DETERMINED"
        ws[f"C{row}"] = display_ref(deed) if deed else None
        ws[f"D{row}"] = 1 if who else 0
        ws[f"E{row}"] = acres
        ws[f"B{row}"].alignment = Alignment(wrap_text=True, vertical="center", horizontal="left")
        ws.row_dimensions[row].height = max(ws.row_dimensions[row].height or 15,
                                            13 * (str(ws[f"B{row}"].value).count("\n") + 1) + 6)
    ws["B62"] = intake.records_searched or None

    # ---- Ownership BT Data --------------------------------------------------------------------
    ws = wb["Ownership BT Data"]
    for n, o in enumerate(owners):
        last, first, middle = _split_name(o)
        for c, v in enumerate([intake.tract_id, round(100 / len(owners), 6), last, first, middle,
                               None, None, None, None, None, None, "S"], 1):
            ws.cell(2 + n, c).value = v

    # ---- Encumbrances -------------------------------------------------------------------------
    # SEG layout: the Active Material Encumbrances table holds the major leases (solar, wind, carbon
    # storage, oil and gas, grazing); every easement, right of way and other surface burden goes on the
    # ROWs and Easements tab, and the text sections point there.
    ws = wb["Encumbrances"]
    current = [e for e in chain["encumbrances"] if e["status"] == "open"]
    is_mortgage = lambda e: e["category"] == "lien" and any(k in e["type"] for k in ("deed of trust", "mortgage",
                                                                                     "vendor"))
    text = lambda e: (f"{abbrev(e['type'])} - {record_type(e)} {display_ref(e)}, dated "
                      f"{fmt_date(e.get('execution_date') or e['filing_date'])} from {names(e['grantors']) or '?'} "
                      f"to {names(e['grantees']) or '?'}" + (f" - {abstractor_note(e)}" if abstractor_note(e) else ""))
    mortgages = [e for e in current if is_mortgage(e)]
    major = [e for e in current if material_lease(e)]
    follow_on = [e for e in current if e not in major and is_lease_paper(e)]     # amendments/assignments of leases
    row_items = [e for e in current if e not in mortgages + major + follow_on
                 and e["category"] in ("easement", "surface agreement", "restriction")
                 and "accommodation" not in e["type"]]
    other = [e for e in current if e not in mortgages + major + follow_on + row_items
             and e["category"] not in ("pore space agreement", "surface agreement")]

    # Burdens created inside conveyances: reserved easements and deed restrictions (rights of first
    # refusal, use covenants). They aren't separate instruments, so they're listed as text lines.
    deed_lines_roads, deed_lines_other, deed_lines_pore = [], [], []
    for c in chain["conveyances"]:
        tag = f"{abbrev(c['type'])} - {record_type(c)} {display_ref(c)}, dated {fmt_date(c.get('execution_date') or c['filing_date'])}"
        if c.get("easements"):
            line = f"{tag} - reserved: {c['easements']}"
            (deed_lines_roads if "road" in c["easements"].lower() else deed_lines_other).append(line)
        if c.get("restrictions"):
            deed_lines_other.append(f"{tag} - {c['restrictions']}")
        if c.get("pore_space"):
            deed_lines_pore.append(f"{tag} - pore space: {c['pore_space']}")
    ps = chain.get("pore_space") or {}
    if ps.get("severed"):
        deed_lines_pore.insert(0, f"Pore space severed from the surface: {ps.get('basis')}")

    for n, e in enumerate(major[:9]):
        for c, v in zip("BCDEFGHI", [lease_label(e), names(e["grantors"]), names(e["grantees"]),
                                     as_date(e.get("execution_date") or e["filing_date"]), e.get("term"),
                                     display_ref(e), "Current", e.get("legal")]):
            ws[f"{c}{5 + n}"] = v
            ws[f"{c}{5 + n}"].alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[5 + n].height = max(30, 15 * (1 + len(str(e.get("term") or "")) // 30))
    if not major:
        ws["C6"] = "None"
    elif len(major) == 1:
        ws["C6"] = None          # clear the template's placeholder
    for n, e in enumerate(mortgages[:3]):
        for c, v in zip("BCDEFGHI", [abbrev(e["type"]), names(e["grantors"]), names(e["grantees"]),
                                     as_date(e.get("execution_date") or e["filing_date"]), e.get("term"),
                                     display_ref(e), None, e.get("legal")]):
            ws[f"{c}{18 + n}"] = v
    if not mortgages:
        ws["C18"] = "None"
    other += mortgages[3:] + major[9:]
    see = ["See ROWs and Easements Tab"] if row_items else []
    sections = {"B24": deed_lines_pore, "B29": see + deed_lines_roads, "B34": list(see), "B39": list(see),
                "B44": see + [text(e) for e in other] + deed_lines_other}
    if not row_items:   # no ROW tab entries: list the roads and railroads in their own sections, as before
        roads = [e for e in current if e["category"] == "easement" and "road" in f"{e['type']} {e.get('easements')}".lower()]
        sections["B29"] = [text(e) for e in roads] + deed_lines_roads
    for cell, lines in sections.items():
        ws[cell] = "\n".join(lines) if lines else "None"
        ws[cell].alignment = Alignment(wrap_text=True, vertical="top", horizontal="left")
        n = max(1, sum(1 + len(t) // 110 for t in lines))
        ws.row_dimensions[ws[cell].row].height = max(ws.row_dimensions[ws[cell].row].height or 15, 15 * n)

    _fit_width(ws)

    # ---- ROWs and Easements (same columns as the Runsheet) --------------------------------------
    rows_ws = wb.copy_worksheet(wb["Runsheet "])
    rows_ws.title = "ROWs and Easements"
    wb.move_sheet(rows_ws, offset=wb.sheetnames.index("Encumbrances") + 1 - wb.sheetnames.index("ROWs and Easements"))
    rows_ws["A2"] = "ACTIVE RIGHT OF WAYS AND EASEMENTS"
    row_items_sorted = sorted(row_items, key=lambda e: e["filing_date"] or e.get("execution_date") or "")
    row_table = [[n, display_ref(e), e["type"].title(), names(e["grantors"]), names(e["grantees"]),
                          as_date(e.get("execution_date")), as_date(e["filing_date"]), record_type(e),
                          (e.get("county") or intake.county or "").upper() or None,
                          "; ".join(x for x in (e.get("easements"), e.get("legal")) if x)[:400],
                          "; ".join(x for x in (e.get("term") and f"Term: {e['term']}", e.get("notes")) if x) or None]
                         for n, e in enumerate(row_items_sorted, 1)]

    # ---- Names Examined -----------------------------------------------------------------------
    ws = wb["Names Examined"]
    periods = _owner_periods(chain, r_from, r_to)
    from .names_checklist import INDEX_NAMES, parse_indices
    checklist = list(getattr(intake, "names_examined", None) or [])
    default_idx = parse_indices(getattr(intake, "indices_examined", None))
    entries = [{"name": n, "from": a, "to": b or r_to, "indices": set(default_idx) if not checklist else set()}
               for n, (a, b) in periods.items()]
    for c in checklist:          # the agent's checklist: the period and indices actually searched
        e = next((e for e in entries if _same_person(c["name"], e["name"])), None)
        if e is None:
            entries.append(e := {"name": c["name"], "from": None, "to": None, "indices": set()})
        e["from"], e["to"] = c.get("from") or e["from"], c.get("to") or e["to"]
        e["indices"] |= c.get("indices") or set()
    for name in (intake.owners or []):    # names the agent searched that aren't in the chain: the search period
        if not any(_same_person(name, e["name"]) for e in entries):
            entries.append({"name": name, "from": r_from, "to": r_to, "indices": set(default_idx)})
    cell_date = lambda v: as_date(v) or v
    rows = [[e["name"], cell_date(e["from"]), cell_date(e["to"])] + ["X" if col in e["indices"] else None
                                                                    for col in INDEX_NAMES]
            for e in entries]
    _write_rows(ws, 5, 2, 13, rows)

    # ---- Index to All Documents and Runsheet ---------------------------------------------------
    page = 1
    index_rows, run_rows = [], []
    for n, i in enumerate(instruments, 1):
        eff = as_date(i.get("execution_date"))
        filed = as_date(i["filing_date"])
        index_rows.append([page, abbrev(i["type"]), names(i["grantors"]), names(i["grantees"]), eff, filed,
                           display_ref(i) if record_type(i) == "DR" else None, i.get("instrument_no")
                           or (display_ref(i) if record_type(i) == "OPR" else None)])
        page += i.get("pages") or 1
        run_rows.append([n, display_ref(i), abbrev(i["type"]), names(i["grantors"]), names(i["grantees"]), eff,
                         filed, record_type(i), f"{i.get('county') or intake.county} County" if (i.get('county') or intake.county) else None,
                         (i.get("legal") or "")[:250], abstractor_note(i)])
    ws = wb["Index to All Documents"]
    last = _write_rows(ws, 3, 2, 9, index_rows)
    ws.conditional_formatting.add(f"F3:G{max(last, 3)}", FormulaRule(formula=["AND($F3<>\"\",$G3<>\"\",$G3<$F3)"], fill=RED))
    ws = wb["Runsheet "]
    a, b = fmt_date(r_from), fmt_date(r_to)
    ws["A4"] = f"Records from {a} to {b}" if researched else f"Documents in package dated {a} to {b}"
    ws["A6"] = legal
    ws["A8"] = f"{acres:.2f} ACRES, MORE OR LESS" if acres else None
    for sheet in (wb["ROWs and Easements"],):
        sheet["A4"], sheet["A6"], sheet["A8"] = ws["A4"].value, ws["A6"].value, ws["A8"].value
        rlast = _write_rows(sheet, 13, 1, 11, row_table or [["", "None"]])
        sheet.print_area = f"A1:K{max(rlast, 13)}"
    last = _write_rows(ws, 13, 1, 11, run_rows)
    ws.conditional_formatting.add(f"F13:G{max(last, 13)}", FormulaRule(formula=["AND($F13<>\"\",$G13<>\"\",$G13<$F13)"], fill=RED))
    ws.print_area = f"A1:K{max(last, 13)}"

    # ---- Flowchart (chain of title in cells) ---------------------------------------------------
    pos = wb.sheetnames.index("Flowchart")
    wb.remove(wb["Flowchart"])                 # the template's sheet is sized for drawn shapes
    ws = wb.create_sheet("Flowchart", pos)
    thin = Side(style="thin", color="595959")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws["B1"] = "CHAIN OF TITLE (surface conveyances, filing order)"
    ws["B1"].font = Font(name="Times New Roman", size=14, bold=True)
    ws.column_dimensions["B"].width = 90
    r = 3
    for c in chain["conveyances"]:
        size = f"{c['acreage']:g} ac" if c.get("acreage") else "acreage not read"
        interest = f"; {c['interest']}" if c.get("interest") else ""
        is_vesting = c["instrument_id"] == chain.get("vesting_instrument")
        cell = ws.cell(r, 2, f"{abbrev(c['type'])} {display_ref(c)}  ({fmt_date(c['filing_date'])})\n"
                             f"{names(c['grantors']) or '?'}  to  {names(c['grantees']) or '?'}\n{size}{interest}"
                             + ("\nVESTING DEED (current owner)" if is_vesting else ""))
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        cell.border = box
        cell.font = Font(name="Times New Roman", size=10, bold=is_vesting)
        if is_vesting:
            cell.fill = PatternFill("solid", fgColor="E2EFDA")
        ws.row_dimensions[r].height = 62 if is_vesting else 48
        ws.cell(r + 1, 2, "↓").alignment = Alignment(horizontal="center")
        r += 2
    ws.cell(r, 2, f"CURRENT SURFACE OWNER: {names(owners) or 'not determined'}").font = Font(
        name="Times New Roman", size=12, bold=True)
    ws.print_area = f"B1:B{r}"
    _fit_width(ws, landscape=False)

    # ---- AI sheets ----------------------------------------------------------------------------
    head = PatternFill("solid", fgColor="1F3A5F")

    def table(title, headers, rows, widths):
        s = wb.create_sheet(title)
        s.append(headers)
        for c in s[1]:
            c.font, c.fill = Font(bold=True, color="FFFFFF"), head
        for row in rows:
            s.append(row)
        for n, w in enumerate(widths, 1):
            s.column_dimensions[openpyxl.utils.get_column_letter(n)].width = w
        for row in s.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        s.freeze_panes = "A2"
        _fit_width(s, landscape=True)
        return s

    to_map = mapping_items(chain, intake)
    table("AI Review", ["Item", "Value"], [
        ["Status", "AI DRAFT. Not a title opinion. A landman must check every item and sign off."],
        ["Run", str(run_id)], ["Tract", intake.tract_id],
        ["Package source", intake.mode + (f" ({intake.runsheet_path.name})" if intake.runsheet_path else "")],
        ["Tract acreage", f"{acres:g}" if acres else "unknown (add to tracts.csv)"],
        ["Remaining acreage", _remaining(chain.get("remaining_acres"))],
        ["Notice address (IOL/FOL)", (chain.get("tax") or {}).get("notice_address")
         or "NOT AVAILABLE: add the appraisal district printout (the deed address is not used)"],
        ["Taxes", _tax_line(chain.get("tax") or {})],
        ["Current surface owner", names(owners) or "NOT DETERMINED"],
        ["Vesting instrument", display_ref(vesting) if vesting else None],
        ["Flags", str(len(flags))],
        ["Pore space owner", (names(chain.get("pore_space", {}).get("owners")) or "NOT DETERMINED")
         + ("  (SEVERED from the surface)" if chain.get("pore_space", {}).get("severed") else "")],
        ["Pore space basis", chain.get("pore_space", {}).get("basis")],
        ["Mapping needed", f"{len(to_map)} item(s): see AI Mapping" if to_map else "none"],
        ["Ownership notes", "\n".join(m for _, m in chain.get("ownership_notes", [])) or "none"],
        ["Instruments with an unrecognized type", ", ".join(chain["unclassified"]) or "none"],
        ["Instruments with no filing date", ", ".join(chain["undated"]) or "none"],
        ["Rules not yet implemented", ", ".join(skipped) or "none"],
        ["Package problems", "\n".join(problems) or "none"],
        ["Red dates", "On the Index and Runsheet, a red file date is earlier than the effective date: check it."],
    ], [34, 100])
    by_id = {i["instrument_id"]: i for i in instruments}
    ms = table("AI Mapping", ["Instrument", "Filed", "Reason", "Acres", "Grantor", "Grantee", "Legal description",
                              "File", "Mapped by", "Date mapped", "Note"],
               [[display_ref(by_id[m["instrument_id"]]) if m["instrument_id"] in by_id else "(not in package)",
                 fmt_date(m["filing_date"]), "\n".join(m["reasons"]), m["acres"], names(m["grantors"]),
                 names(m["grantees"]), m["legal"], m["file_name"], None, None, None] for m in to_map]
               or [["Nothing to map", None, None, None, None, None, None, None, None, None, None]],
               [16, 11, 40, 8, 22, 22, 60, 30, 14, 12, 30])
    if to_map:
        ms.sheet_properties.tabColor = "7030A0"
    fs = table("AI Flags", ["Rule", "Severity", "Instrument", "Message", "What to do", "Reviewer action", "Note"],
               [[f.rule_id, f.severity, f.instrument_id, f.message, getattr(f, "action", None), None, None]
                for f in flags], [8, 12, 26, 80, 40, 16, 40])
    colors = {"high": "F8D7DA", "medium": "FFF2CC", "low": "E2EFDA", "review": "DDEBF7", "mapping": "E4D7F5",
              "note": "EDEDED"}
    for row in fs.iter_rows(min_row=2):
        color = colors.get(str(row[1].value or "").split()[0].lower() if row[1].value else "")
        if color:
            row[1].fill = PatternFill("solid", fgColor=color)
    liens = [i for i in instruments if i.get("category") in ("lien", "judgment")]
    table("AI Liens", ["Instrument", "Type", "Filed", "Holder", "Amount", "Rate", "Term", "Maturity",
                       "Est. months left", "Est. balance", "Release status", "Release", "Missing for estimate"],
          [_lien_row(i) for i in liens] or [["No liens in the package"] + [None] * 12],
          [16, 22, 11, 26, 14, 8, 10, 12, 10, 14, 16, 26, 22])
    esmts = [i for i in instruments if i.get("category") in ("easement", "surface agreement", "pore space agreement")
             or (i.get("category") != "release" and re.search(r"\blease|easement", i.get("type") or ""))]
    table("AI Easements & Leases", ["Instrument", "Type", "Filed", "Holder", "Exclusive", "Width", "Term", "Expires",
                                    "Abandonment", "Crossing limits", "Crossing control", "Assignment", "Preferential rights"],
          [_esmt_row(i) for i in esmts] or [["No easements or leases in the package"] + [None] * 12],
          [16, 22, 11, 24, 12, 14, 24, 14, 30, 30, 30, 26, 26])
    tx = chain.get("tax") or {}
    labels = {"cad_property_id": "CAD property ID", "cad_owner_of_record": "CAD owner of record",
              "cad_mailing_address": "CAD mailing address", "notice_address": "Notice address (IOL/FOL)",
              "taxes_current": "Taxes current", "last_tax_year_paid": "Last tax year paid",
              "last_amount_paid": "Last amount paid", "last_payment_date": "Last payment date",
              "delinquent_amount": "Delinquent amount", "exemptions_special_valuation": "Exemptions / special valuation",
              "tax_check_source": "Source checked"}
    table("AI Taxes & CAD", ["Item", "Value"],
          [[v, tx.get(k)] for k, v in labels.items()] + [["Printouts read", "\n".join(tx.get("files") or []) or
                                                          "none: ask the agent to add the CAD and tax printouts"]],
          [32, 90])
    ld = chain.get("legal_description") or {}
    ls = table("AI Legal Description", ["Item", "Value"], [
        ["Status", ld.get("status") or "Not generated"],
        ["Approved by (reviewer name)", None], ["Date approved", None],
        ["Vesting instrument", ld.get("vesting_instrument")],
        ["Save-and-excepts subtracted", "\n".join(ld.get("less_and_except_instruments") or []) or "none"],
        ["Remaining acreage", ld.get("remaining_acreage")],
        ["Preliminary description", ld.get("text") or "The vesting deed's description wasn't read"],
    ], [32, 110])
    yellow = PatternFill("solid", fgColor="FFF2CC")
    for r in (3, 4):
        ls.cell(r, 2).fill = yellow
    ls.cell(8, 2).alignment = Alignment(wrap_text=True, vertical="top")
    _all_fields_sheet(wb, instruments, head)
    table("AI Supporting Docs", ["Kind", "File"], [[i.get("kind"), i["file_name"]] for i in chain.get("supporting", [])],
          [30, 80])

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.active = 0
    wb.save(path)
    return path


def _remaining(r):
    if not r:
        return "unknown"
    outs = " - ".join(f"{a:g}" for a in r["out_sales"])
    return f"{r['remaining']:g} ac" + (f" ({r['base']:g} - {outs} out-sold; preliminary)" if r["out_sales"] else "")


def _tax_line(tx):
    if not tx.get("files"):
        return "no appraisal district or tax printout in the package"
    if tx.get("delinquent"):
        return f"DELINQUENT {tx.get('delinquent_amount') or ''}".strip()
    return f"current: {tx.get('taxes_current') or 'not shown'}; last year paid {tx.get('last_tax_year_paid') or '?'}"


def _v(i, name):
    return (i.get("values") or {}).get(name)


def _lien_row(i):
    c = (i.get("calc") or {}).get("lien") or {}
    rel = (i.get("calc") or {}).get("release") or {}
    return [display_ref(i), i.get("type"), fmt_date(i.get("filing_date")), ", ".join(i.get("grantees") or []) or None,
            c.get("amount"), f"{c['rate'] * 100:g}%" if c.get("rate") is not None else _v(i, "interest_rate"),
            f"{c['term_months'] / 12:g} yr" if c.get("term_months") else _v(i, "lien_term"),
            (c.get("maturity") or "") + (" (calc.)" if c.get("maturity_computed") else "") or None,
            c.get("months_left"), c.get("balance"), rel.get("status"), rel.get("reference"),
            ", ".join(c.get("missing") or []) or None]


def _esmt_row(i):
    c = i.get("calc") or {}
    e, a = c.get("expiration") or {}, c.get("abandonment") or {}
    exp = e.get("date") or ("perpetual" if e.get("how") == "perpetual" else None)
    ab = "; ".join(x for x in ((f"{a['months']:g} mo" if a.get("months") else None), a.get("trigger"), a.get("effect")) if x)
    return [display_ref(i), i.get("type"), fmt_date(i.get("filing_date")), ", ".join(i.get("grantees") or []) or None,
            _v(i, "easement_exclusive"), _v(i, "easement_width"), _v(i, "term") or i.get("term"),
            (exp + (" (calc.)" if e.get("how") == "calculated" else "")) if exp else None, ab or None,
            _v(i, "crossing_restrictions"), _v(i, "crossing_control"), _v(i, "assignment_restrictions"),
            _v(i, "preferential_rights")]


def _all_fields_sheet(wb, instruments, head):
    """One row per instrument, one column per field the AI read. New fields added to the workbook show up here
    on the next run with no code change."""
    names = []
    for i in instruments:
        for k in (i.get("values") or {}):
            if k not in names:
                names.append(k)
    s = wb.create_sheet("AI All Fields")
    s.append(["Instrument", "File"] + names)
    for c in s[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), head
    for i in instruments:
        vals = i.get("values") or {}
        s.append([display_ref(i), i.get("file_name")] + [(str(vals.get(n))[:32000] if vals.get(n) is not None else None)
                                                         for n in names])
    for n in range(1, len(names) + 3):
        s.column_dimensions[openpyxl.utils.get_column_letter(n)].width = 18 if n > 2 else 22
    for row in s.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    s.freeze_panes = "C2"
