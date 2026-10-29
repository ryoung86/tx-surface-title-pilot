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
    """Field value, with "" / [] (the schema's 'not on the page') returned as None."""
    f = record.get(name)
    v = f.get("value") if isinstance(f, dict) else None
    if v is None or (isinstance(v, (str, list)) and not v):
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return v


def as_text(v):
    """Lists (e.g. a 'pick from list' field returned as an array) joined into one string."""
    if v is None:
        return None
    if isinstance(v, list):
        return "; ".join(str(x) for x in v if x) or None
    return str(v)


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


_YEAR_NUMBER = re.compile(r"(?<!\d)((?:18|19|20)\d{2})\s*[-/]\s*0*(\d{1,9})(?!\d)")
_VOL_PAGE = re.compile(r"\bvol(?:ume)?\.?\s*0*(\d+)\D{0,12}?\bp(?:age|g)?s?\.?\s*0*(\d+)", re.I)


def norm_ref(s) -> str:
    """Matching key for a recording reference, ignoring county names and formatting.

    'Sample County, Texas OPR 2001-004410', 'OPR 2001-4410' and '2001-004410' -> '2001-4410'
    'Vol. 412, p. 88' and 'Volume 412 Page 088'                               -> 'V412P88'
    Anything else falls back to letters and digits only.
    """
    if not s:
        return ""
    s = str(s)
    m = _YEAR_NUMBER.search(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = _VOL_PAGE.search(s)
    if m:
        return f"V{m.group(1)}P{m.group(2)}"
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def norm_type(s) -> str:
    """Lower-case, single spaces, and spaces around slashes: 'Easement/ROW' -> 'easement / row'."""
    if not s:
        return ""
    s = re.sub(r"\s*/\s*", " / ", str(s).strip().lower())
    return re.sub(r"\s+", " ", s)


def party_names(v) -> list[str]:
    return [p["name"] if isinstance(p, dict) else str(p) for p in (v or []) if p]


def normalize(record: dict, doc) -> dict:
    """doc is the intake DocRow for this file."""
    from .chain import classify

    iid = val(record, "instrument_id") or doc.recording_ref or doc.file_name
    itype = norm_type(as_text(val(record, "instrument_type")) or doc.instrument_type)
    return {
        "instrument_id": iid,
        "ref_keys": {k for k in (norm_ref(iid), norm_ref(doc.recording_ref)) if k},
        "file_name": doc.file_name,
        "type": itype,
        "category": classify(itype),
        "filing_date": norm_date(val(record, "filing_date")) or norm_date(doc.filing_date),
        "grantors": party_names(val(record, "grantors")),
        "grantees": party_names(val(record, "grantees")),
        "refers_to": [norm_ref(x) for x in (val(record, "refers_to") or []) if x],
        "found_by": doc.found_by,
        "record": record,
    }
