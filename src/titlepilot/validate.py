"""Schema checks and normalized copies of key values (originals are kept)."""
import re
from datetime import datetime

from jsonschema import Draft202012Validator

DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%m/%d/%y"]


def schema_errors(record: dict, schema: dict) -> list[str]:
    data = {k: v for k, v in record.items() if not k.startswith("_")}
    return [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}"
            for e in Draft202012Validator(schema).iter_errors(data)]


def val(record: dict, name: str):
    f = record.get(name)
    return f.get("value") if isinstance(f, dict) else None


def conf(record: dict, name: str):
    f = record.get(name)
    return f.get("confidence") if isinstance(f, dict) else None


def norm_date(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def norm_ref(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper()) if s else ""


def norm_type(s) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower()) if s else ""


def party_names(v) -> list[str]:
    return [p["name"] if isinstance(p, dict) else str(p) for p in (v or []) if p]


def normalize(record: dict, doc) -> dict:
    """doc is the intake DocRow for this file."""
    iid = val(record, "instrument_id") or doc.recording_ref or doc.file_name
    return {
        "instrument_id": iid,
        "ref_keys": {k for k in (norm_ref(iid), norm_ref(doc.recording_ref)) if k},
        "file_name": doc.file_name,
        "type": norm_type(val(record, "instrument_type") or doc.instrument_type),
        "filing_date": norm_date(val(record, "filing_date")) or norm_date(doc.filing_date),
        "grantors": party_names(val(record, "grantors")),
        "grantees": party_names(val(record, "grantees")),
        "refers_to": [norm_ref(x) for x in (val(record, "refers_to") or []) if x],
        "found_by": doc.found_by,
        "record": record,
    }
