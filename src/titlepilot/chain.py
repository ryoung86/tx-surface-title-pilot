"""Order instruments, find the current surface owner, and mark encumbrances open or released."""
from .fuzzy import token_sort_ratio

# Adjust these as the instrument-type list in the workbook settles.
VESTING_TYPES = {
    "warranty deed", "special warranty deed", "deed without warranty", "quitclaim deed", "deed",
    "tax deed", "sheriff's deed", "affidavit of heirship", "probate",
}
ENCUMBRANCE_TYPES = {
    "deed of trust", "vendor's lien", "lien", "mechanic's lien", "tax lien", "abstract of judgment",
    "judgment", "lis pendens", "easement / row", "easement", "right-of-way", "restrictions",
    "surface lease", "surface use or damage agreement",
}


def is_release(itype: str) -> bool:
    return itype.startswith("release") or itype == "partial release"


def _dedupe(names):
    out = []
    for n in names:
        if n and not any(token_sort_ratio(n.lower(), o.lower()) >= 90 for o in out):
            out.append(n)
    return out


def _slim(i):
    return {k: i[k] for k in ("instrument_id", "type", "filing_date", "grantors", "grantees", "file_name")}


def build_chain(instruments: list[dict]) -> dict:
    ordered = sorted(instruments, key=lambda i: (i["filing_date"] is None, i["filing_date"] or ""))
    conveyances = [i for i in ordered if i["type"] in VESTING_TYPES]
    released = {r for i in ordered if is_release(i["type"]) for r in i["refers_to"]}

    encumbrances = [
        {**_slim(i), "status": "released" if i["ref_keys"] & released else "open"}
        for i in ordered if i["type"] in ENCUMBRANCE_TYPES
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
    }
