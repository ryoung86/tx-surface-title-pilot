"""Tract-level outputs built from the whole package (broker sheet, Phase 2).

- Taxes and appraisal district: merged from the CAD and tax office printouts the agent adds to the package.
- Notice address for initial and final offer letters (IOL/FOL): always the appraisal district mailing address,
  never the deed address.
- Preliminary legal description: the vesting deed's description, less each out-sale after it with its recording
  reference, and the remaining acreage. Always PRELIMINARY until a named reviewer approves it on the report.
"""
import re

from .validate import number

TAX_FIELDS = ["cad_property_id", "cad_owner_of_record", "cad_mailing_address", "taxes_current", "last_tax_year_paid",
              "last_amount_paid", "last_payment_date", "delinquent_amount", "exemptions_special_valuation",
              "tax_check_source"]
EMPTY = re.compile(r"^\s*(?:none|n/?a|unknown|not\s+(?:shown|stated|found))?\s*\.?\s*$", re.I)


def is_tax_record(i) -> bool:
    kind = (i.get("kind") or "").lower()
    return "tax" in kind or "apprais" in kind or any((i.get("values") or {}).get(f) for f in ("cad_property_id", "taxes_current"))


def tax_summary(supporting: list, instruments: list) -> dict:
    """First value found for each tax field across the printouts (CAD first, then the tax office)."""
    records = [i for i in list(supporting) + list(instruments) if is_tax_record(i)]
    records.sort(key=lambda i: 0 if "apprais" in (i.get("kind") or "").lower() or "cad" in (i.get("file_name") or "").lower() else 1)
    out = {"files": [i["file_name"] for i in records]}
    for f in TAX_FIELDS:
        out[f] = next(((i.get("values") or {}).get(f) for i in records
                       if (i.get("values") or {}).get(f) and not EMPTY.match(str((i.get("values") or {}).get(f)))), None)
    out["notice_address"] = out.get("cad_mailing_address")
    current = (out.get("taxes_current") or "").strip().lower()
    owed = number(out.get("delinquent_amount"))
    out["delinquent"] = current.startswith("no") or "delinq" in current or bool(owed and owed > 0)
    return out


def preliminary_legal(vesting: dict | None, out_sales: list, remaining: dict | None) -> dict | None:
    """vesting: the vesting deed (slim). out_sales: later conveyances of part of the tract (slim)."""
    if not vesting or not vesting.get("legal"):
        return None
    lines = ["PRELIMINARY - NOT APPROVED. Generated from the record; a reviewer must check and approve it.", "",
             vesting["legal"].strip()]
    own = (vesting.get("values") or {}).get("less_and_except")
    if own and not EMPTY.match(own):
        lines += ["", f"(Save-and-except in the vesting deed {vesting['instrument_id']}, as written: {own.strip()})"]
    used = []
    if out_sales:
        lines += ["", "SAVE AND EXCEPT:"]
        for n, c in enumerate(out_sales, 1):
            who = ", ".join(c.get("grantees") or []) or "?"
            lines.append(f"  {n}. {c['acreage']:g} acres conveyed to {who} by instrument {c['instrument_id']}"
                         + (f", filed {c['filing_date']}" if c.get("filing_date") else "") + ";")
            used.append(f"{c['instrument_id']} ({c['acreage']:g} ac)")
    if remaining:
        lines += ["", f"LEAVING A TOTAL of {remaining['remaining']:g} acres, more or less."]
    return {"text": "\n".join(lines), "vesting_instrument": vesting["instrument_id"], "less_and_except_instruments": used,
            "remaining_acreage": remaining["remaining"] if remaining else None, "status": "Preliminary",
            "approved_by": None, "vesting_less_and_except": own}
