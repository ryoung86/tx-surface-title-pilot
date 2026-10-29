"""Order instruments, find the current surface owner, and mark encumbrances open or released.

Instrument types are classified by keyword, not exact name, because the model
writes them in many ways ("Easement / ROW", "easement/row", "Pipeline
Right-of-Way Easement"). Adjust the keywords as real documents come in.
"""
import re

from .fuzzy import token_sort_ratio

ENCUMBRANCE_CATEGORIES = {"lien", "judgment", "easement", "restriction", "surface agreement"}

CONVEYANCE_KEYWORDS = ("warranty deed", "quitclaim", "deed without warranty", "tax deed", "sheriff",
                       "heirship", "probate", "executor's deed", "trustee's deed", "gift deed", "deed of gift",
                       "deed of conveyance", "cash sale", "general warranty", "special warranty")
PARTIAL_AREA = 0.95   # a conveyance under 95% of the tract's acreage is treated as covering only part of it


def classify(itype: str) -> str:
    """Broad category for an instrument type string (already lower-cased)."""
    t = itype or ""
    if not t:
        return "unknown"
    if t.startswith("release") or "partial release" in t:
        return "release"
    if "judgment" in t or "lis pendens" in t:
        return "judgment"
    if ("deed of trust" in t or "mortgage" in t or "lien" in t
            or re.search(r"\bucc\b", t) or "financing statement" in t):
        return "lien"
    if "easement" in t or "right-of-way" in t or "right of way" in t or re.search(r"\brow\b", t):
        return "easement"
    if "restriction" in t or "covenant" in t:
        return "restriction"
    if "surface lease" in t or "surface use" in t or "damage" in t:
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
    keys = ("instrument_id", "type", "category", "filing_date", "grantors", "grantees", "file_name",
            "acreage", "interest", "kind")
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
    ordered = sorted(recorded, key=lambda i: (i["filing_date"] is None, i["filing_date"] or ""))
    conveyances = [i for i in ordered if i["category"] == "conveyance"]
    released = {r for i in ordered if i["category"] == "release" for r in i["refers_to"]}
    notes = []

    whole = [c for c in conveyances if not is_fractional(c) and not is_partial_area(c, subject_acres)]
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
        if not subject_acres and len(conveyances) >= 2 and vesting is conveyances[-1]:
            prev = conveyances[-2]
            if vesting.get("acreage") and prev.get("acreage") and vesting["acreage"] < prev["acreage"] * PARTIAL_AREA:
                notes.append((vesting["instrument_id"],
                              f"Last conveyance covers {vesting['acreage']:g} ac, less than the {prev['acreage']:g} ac "
                              "before it: may be an out-sale, so the current owner may be the grantor. Add the tract's "
                              "acreage (tracts.csv) to resolve this automatically"))
    if not conveyances:
        notes.append((None, "No conveyance found in the package: current owner could not be determined"))

    encumbrances = [
        {**_slim(i), "holder": holder(i), "status": "released" if i["ref_keys"] & released else "open"}
        for i in ordered if i["category"] in ENCUMBRANCE_CATEGORIES
    ]
    owners = _dedupe((conveyances[0]["grantors"] if conveyances else [])
                     + [g for c in conveyances for g in c["grantees"]])
    return {
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
