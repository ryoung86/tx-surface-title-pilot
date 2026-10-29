"""Order instruments, find the current surface owner, and mark encumbrances open or released.

Instrument types are classified by keyword, not exact name, because the model
writes them in many ways ("Easement / ROW", "easement/row", "Pipeline
Right-of-Way Easement"). Adjust the keywords as real documents come in.
"""
import re

from .fuzzy import token_sort_ratio

ENCUMBRANCE_CATEGORIES = {"lien", "judgment", "easement", "restriction", "surface agreement"}

CONVEYANCE_KEYWORDS = ("warranty deed", "quitclaim", "deed without warranty", "tax deed", "sheriff",
                       "heirship", "probate", "executor's deed", "trustee's deed", "gift deed")


def classify(itype: str) -> str:
    """Broad category for an instrument type string (already lower-cased)."""
    t = itype or ""
    if not t:
        return "unknown"
    if t.startswith("release") or "partial release" in t:
        return "release"
    if "judgment" in t or "lis pendens" in t:
        return "judgment"
    if "deed of trust" in t or "mortgage" in t or "lien" in t:
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
    deeds of trust, easements and the rest list the holder as grantee."""
    t = inst["type"]
    creditor_is_grantor = inst["category"] == "judgment" or (
        inst["category"] == "lien" and "deed of trust" not in t and "mortgage" not in t)
    return inst["grantors"] if creditor_is_grantor else inst["grantees"]


def _dedupe(names):
    out = []
    for n in names:
        if n and not any(token_sort_ratio(n.lower(), o.lower()) >= 90 for o in out):
            out.append(n)
    return out


def _slim(i):
    return {k: i[k] for k in ("instrument_id", "type", "category", "filing_date", "grantors", "grantees", "file_name")}


def build_chain(instruments: list[dict]) -> dict:
    ordered = sorted(instruments, key=lambda i: (i["filing_date"] is None, i["filing_date"] or ""))
    conveyances = [i for i in ordered if i["category"] == "conveyance"]
    released = {r for i in ordered if i["category"] == "release" for r in i["refers_to"]}

    encumbrances = [
        {**_slim(i), "holder": holder(i), "status": "released" if i["ref_keys"] & released else "open"}
        for i in ordered if i["category"] in ENCUMBRANCE_CATEGORIES
    ]
    owners = _dedupe((conveyances[0]["grantors"] if conveyances else [])
                     + [g for c in conveyances for g in c["grantees"]])
    return {
        "instruments": [_slim(i) for i in ordered],
        "conveyances": [_slim(i) for i in conveyances],
        "current_owner": conveyances[-1]["grantees"] if conveyances else [],
        "vesting_instrument": conveyances[-1]["instrument_id"] if conveyances else None,
        "encumbrances": encumbrances,
        "owners": owners,
        "undated": [i["instrument_id"] for i in ordered if not i["filing_date"]],
        "unclassified": [i["instrument_id"] for i in ordered if i["category"] in ("other", "unknown")],
    }
