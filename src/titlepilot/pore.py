"""Pore space ownership.

Texas default: the surface owner owns the pore space (the empty subsurface space usable for
storage, such as CO2 sequestration or gas storage) unless a conveyance says otherwise, and
pore space is not a mineral, so a mineral reservation does not take it.
Myers-Woodward, LLC v. Underground Services Markham, LLC (Tex. 2025). Confirm with counsel.

So the pore space follows the surface owner unless the package has language that splits it off:
- a deed that reserves or excepts the pore space (it stays with the grantor),
- a deed or other conveyance of the pore space alone (it goes to the grantee),
- a pore space / storage / sequestration lease, easement or option (a burden, not a change of owner),
- any other mention, which a landman needs to read.
"""
import re

PORE = re.compile(
    r"pore\s*[- ]?space|subsurface\s+(?:storage|space|pore|reservoir)|underground\s+(?:storage|reservoir|space)"
    r"|geolog(?:ic|ical)\s+(?:storage|formation|sequestration|space)|sequest(?:er|ration)"
    r"|carbon\s+(?:dioxide\s+)?(?:storage|capture|injection)|\bCO2\b|\bCO₂\b|storage\s+(?:rights|space|cavern|facility|reservoir)"
    r"|salt\s+(?:cavern|dome\s+storage)|\bcaverns?\b|injection\s+(?:and\s+storage\s+)?rights", re.I)
RESERVE = re.compile(r"reserv|except|retain|withh(?:o|e)ld|save\s*(?:and|&)\s*except|not\s+(?:convey|includ)", re.I)
GRANT = re.compile(r"\b(?:grant|convey|sell|assign|transfer)s?\b|bargain", re.I)
AGREEMENT_TYPES = ("lease", "easement", "option", "agreement", "license", "right of way", "right-of-way", "unit")
PORE_TYPES = ("pore", "storage", "sequestration", "carbon", "co2", "cavern")
CASE = "Myers-Woodward, LLC v. Underground Services Markham, LLC (Tex. 2025)"


def pore_text(inst) -> str:
    """Every place the extraction could have put pore space language."""
    parts = [inst.get("pore_space"), inst.get("mineral_exception"), inst.get("easements"), inst.get("restrictions"),
             inst.get("notes"), inst.get("legal")]
    return " | ".join(p for p in parts if p)


def is_pore_type(itype) -> bool:
    t = (itype or "").lower()
    return any(k in t for k in PORE_TYPES)


def mention_kind(inst) -> str | None:
    """reservation, grant, agreement or mention; None if the instrument doesn't touch pore space."""
    t = (inst.get("type") or "").lower()
    text = pore_text(inst)
    hit = PORE.search(text)
    if is_pore_type(t):
        return "agreement" if any(k in t for k in AGREEMENT_TYPES) else "grant"
    if not hit:
        return None
    window = text[max(0, hit.start() - 160): hit.end() + 80]
    if RESERVE.search(window):
        return "reservation"
    if inst.get("category") == "conveyance" and GRANT.search(window):
        return "grant"          # conveyed along with the surface
    return "mention"


def _snippet(inst):
    text = pore_text(inst)
    m = PORE.search(text)
    if not m:
        return (inst.get("pore_space") or inst.get("type") or "")[:200]
    return text[max(0, m.start() - 120): m.end() + 120].strip(" |")


def pore_owner(chain: dict) -> dict:
    """Walk the recorded instruments in filing order and decide who owns the pore space."""
    owners, vesting, severed = list(chain.get("current_owner") or []), chain.get("vesting_instrument"), False
    basis = (f"No pore space language found in the package, so the pore space follows the surface ({CASE}). "
             "Confirm with counsel.")
    mentions, burdens = [], []
    for i in chain.get("instruments", []):
        kind = mention_kind(i)
        if not kind:
            continue
        mentions.append({"instrument_id": i["instrument_id"], "kind": kind, "text": _snippet(i),
                         "grantors": i.get("grantors", []), "grantees": i.get("grantees", []),
                         "filing_date": i.get("filing_date"), "type": i.get("type")})
        if kind == "reservation" and i.get("category") == "conveyance":
            severed, owners, vesting = True, list(i.get("grantors", [])), i["instrument_id"]
            basis = (f"Pore space reserved or excepted in {i['instrument_id']}: it stays with "
                     f"{', '.join(owners) or 'the grantor'} and does not follow later surface deeds. Confirm the wording "
                     "and trace the reserved pore space forward.")
        elif kind == "grant" and i.get("category") == "pore space":
            severed, owners, vesting = True, list(i.get("grantees", [])), i["instrument_id"]
            basis = (f"Pore space conveyed separately in {i['instrument_id']} to {', '.join(owners) or 'the grantee'}. "
                     "Confirm the wording and trace it forward.")
        elif kind == "agreement":
            burdens.append(i["instrument_id"])
    if mentions and not severed:
        basis = ("Pore space language found (see PS01 flags) but no clear reservation or separate conveyance, so the "
                 f"pore space is shown as following the surface ({CASE}). A landman must read the language.")
    return {"owners": owners, "vesting_instrument": vesting, "severed": severed, "basis": basis,
            "mentions": mentions, "burdens": burdens}
