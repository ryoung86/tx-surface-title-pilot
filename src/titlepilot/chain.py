"""Order instruments, find the current surface owner, and mark encumbrances open or released.

Instrument types are classified by keyword, not exact name, because the model
writes them in many ways ("Easement / ROW", "easement/row", "Pipeline
Right-of-Way Easement"). Adjust the keywords as real documents come in.
"""
import re

from .fuzzy import token_sort_ratio

ENCUMBRANCE_CATEGORIES = {"lien", "judgment", "easement", "restriction", "surface agreement", "pore space agreement"}

CONVEYANCE_KEYWORDS = ("warranty deed", "quitclaim", "deed without warranty", "tax deed", "sheriff",
                       "heirship", "probate", "executor's deed", "trustee's deed", "gift deed", "deed of gift",
                       "deed of conveyance", "cash sale", "general warranty", "special warranty", "correction deed",
                       "corrective deed")
PARTIAL_AREA = 0.95   # a conveyance under 95% of the tract's acreage is treated as covering only part of it


def classify(itype: str) -> str:
    """Broad category for an instrument type string (already lower-cased)."""
    t = itype or ""
    if not t:
        return "unknown"
    if t.startswith("release") or "partial release" in t:
        return "release"
    if any(k in t for k in ("pore", "sequestration", "carbon", "co2", "underground storage", "subsurface storage",
                            "storage cavern", "storage agreement", "storage lease", "storage easement")):
        agreement = any(k in t for k in ("lease", "easement", "option", "agreement", "license", "right of way"))
        return "pore space agreement" if agreement else "pore space"
    if "judgment" in t or "lis pendens" in t:
        return "judgment"
    if any(k in t for k in CONVEYANCE_KEYWORDS) and "deed of trust" not in t and "mortgage" not in t:
        return "conveyance"              # 'warranty deed with vendor's lien' is a deed that retains a lien
    if ("deed of trust" in t or "mortgage" in t or "lien" in t
            or re.search(r"\bucc\b", t) or "financing statement" in t):
        return "lien"
    if ("lease" in t and not re.search(r"oil|gas|mineral", t)) or "surface agreement" in t or "possession and use" in t:
        return "surface agreement"       # land, solar, wind and ground leases come before the easement check
    if "easement" in t or "right-of-way" in t or "right of way" in t or re.search(r"\brow\b", t):
        return "easement"
    if "restriction" in t or "covenant" in t:
        return "restriction"
    if ("surface lease" in t or "surface use" in t or "damage" in t or "timber" in t or "accommodation" in t
            or "wind" in t or "solar" in t or "ground lease" in t
            or ("lease" in t and not re.search(r"oil|gas|mineral", t))):
        return "surface agreement"
    if t == "deed" or any(k in t for k in CONVEYANCE_KEYWORDS):
        return "conveyance"
    return "other"


def holder(inst: dict) -> list[str]:
    """Who holds the encumbrance. Judgments and lien claims list the creditor as grantor;
    deeds of trust, UCC financing statements, easements and the rest list the holder as grantee."""
    t = inst["type"]
    secured_party_is_grantee = any(k in t for k in ("deed of trust", "mortgage", "financing statement")) \
        or re.search(r"\bucc\b", t)
    creditor_is_grantor = inst["category"] == "judgment" or (inst["category"] == "lien" and not secured_party_is_grantee)
    return inst["grantors"] if creditor_is_grantor else inst["grantees"]


def _dedupe(names):
    out = []
    for n in names:
        if n and not any(token_sort_ratio(n.lower(), o.lower()) >= 90 for o in out):
            out.append(n)
    return out


def _slim(i):
    keys = ("instrument_id", "type", "category", "filing_date", "execution_date", "grantors", "grantees",
            "file_name", "acreage", "interest", "kind", "legal", "instrument_no", "mineral_exception",
            "easements", "liens", "notes", "pages", "ref_keys", "restrictions", "pore_space", "term", "county",
            "values", "calc", "type_conf", "operative", "runsheet_type")
    return {k: i.get(k) for k in keys}


def is_fractional(inst) -> bool:
    """Conveys an undivided part of the interest (e.g. 'undivided 5% of the surface'), not all of it."""
    t = (inst.get("interest") or "").lower()
    if not t or re.search(r"\b100\s*%|\ball\b|entire|fee simple", t):
        return False
    return bool(re.search(r"\d+(?:\.\d+)?\s*%|\b\d+\s*/\s*\d+\b|undivided|per\s?cent|one-half|one-fourth", t))


def is_partial_area(inst, subject_acres) -> bool:
    """Covers clearly less land than the subject tract (an out-sale or part of the tract)."""
    a = inst.get("acreage")
    return bool(subject_acres and a and a < subject_acres * PARTIAL_AREA)


def build_chain(instruments: list[dict], subject_acres: float | None = None) -> dict:
    """subject_acres: the tract's acreage (from the intake form, tracts.csv or a tract runsheet).
    Without it, out-sales can't be told apart from conveyances of the whole tract, so the last
    conveyance is used and a review note is added when it looks smaller than the one before."""
    supporting = [i for i in instruments if not i.get("recorded", True)]
    recorded = [i for i in instruments if i.get("recorded", True)]
    # Filing order; an instrument with no filing date read goes by the date it was signed, and one with
    # neither goes last and is never taken as the vesting deed (a late-recorded probate, say, must not
    # look like the newest conveyance).
    when = lambda i: i["filing_date"] or i.get("execution_date")
    ordered = sorted(recorded, key=lambda i: (when(i) is None, when(i) or ""))
    conveyances = [i for i in ordered if i["category"] == "conveyance"]
    released = {r for i in ordered if i["category"] == "release" for r in i["refers_to"]}
    notes = []

    whole = [c for c in conveyances if not is_fractional(c) and not is_partial_area(c, subject_acres) and when(c)]
    vesting = whole[-1] if whole else (conveyances[-1] if conveyances else None)
    if vesting is not None:
        later = conveyances[conveyances.index(vesting) + 1:]
        for c in later:
            who = ", ".join(c["grantees"]) or "?"
            if is_partial_area(c, subject_acres):
                notes.append((c["instrument_id"], f"Conveys {c['acreage']:g} ac of the {subject_acres:g} ac tract to {who}: "
                                                  "confirm it is an out-sale excepted from the tract"))
            elif is_fractional(c):
                notes.append((c["instrument_id"], f"Conveys an undivided interest ({c['interest']}) to {who} after "
                                                  "the vesting deed: ownership may be shared"))
        if subject_acres and vesting.get("acreage") is None and len(conveyances) >= 2:
            notes.append((vesting["instrument_id"],
                          "Acreage could not be read from this deed, so it is taken as covering the whole "
                          f"{subject_acres:g} ac tract: confirm it isn't an out-sale"))
        if not subject_acres and len(conveyances) >= 2 and vesting is conveyances[-1]:
            prev = conveyances[-2]
            if vesting.get("acreage") and prev.get("acreage") and vesting["acreage"] < prev["acreage"] * PARTIAL_AREA:
                notes.append((vesting["instrument_id"],
                              f"Last conveyance covers {vesting['acreage']:g} ac, less than the {prev['acreage']:g} ac "
                              "before it: may be an out-sale, so the current owner may be the grantor. Add the tract's "
                              "acreage (tracts.csv) to resolve this automatically"))
    if not conveyances:
        notes.append((None, "No conveyance found in the package: current owner could not be determined"))
    for i in ordered:
        if i["category"] != "conveyance" and conveys_fee(i) and (i["category"] in ("other", "unknown")
                                                                  or i.get("type_conf") == "low"):
            notes.append((i["instrument_id"], f"Type unclear ('{i['type'] or 'none'}') but the operative words "
                                              f"('{i.get('operative')}') read like a conveyance. It was not used for "
                                              "ownership: decide whether it belongs in the chain"))

    for i in ordered:
        i["calc"] = instrument_calc(i, released, ordered)

    encumbrances = [
        {**_slim(i), "holder": holder(i), "status": "released" if i["ref_keys"] & released else "open"}
        for i in ordered if i["category"] in ENCUMBRANCE_CATEGORIES
    ]
    owners = _dedupe((conveyances[0]["grantors"] if conveyances else [])
                     + [g for c in conveyances for g in c["grantees"]])
    result = {
        "instruments": [_slim(i) for i in ordered],
        "conveyances": [_slim(i) for i in conveyances],
        "current_owner": vesting["grantees"] if vesting else [],
        "vesting_instrument": vesting["instrument_id"] if vesting else None,
        "subject_acres": subject_acres,
        "ownership_notes": notes,
        "encumbrances": encumbrances,
        "owners": owners,
        "supporting": [_slim(i) for i in supporting],
        "undated": [i["instrument_id"] for i in ordered if not i["filing_date"]],
        "unclassified": [i["instrument_id"] for i in ordered if i["category"] in ("other", "unknown")],
    }
    result["remaining_acres"] = remaining_acres(vesting, notes, conveyances, subject_acres)
    from .pore import pore_owner
    result["pore_space"] = pore_owner(result)
    return result


FEE_WORDS = re.compile(r"\b(?:grants?,?\s+(?:bargains?,?\s+)?sells?|sell\s+and\s+convey|convey(?:s|ed)?\s+(?:unto|to)|"
                       r"bargain,?\s+sell|quitclaim|release,?\s+remise)", re.I)
EASEMENT_WORDS = re.compile(r"easement|right[- ]of[- ]way|\bright to (?:lay|construct|install)", re.I)


def conveys_fee(i) -> bool:
    op = i.get("operative") or ""
    return bool(FEE_WORDS.search(op)) and not EASEMENT_WORDS.search(op) and "lease" not in op.lower()


def remaining_acres(vesting, notes, conveyances, subject_acres):
    """Vesting (or tract) acreage less the out-sales after the vesting deed, when both are known."""
    base = (vesting or {}).get("acreage") or subject_acres
    if not base or vesting is None:
        return None
    later = conveyances[conveyances.index(vesting) + 1:] if vesting in conveyances else []
    out = [c["acreage"] for c in later if c.get("acreage") and subject_acres and is_partial_area(c, subject_acres)]
    return {"base": base, "out_sales": out, "remaining": round(base - sum(out), 3)}


def instrument_calc(i, released, ordered) -> dict:
    """Lien and easement figures worked out in code (see calc.py)."""
    from .calc import abandonment_months, closure, expiration, lien_figures
    v = i.get("values") or {}
    out = {}
    if i["category"] == "lien":
        out["lien"] = lien_figures(v.get("lien_amount") or v.get("consideration"), v.get("interest_rate"),
                                   v.get("lien_term"), v.get("payment_terms"), i.get("execution_date"),
                                   v.get("maturity_date"))
    if i["category"] in ("lien", "judgment"):
        rel = [r for r in ordered if r["category"] == "release" and i["ref_keys"] & set(r["refers_to"])]
        out["release"] = {"status": ("released" if rel else "no release found"),
                          "reference": "; ".join(f"{r['instrument_id']} ({r['filing_date'] or 'undated'})" for r in rel)
                                       or None}
    if i["category"] in ("easement", "surface agreement", "pore space agreement") or (
            i["category"] != "release" and re.search(r"\blease", i["type"] or "")):
        exp, how = expiration(v.get("term") or i.get("term"), v.get("term_years"), v.get("expiration_date"),
                              i.get("execution_date") or i.get("filing_date"), v.get("perpetual"))
        out["expiration"] = {"date": exp, "how": how}
        ab = abandonment_months(v.get("abandonment_period_months"), v.get("abandonment_trigger"))
        if ab or v.get("abandonment_trigger") or v.get("abandonment_effect"):
            out["abandonment"] = {"months": ab, "trigger": v.get("abandonment_trigger"),
                                  "effect": v.get("abandonment_effect")}
    calls = (i.get("record") or {}).get("metes_and_bounds_calls", {})
    calls = calls.get("value") if isinstance(calls, dict) else None
    if isinstance(calls, list) and calls:
        out["closure"] = closure(calls)
    return out
