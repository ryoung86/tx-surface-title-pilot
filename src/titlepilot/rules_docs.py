"""Checks added from the broker's Fields and Rules sheet (2026-11-05): document defects (DR), liens (LN),
easement and lease terms (TS08-TS13, EA01-EA05). They read the fields in each instrument's record; figures
worked out in code (maturity, balance, expiration, closure) come from chain.instrument_calc.

Each check stays quiet when the field it needs is empty, so a document the model wasn't asked about (e.g.
lien fields on a deed) never raises a flag.
"""
import re
from datetime import date

from .calc import add_months
from .chain import classify
from .fuzzy import token_sort_ratio
from .rules import NEGATIVE, rule, same_person
from .validate import as_text, norm_date, norm_ref, norm_type, val

# --------------------------------------------------------------------------------------------- helpers

EMPTY = re.compile(
    r"^\s*(?:none\b.*|n/?a|no|unknown|silent|nothing\b.*|not\s+(?:stated|found|applicable|shown|present|mentioned)\b.*|"
    r"there\s+(?:is|are)\s+no\b.*|no\s+(?:\w+\s+){0,3}(?:language|clauses?|provisions?|restrictions?|rights?|terms?|"
    r"mention|stated|found|references?|alterations?|changes?|issues?|conflicts?)\b[^.]*)\s*\.?\s*$", re.I)


def empty(v) -> bool:
    """'None', 'N/A', 'Not stated', 'No assignment restrictions' mean empty; 'No crossing without consent' doesn't."""
    return not str(v or "").strip() or bool(EMPTY.match(str(v)))


def text(i, name):
    v = as_text(val(i.get("record") or {}, name))
    return None if empty(v) else v


def items(i, name):
    v = val(i.get("record") or {}, name)
    if isinstance(v, str):
        v = [v]
    return [str(x) for x in (v or []) if not empty(x)]


def parts(item):
    return [p.strip() for p in str(item).split("|")]


def short(s, n=160):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def is_easement_or_lease(i):
    t = i["type"] or ""
    return i["category"] in ("easement", "surface agreement", "pore space agreement") or (
        i["category"] != "release" and bool(re.search(r"\blease|easement", t)))


def is_easement(i):
    t = i["type"] or ""
    return i["category"] == "easement" or ("easement" in t and not re.search(r"\blease", t) and i["category"] != "release")


ENTITY = re.compile(r"\b(llc|l\.l\.c|inc|corp|corporation|company|co\.|ltd|l\.p|lp|llp|trust|bank|partnership|"
                    r"association|church|county|city|state of|independent school|isd|estate of|authority|district|"
                    r"cooperative|co-op|fund|partners|properties|ranch(?:es)?\b.*\b(?:llc|inc|ltd|lp)|pipeline|"
                    r"energy|oil|gas|operating|resources|holdings|investments|federal|united states)\b", re.I)


def is_entity(name):
    return bool(ENTITY.search(str(name or "")))


def parties(i, side="grantors"):
    v = val(i.get("record") or {}, side) or []
    return [p for p in v if isinstance(p, dict) and (p.get("name") or "").strip()]


def signers(i):
    """(name, capacity, signed?) for each signature item."""
    out = []
    for s in items(i, "signatures"):
        p = parts(s)
        flag = " ".join(p[2:]).lower() if len(p) > 2 else s.lower()
        unsigned = bool(re.search(r"not\s+signed|unsigned|no\s+signature|blank|did\s+not\s+sign", flag))
        out.append((p[0], p[1] if len(p) > 1 else "", not unsigned))
    return out


def acknowledgments(i):
    return [parts(a) for a in items(i, "acknowledgment")]


SIGNED_TYPES = ("conveyance", "lien", "easement", "surface agreement", "release", "pore space", "pore space agreement")

# ------------------------------------------------------------------------------------------ DR: defects


@rule("DR01")
def unsigned(ctx, params):
    for i in ctx.instruments:
        sig = signers(i)
        if not sig:
            continue
        missing = [n for n, _, ok in sig if not ok]
        if missing:
            yield i["instrument_id"], f"Not signed by: {', '.join(missing)}"
        names = [n for n, _, ok in sig if ok]
        for p in parties(i):
            g = p["name"]
            if is_entity(g):        # an entity signs through an officer, whose name won't match
                continue
            if not any(same_person(g, n) or token_sort_ratio(g.lower(), n.lower()) >= 70 for n in names) \
                    and g not in missing:
                yield i["instrument_id"], f"'{g}' is named as a grantor but no signature for them was found"


DATE_IN = re.compile(r"(?:commission\s+expires?|expires?|exp\.?)\s*:?\s*(.+)", re.I)


@rule("DR02")
def acknowledgment_defects(ctx, params):
    for i in ctx.instruments:
        acks = acknowledgments(i)
        raw = items(i, "acknowledgment")
        if raw and len(raw) == 1 and raw[0].strip().lower() in ("none", "no acknowledgment"):
            acks = []
        if not acks:
            if raw or (i["category"] in SIGNED_TYPES and signers(i)):
                yield i["instrument_id"], "No acknowledgment found: an unacknowledged instrument may not give notice"
            continue
        sig_names = [n for n, _, _ in signers(i)]
        for a in acks:
            name, problems = a[0], []
            joined = " | ".join(a).lower()
            if re.search(r"seal\s*:\s*no", joined):
                problems.append("no notary seal")
            venue = next((p for p in a if p.lower().startswith("venue")), a[2] if len(a) > 2 else "")
            if len(a) > 2 and (not re.sub(r"(?i)venue\s*:?", "", venue).strip()
                               or re.search(r"(?i)venue\s*:?\s*(blank|unknown|$)", venue)):
                problems.append("venue blank")
            exp = next((norm_date(m.group(1)) for p in a for m in [DATE_IN.search(p)] if m), None)
            ack_date = norm_date(a[-1]) if len(a) >= 7 else None
            if exp and ack_date and exp < ack_date:
                problems.append(f"notary commission expired {exp}, before the acknowledgment date {ack_date}")
            # a near-miss spelling is DR06's; here only someone who isn't a signer at all
            if sig_names and name and name.lower() != "unknown" and not is_entity(name) and \
                    not any(same_person(name, s) or token_sort_ratio(name.lower(), s.lower()) >= 60 for s in sig_names):
                problems.append(f"acknowledged by '{name}', who is not a signer")
            if problems:
                yield i["instrument_id"], f"Acknowledgment of {name or 'a signer'}: {'; '.join(problems)}"


PAGE_OF = re.compile(r"page\s+(\d+)\s+of\s+(\d+)", re.I)
MISSING = re.compile(r"missing|not\s+attached|no\s+exhibit|cut\s+off|incomplete|not\s+included|skipped|gap|out\s+of\s+order", re.I)


@rule("DR04")
def missing_pages(ctx, params):
    for i in ctx.instruments:
        t = text(i, "page_completeness")
        if t and MISSING.search(t) and not re.search(r"\bno\s+(?:pages?\s+)?missing|none\s+missing|all\s+(?:pages|exhibits)\s+(?:present|attached)", t, re.I):
            yield i["instrument_id"], f"Pages or exhibits may be missing: {short(t)}"
            continue
        totals = [int(b) for a, b in PAGE_OF.findall(t or "")]
        if totals and i.get("pages") and max(totals) > i["pages"]:
            yield i["instrument_id"], f"Says 'page … of {max(totals)}' but the file has {i['pages']} page(s)"


@rule("DR05")
def alterations(ctx, params):
    for i in ctx.instruments:
        t = text(i, "alterations")
        if not t:
            continue
        initialed = re.search(r"\binitial(?:ed|led)\b", t, re.I) and not re.search(r"not\s+initial|no\s+initial|uninitial", t, re.I)
        if not initialed:
            yield i["instrument_id"], f"Handwritten change, strikeout or blank without initials: {short(t)}"


@rule("DR06")
def grantor_name_mismatch(ctx, params):
    for i in ctx.instruments:
        sources = (("signed", [n for n, _, _ in signers(i)]),
                   ("acknowledged", [a[0] for a in acknowledgments(i) if a and a[0].lower() != "unknown"]))
        for p in parties(i):
            g = p["name"]
            if is_entity(g):
                continue
            for how, names in sources:
                if not names or any(same_person(g, n) for n in names):
                    continue
                best = max(names, key=lambda n: token_sort_ratio(g.lower(), n.lower()))
                if 60 <= token_sort_ratio(g.lower(), best.lower()) < 92:
                    yield i["instrument_id"], f"Granting clause names '{g}' but it was {how} as '{best}'"


CAPACITY = re.compile(r"trustee|executor|executrix|administrat|attorney[- ]in[- ]fact|agent|guardian|receiver|"
                      r"president|vice|officer|manager|member|partner|secretary|director|heir|independent", re.I)


@rule("DR07")
def capacity_mismatch(ctx, params):
    for i in ctx.instruments:
        sig = signers(i) + [(a[0], a[1] if len(a) > 1 else "", True) for a in acknowledgments(i)]
        if not sig:
            continue
        for p in parties(i):
            cap = (p.get("capacity") or "").strip()
            m = CAPACITY.search(cap)
            if not m:
                continue
            word = m.group(0).lower()
            mine = [c for n, c, _ in sig if same_person(p["name"], n) or token_sort_ratio(p["name"].lower(), n.lower()) >= 70]
            if mine and not any(word in c.lower() for c in mine):
                yield i["instrument_id"], (f"'{p['name']}' is recited as {cap} but signed or acknowledged as "
                                           f"{', '.join(c or 'individual' for c in mine)}")


@rule("DR08")
def marital_status_missing(ctx, params):
    for i in ctx.instruments:
        if i["category"] not in ("conveyance", "lien"):
            continue
        who = [p["name"] for p in parties(i) if not is_entity(p["name"]) and not (p.get("marital_status") or "").strip()
               and not CAPACITY.search(p.get("capacity") or "")]
        if who:
            yield i["instrument_id"], f"Marital status not stated for {', '.join(who)}: check homestead and spousal joinder"


VAGUE = re.compile(r"\bmy\s+(?:land|property|farm|place|ranch|home)|home\s*place|homestead\s+of|all\s+(?:of\s+)?(?:the\s+)?"
                   r"(?:land|lands|property)\s+(?:i|we)\s+own|all\s+(?:of\s+)?my\b|unrecorded\s+(?:survey|plat|map)|"
                   r"blanket|across\s+(?:the|my|our|said)\s+(?:land|lands|property|premises)|as\s+(?:now\s+)?"
                   r"(?:located|constructed|laid)|route\s+to\s+be\s+(?:selected|determined)", re.I)
LOCATORS = re.compile(r"abstract|\bA-\s?\d|survey|section|\bsec\.|\blot\b|block|subdivision|addition|plat|"
                      r"metes|bearing|\bN\s?\d|\bS\s?\d|exhibit|centerline|center\s+line|beginning", re.I)


@rule("DR09")
def unlocatable_description(ctx, params):
    """Also covers TS01, blanket easements."""
    for i in ctx.instruments:
        if i["category"] not in ("conveyance", "easement", "surface agreement", "lien", "pore space", "pore space agreement"):
            continue
        legal = i.get("legal") or ""
        refs = [r for r in items(i, "description_references") if re.search(r"not\s+(?:recorded|attached)", r, re.I)]
        if not legal.strip():
            if i["category"] in ("conveyance", "easement"):
                yield i["instrument_id"], "No legal description was found"
            continue
        m = VAGUE.search(legal)
        if m:
            kind = "Blanket or unlocated easement" if is_easement(i) else "Description can't be located on its own"
            yield i["instrument_id"], f"{kind}: '{short(legal[max(0, m.start() - 60): m.end() + 60], 200)}'"
        elif refs:
            yield i["instrument_id"], f"Description relies on something not in the record: {'; '.join(short(r, 80) for r in refs)}"
        elif not LOCATORS.search(legal):
            yield i["instrument_id"], f"Description names no survey, abstract, lot, plat or calls: '{short(legal)}'"


@rule("DR10")
def calls_dont_close(ctx, params):
    precision = params.get("min_precision", 2500)       # perimeter / misclosure, e.g. 1:2500
    tolerance = params.get("acreage_tolerance", 0.02)   # 2% of the stated acreage
    for i in ctx.instruments:
        c = (i.get("calc") or {}).get("closure")
        if not c:
            continue
        note = f" ({len(c['unread'])} call(s) not checked: curves or unreadable)" if c["unread"] else ""
        if c["unread"]:
            # a curve leaves a gap, so misclosure means nothing; still compare the area if no curves
            if any(u.upper().startswith("CURVE") for u in c["unread"]):
                continue
        if c["precision"] is not None and c["precision"] < precision and c["misclosure_ft"] > 1:
            yield i["instrument_id"], (f"Metes and bounds don't close: off by {c['misclosure_ft']:g} ft over "
                                       f"{c['perimeter_ft']:g} ft (1:{c['precision']}){note}. Check the calls against the image")
        stated = i.get("acreage")
        if stated and c["acres"] and abs(c["acres"] - stated) > tolerance * stated and \
                (c["precision"] is None or c["precision"] >= precision):
            yield i["instrument_id"], (f"Calls enclose {c['acres']:g} ac but the instrument states {stated:g} ac{note}")


def _target(ctx, i):
    keys = set(i["refers_to"])
    return next((o for o in ctx.instruments if o is not i and o["ref_keys"] & keys), None)


@rule("DR11")
def correction_differs(ctx, params):
    for i in ctx.instruments:
        t = i["type"] or ""
        partial = "partial release" in t
        if not ("correct" in t or "ratif" in t or partial):
            continue
        o = _target(ctx, i)
        if not o:
            continue
        a, b = i.get("acreage"), o.get("acreage")
        if partial:
            if a and b and a > b:
                yield i["instrument_id"], f"Partial release covers {a:g} ac, more than the {b:g} ac of {o['instrument_id']}"
            continue
        if a and b and abs(a - b) > 0.01 * b:
            yield i["instrument_id"], f"Corrects {o['instrument_id']} but states {a:g} ac against {b:g} ac: confirm which is right"
        elif i.get("legal") and o.get("legal") and token_sort_ratio(i["legal"].lower(), o["legal"].lower()) < 60:
            yield i["instrument_id"], f"Description differs from {o['instrument_id']}, which it corrects or ratifies: compare the two"


@rule("DR12")
def clause_conflict(ctx, params):
    for i in ctx.instruments:
        t = text(i, "clause_conflicts")
        if t:
            yield i["instrument_id"], f"Granting, habendum or reservation clauses conflict: {short(t, 220)}"


AMBIGUOUS = re.compile(r"prior\s+reservations?|reservations?\s+of\s+record|(?:less\s+and\s+except|save\s+and\s+except|subject\s+to)\s+"
                       r"(?:all\s+)?(?:prior|previous|existing|any|those)\b|(?:heretofore|previously)\s+(?:reserved|conveyed|excepted)|"
                       r"of\s+record\b|if\s+any", re.I)
FLOATING = re.compile(r"(?:\d+\s*/\s*\d+|one[- ]half|one[- ]fourth|one[- ]eighth|one[- ]sixteenth)\s+of\s+(?:the\s+)?"
                      r"(?:usual\s+)?(?:\d+\s*/\s*\d+|one[- ]eighth)?\s*(?:royalty|royalties)", re.I)


@rule("DR13")
def ambiguous_reservation(ctx, params):
    for i in ctx.instruments:
        if i["category"] != "conveyance":
            continue
        t = " | ".join(x for x in (text(i, "habendum_and_reservations_text"), i.get("mineral_exception"),
                                   text(i, "less_and_except")) if x)
        m = AMBIGUOUS.search(t)
        if m:
            yield i["instrument_id"], f"General or ambiguous reservation: '{short(t[max(0, m.start() - 80): m.end() + 80], 220)}'"
        for f in items(i, "reserved_fractions") + ([t] if t else []):
            m = FLOATING.search(f)
            if m:
                yield i["instrument_id"], f"Royalty fraction '{m.group(0)}': fixed or floating? Needs review"
                break


@rule("DR14")
def duhig(ctx, params):
    """Grantor reserves an interest while its own title came subject to an earlier reservation."""
    convs = ctx.chain.get("conveyances", [])
    by_id = {i["instrument_id"]: i for i in ctx.instruments}
    for n, c in enumerate(convs):
        i = by_id.get(c["instrument_id"])
        if not i:
            continue
        reserves = items(i, "reserved_fractions") or ([i["mineral_exception"]] if i.get("mineral_exception")
                                                        and re.search(r"reserv|retain", i["mineral_exception"], re.I) else [])
        if not reserves:
            continue
        for prior in convs[:n]:
            got = any(same_person(g, p) for g in c["grantors"] for p in prior["grantees"])
            if got and prior.get("mineral_exception") and not NEGATIVE.match(prior["mineral_exception"]):
                yield i["instrument_id"], (f"Grantor reserves '{short(reserves[0], 80)}' but took title under "
                                           f"{prior['instrument_id']}, which already excepted '{short(prior['mineral_exception'], 80)}'. "
                                           "Possible Duhig problem: trace the grantor's interest")
                break


LIEN_WORD = re.compile(r"vendor'?s\s+lien|deed\s+of\s+trust|mortgage|note\s+of\s+even\s+date|purchase\s+money", re.I)


@rule("DR15")
def recited_lien_missing(ctx, params):
    liens = [o for o in ctx.instruments if o["category"] == "lien"]
    for i in ctx.instruments:
        if i["category"] != "conveyance":
            continue
        recited = [r for r in items(i, "recited_liens") if LIEN_WORD.search(r) or "|" in r]
        cons = text(i, "consideration")
        if not recited and cons and LIEN_WORD.search(cons):
            recited = [cons]
        if not recited:
            continue
        d = i.get("filing_date") or i.get("execution_date")
        near = [o for o in liens if d and (o.get("filing_date") or "") and abs(
            (date.fromisoformat(o["filing_date"]) - date.fromisoformat(d)).days) <= 120
            and any(same_person(g, p) for g in o["grantors"] for p in i["grantees"])]
        if not near:
            yield i["instrument_id"], (f"Recites a lien ({short(recited[0], 120)}) but no matching deed of trust or lien "
                                       "is in the package: pull it and check for a release")


@rule("DR16")
def release_mismatch(ctx, params):
    from .calc import money
    for i in ctx.instruments:
        if i["category"] != "release":
            continue
        rel = text(i, "released_lien")
        o = _target(ctx, i)
        if not rel or not o:
            continue
        problems = []
        holders = o["grantees"] if o["category"] == "lien" else o["grantors"]
        if holders and not any(token_sort_ratio(h.lower(), rel.lower()) >= 60 or h.lower() in rel.lower() for h in holders):
            # the holder named in the release text should appear somewhere in it
            if not any(same_person(h, g) for h in holders for g in i["grantors"]):
                problems.append(f"lien holder of record is {', '.join(holders)}")
        a = money(rel)
        b = money((o.get("values") or {}).get("lien_amount") or (o.get("values") or {}).get("consideration"))
        if a and b and abs(a - b) > 1:
            problems.append(f"amount ${a:,.2f} vs ${b:,.2f}")
        rd, od = norm_date(rel), o.get("execution_date") or o.get("filing_date")
        if rd and od and rd not in (o.get("execution_date"), o.get("filing_date")):
            problems.append(f"date {rd} vs {od}")
        if problems:
            yield i["instrument_id"], f"Release doesn't match {o['instrument_id']}: {'; '.join(problems)}"


@rule("DR17")
def note_of_even_date(ctx, params):
    pat = re.compile(r"note\s+of\s+even\s+date|assum(?:e|es|ed|ption)\s+(?:and\s+agree|of|the)|assumed\s+debt|subject\s+to\s+(?:a|the)\s+(?:note|deed\s+of\s+trust)", re.I)
    for i in ctx.instruments:
        if i["category"] != "conveyance":
            continue
        t = " | ".join(items(i, "recited_liens") + [text(i, "consideration") or ""])
        m = pat.search(t)
        if m:
            yield i["instrument_id"], f"Consideration recites '{m.group(0)}': check for the lien and its release"


# Runsheet abbreviations -> category, for the type cross-check.
RUNSHEET_TYPES = {
    "wd": "conveyance", "gwd": "conveyance", "swd": "conveyance", "wdvl": "conveyance", "qcd": "conveyance",
    "qc": "conveyance", "dwow": "conveyance", "deed": "conveyance", "cd": "conveyance", "exd": "conveyance",
    "td": "conveyance", "gd": "conveyance", "aoh": "conveyance", "aff heir": "conveyance", "prob": "conveyance",
    "dt": "lien", "dot": "lien", "mtg": "lien", "vl": "lien", "ml": "lien", "mml": "lien", "ucc": "lien", "fs": "lien",
    "ftl": "lien", "stl": "lien", "aj": "judgment", "abj": "judgment", "jdgmt": "judgment", "lp": "judgment",
    "rel": "release", "rls": "release", "prel": "release", "rdt": "release",
    "esmt": "easement", "ease": "easement", "row": "easement", "r/w": "easement", "pl esmt": "easement",
    "sl": "surface agreement", "sua": "surface agreement", "rest": "restriction", "rc": "restriction",
}


def runsheet_category(t):
    t = norm_type(t)
    if not t:
        return None
    key = re.sub(r"[.\s]+", " ", t).strip()
    if key in RUNSHEET_TYPES:
        return RUNSHEET_TYPES[key]
    if key.replace(" ", "") in RUNSHEET_TYPES:
        return RUNSHEET_TYPES[key.replace(" ", "")]
    c = classify(t)
    return None if c in ("other", "unknown") else c


@rule("DR19")
def type_mismatch(ctx, params):
    from .chain import EASEMENT_WORDS, conveys_fee
    by_ref = {norm_ref(e.get("recording_ref")): e for e in (getattr(ctx.intake, "runsheet_entries", None) or [])
              if e.get("recording_ref") and e.get("_kind") != "name"}
    for i in ctx.instruments:
        op = i.get("operative") or ""
        if is_easement(i) and conveys_fee(i):
            yield i["instrument_id"], f"Titled '{i['type']}' but the operative words convey fee: '{short(op, 100)}'"
        elif i["category"] == "conveyance" and EASEMENT_WORDS.search(op) and not FEE_ONLY.search(op):
            yield i["instrument_id"], f"Titled '{i['type']}' but the operative words grant an easement: '{short(op, 100)}'"
        elif i["category"] == "conveyance" and re.search(r"\blease|let\b|demise", op, re.I):
            yield i["instrument_id"], f"Titled '{i['type']}' but the operative words lease: '{short(op, 100)}'"
        e = next((by_ref[k] for k in i["ref_keys"] if k in by_ref), None)
        rc = runsheet_category(e.get("instrument_type")) if e else None
        if rc and i["category"] not in ("other", "unknown") and rc != i["category"] and \
                {rc, i["category"]} not in ({"lien", "judgment"}, {"easement", "surface agreement"}):
            yield i["instrument_id"], (f"The AI read this as '{i['type']}' ({i['category']}) but the agent's runsheet "
                                       f"lists it as '{e.get('instrument_type')}' ({rc}): confirm the type")
        elif i.get("type_conf") == "low" or (i["category"] in ("other", "unknown") and not i["type"]):
            yield i["instrument_id"], (f"Instrument type is unclear ('{i['type'] or 'none'}'"
                                       + (f", runsheet says '{e.get('instrument_type')}'" if e else "") + "): confirm it")


FEE_ONLY = re.compile(r"grant,?\s+(?:bargain,?\s+)?sell\s+and\s+convey", re.I)

# --------------------------------------------------------------------------------------------- liens


def _lien_line(i):
    c = (i.get("calc") or {}).get("lien") or {}
    bits = []
    if c.get("amount"):
        bits.append(f"${c['amount']:,.2f}")
    if c.get("rate") is not None:
        bits.append(f"{c['rate'] * 100:g}%")
    if c.get("term_months"):
        bits.append(f"{c['term_months'] / 12:g} yr")
    if c.get("maturity"):
        bits.append(f"matures {c['maturity']}" + (" (calculated)" if c.get("maturity_computed") else ""))
    return c, ", ".join(bits)


def _released(i):
    return ((i.get("calc") or {}).get("release") or {}).get("status") == "released"


@rule("LN01")
def lien_open(ctx, params):
    for i in ctx.instruments:
        if i["category"] != "lien" or _released(i):
            continue
        c, line = _lien_line(i)
        if c.get("matured"):
            continue
        holder = ", ".join(i["grantees"]) or "?"
        bal = f"; estimated balance ${c['balance']:,.0f} (estimate, assumes payments made as scheduled)" \
            if c.get("balance") is not None else ""
        when = "" if c.get("maturity") else "; maturity unknown"
        yield i["instrument_id"], (f"{i['type'].capitalize()} to {holder} has no release in the package and has not "
                                   f"matured{when}" + (f" ({line})" if line else "") + bal)


@rule("LN02")
def lien_matured(ctx, params):
    for i in ctx.instruments:
        if i["category"] != "lien" or _released(i):
            continue
        c, line = _lien_line(i)
        if c.get("matured"):
            yield i["instrument_id"], (f"{i['type'].capitalize()} to {', '.join(i['grantees']) or '?'} matured "
                                       f"{c['maturity']} with no release in the package ({line}): request a release")


@rule("LN03")
def lien_figures_missing(ctx, params):
    for i in ctx.instruments:
        if i["category"] != "lien" or _released(i) or "ucc" in (i["type"] or "") or "financing" in (i["type"] or ""):
            continue
        c = (i.get("calc") or {}).get("lien") or {}
        missing = c.get("missing") or []
        if missing:
            yield i["instrument_id"], f"Balance can't be estimated: {', '.join(missing)} not found or unreadable"


# ------------------------------------------------------------------------------------ easements and leases


def _field_flag(rule_id, field, message, test=is_easement_or_lease):
    @rule(rule_id)
    def check(ctx, params):
        for i in ctx.instruments:
            if test and not test(i):
                continue
            t = text(i, field)
            if t:
                yield i["instrument_id"], f"{message}: '{short(t, 200)}'"
    return check


@rule("TS08")
def exclusive_rights(ctx, params):
    for i in ctx.instruments:
        if not is_easement_or_lease(i):
            continue
        ex = (text(i, "easement_exclusive") or "").lower()
        lang = (i.get("record") or {}).get("easement_exclusive", {})
        lang = lang.get("source_text") if isinstance(lang, dict) else ""
        if ex.startswith("exclusive"):
            yield i["instrument_id"], f"Grants exclusive surface rights: '{short(lang or ex, 200)}'"


_field_flag("TS10", "assignment_restrictions", "Assignment restricted or needs consent", None)
_field_flag("TS11", "preferential_rights", "Right of first refusal, option or preferential right", None)


@rule("TS12")
def no_access(ctx, params):
    landlocked = re.compile(r"landlock|no\s+(?:legal\s+)?access|no\s+(?:road\s+)?frontage|without\s+access|not\s+(?:adjoin|abut)", re.I)
    access = [i for i in ctx.instruments if is_easement(i) and re.search(
        r"access|ingress|egress|road|roadway", " ".join([i["type"] or "", text(i, "access") or "", i.get("easements") or ""]), re.I)]
    for i in ctx.instruments:
        t = text(i, "access")
        if t and landlocked.search(t) and not access:
            yield i["instrument_id"], f"Recites no road frontage or access, and no access easement is in the package: '{short(t)}'"


@rule("TS13")
def accommodation(ctx, params):
    for i in ctx.instruments:
        t = text(i, "accommodation_terms")
        typ = i["type"] or ""
        if t:
            yield i["instrument_id"], f"Surface use or accommodation terms tied to a mineral lessee: '{short(t, 200)}'"
        elif i["category"] == "surface agreement" and re.search(r"accommodation|surface\s+use|damage", typ):
            yield i["instrument_id"], f"{typ.capitalize()} ties the surface to a mineral lessee or operator: read its terms"


_field_flag("EA01", "crossing_restrictions", "Restricts crossings or encroachments")
_field_flag("EA02", "crossing_control", "Holder controls crossings")


@rule("EA03")
def no_width(ctx, params):
    for i in ctx.instruments:
        if is_easement(i) and not text(i, "easement_width") and not re.search(
                r"\d+\s*(?:feet|foot|ft\b|'|rods?\b|varas?\b)|width", i.get("easements") or "", re.I) and not re.search(
                r"\d+\s*(?:feet|foot|ft|'|rods?|varas?)\s*(?:wide|in\s+width)", i.get("legal") or "", re.I):
            yield i["instrument_id"], "No width stated for the easement"


@rule("EA04")
def expired_or_expiring(ctx, params):
    """Also covers TS09: an expired term with no release."""
    years = params.get("years", 5)
    today = date.today()
    soon = add_months(today, years * 12).isoformat()
    released = {r for o in ctx.instruments if o["category"] == "release" for r in o["refers_to"]}
    for i in ctx.instruments:
        if not is_easement_or_lease(i):
            continue
        e = (i.get("calc") or {}).get("expiration") or {}
        d = e.get("date")
        if not d:
            continue
        how = " (calculated from the term)" if e.get("how") == "calculated" else ""
        rel = " A release is in the package." if i["ref_keys"] & released else " No release found."
        if d <= today.isoformat():
            yield i["instrument_id"], f"Term expired {d}{how}.{rel} Confirm it lapsed and check renewal rights"
        elif d <= soon:
            yield i["instrument_id"], f"Term expires {d}{how}, within {years} years. Check renewal or extension rights"


@rule("EA05")
def abandonment_clause(ctx, params):
    """Also covers TS09: an abandonment or nonuse clause with no release."""
    released = {r for o in ctx.instruments if o["category"] == "release" for r in o["refers_to"]}
    for i in ctx.instruments:
        a = (i.get("calc") or {}).get("abandonment")
        if not a or not is_easement_or_lease(i):
            continue
        months = f"{a['months']:g} months of " if a.get("months") else ""
        what = a.get("trigger") or "nonuse"
        effect = f"; effect: {short(a['effect'], 100)}" if a.get("effect") else ""
        rel = " A release is in the package." if i["ref_keys"] & released else " No release found."
        yield i["instrument_id"], f"Abandonment provision: {months}{short(what, 100)}{effect}.{rel} Confirm whether it is still in use"


# ---------------------------------------------------------------------------------- taxes, address, legal


def _owner_matches(owners, cad_text):
    words = set(re.findall(r"[a-z0-9]+", str(cad_text or "").lower()))
    for o in owners:
        ow = {w for w in re.findall(r"[a-z0-9]+", o.lower()) if w not in ("llc", "inc", "ltd", "lp", "the", "and", "et", "al", "ux")}
        if ow and len(ow & words) >= max(1, min(2, len(ow))):
            return True
    return False


@rule("TX01")
def taxes(ctx, params):
    """Severity: High if delinquent, else Note (Review when the printout is missing or the CAD owner differs)."""
    tx = ctx.chain.get("tax") or {}
    if not tx.get("files"):
        yield None, "No appraisal district or tax printout in the package: ask the agent to add them", "Review"
        return
    src = f" (source: {tx['tax_check_source']})" if tx.get("tax_check_source") else ""
    if tx.get("delinquent"):
        owed = f" ${number_text(tx.get('delinquent_amount'))} due" if tx.get("delinquent_amount") else ""
        yield None, f"Taxes delinquent{owed}; last year paid {tx.get('last_tax_year_paid') or '?'}{src}", "High"
    else:
        paid = ", ".join(x for x in (
            f"last year paid {tx['last_tax_year_paid']}" if tx.get("last_tax_year_paid") else None,
            f"last paid ${number_text(tx['last_amount_paid'])}" if tx.get("last_amount_paid") else None,
            f"on {tx['last_payment_date']}" if tx.get("last_payment_date") else None) if x)
        state = "Taxes current" if (tx.get("taxes_current") or "").lower().startswith("yes") else "Tax status not shown"
        yield None, f"{state}{': ' + paid if paid else ''}; CAD ID {tx.get('cad_property_id') or '?'}{src}", "Note"
    owners = ctx.chain.get("current_owner") or []
    if tx.get("cad_owner_of_record") and owners and not _owner_matches(owners, tx["cad_owner_of_record"]):
        yield None, (f"Appraisal district owner '{short(tx['cad_owner_of_record'], 100)}' differs from the record owner "
                     f"{', '.join(owners)}: check for an unrecorded conveyance"), "Review"
    ex = tx.get("exemptions_special_valuation") or ""
    if re.search(r"\bag\b|agricult|1-d-1|1d1|open[- ]space|wildlife|timber", ex, re.I):
        yield None, f"Special valuation ({short(ex, 80)}): a change of use can trigger rollback taxes", "Note"


def number_text(v):
    from .validate import number
    n = number(v)
    return f"{n:,.2f}" if n is not None else str(v)


def _addr_key(a):
    a = str(a or "").lower()
    for k, v in (("street", "st"), ("road", "rd"), ("avenue", "ave"), ("drive", "dr"), ("lane", "ln"),
                 ("highway", "hwy"), ("post office box", "po box"), ("p.o. box", "po box"), ("p o box", "po box")):
        a = a.replace(k, v)
    return " ".join(re.findall(r"[a-z0-9]+", a))


@rule("AD01")
def address_differs(ctx, params):
    tx = ctx.chain.get("tax") or {}
    cad = tx.get("cad_mailing_address")
    vest = next((i for i in ctx.instruments if i["instrument_id"] == ctx.chain.get("vesting_instrument")), None)
    deed = text(vest, "grantee_mailing_address") if vest else None
    if cad and deed and token_sort_ratio(_addr_key(cad), _addr_key(deed)) < 85:
        yield vest["instrument_id"], (f"Deed mailing address '{short(deed, 90)}' differs from the appraisal district's "
                                      f"'{short(cad, 90)}'. The appraisal district address is used for IOL/FOL")


@rule("LD01")
def legal_preliminary(ctx, params):
    ld = ctx.chain.get("legal_description")
    if not ld:
        yield None, "No legal description generated: the vesting deed's description wasn't read", "Review"
        return
    n = len(ld["less_and_except_instruments"])
    acres = f"{ld['remaining_acreage']:g} ac remaining" if ld.get("remaining_acreage") is not None else "acreage unknown"
    yield ld["vesting_instrument"], (f"Generated legal description is PRELIMINARY ({acres}, {n} save-and-except(s) "
                                     "subtracted). Check and approve or correct it on the AI Legal Description sheet")
