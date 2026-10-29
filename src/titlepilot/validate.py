"""Schema checks and normalized copies of key values (originals are kept)."""
import re
import calendar
from datetime import date

from jsonschema import Draft202012Validator

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


def src(record: dict, name: str):
    f = record.get(name)
    return f.get("source_text") if isinstance(f, dict) else None


def number(v):
    """First number in a value: '5.60 acres, more or less' -> 5.6."""
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d[\d,]*(?:\.\d+)?|\.\d+", str(v or ""))
    return float(m.group(0).replace(",", "")) if m else None


def conf(record: dict, name: str):
    f = record.get(name)
    return f.get("confidence") if isinstance(f, dict) else None


_MONTHS = {name: i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower()[:3]: i for name, i in list(_MONTHS.items())})
_MONTHS.update({name.lower(): i for name, i in list(_MONTHS.items()) if name[0].isupper()})
_MONTHS["sept"] = 9


def _make(y, m, d):
    try:
        y = int(y)
        if y < 100:
            y += 1900 if y > 30 else 2000
        return date(y, int(m), int(d)).isoformat()
    except (ValueError, TypeError):
        return None


def norm_date(s):
    """Date in almost any written form -> 'YYYY-MM-DD' (None if no full date is found).

    Handles 1983-12-21, 12/21/1983, 12-21-83, December 21, 1983, Dec. 21st, 1983,
    the 21st day of December, A.D. 1983, and text around them ('Filed Dec. 21, 1983 at 2:30 PM').
    """
    if not s:
        return None
    t = str(s).strip().lower()
    m = re.search(r"\b(1[789]\d{2}|20\d{2})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        return _make(m.group(1), m.group(2), m.group(3))
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})\b", t)
    if m:
        return _make(m.group(3), m.group(1), m.group(2))
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?([a-z]+)\.?,?\s+(?:a\.?\s?d\.?,?\s+)?(\d{4})\b", t)
    if m and m.group(2) in _MONTHS:
        return _make(m.group(3), _MONTHS[m.group(2)], m.group(1))
    m = re.search(r"\b([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(?:a\.?\s?d\.?,?\s+)?(\d{4})\b", t)
    if m and m.group(1) in _MONTHS:
        return _make(m.group(3), _MONTHS[m.group(1)], m.group(2))
    return None


_VOL_PAGE = re.compile(r"\bvol(?:ume)?\.?\s*0*(\d+)\D{0,12}?\bp(?:age|g)?s?\.?\s*0*(\d+)", re.I)
_SLASH_VOL_PAGE = re.compile(r"(?<![\d-])0*(\d{1,5})\s*/\s*0*(\d{1,5})(?![\d/-])")
_YEAR_NUMBER = re.compile(r"(?<!\d)((?:18|19|20)\d{2})\s*-\s*0*(\d{1,9})(?!\d)")
_YEAR_NUMBER_JOINED = re.compile(r"(?<!\d)((?:19|20)\d{2})(\d{6})(?!\d)")


def norm_ref(s) -> str:
    """Matching key for a recording reference, ignoring county names and formatting.

    'Sample County, Texas OPR 2001-004410', 'OPR 2001-4410', '2001004410' -> '2001-4410'
    'Vol. 412, p. 88', 'Volume 412 Page 088', '412/88', 'Esmt. 412/88'    -> 'V412P88'
    Anything else falls back to letters and digits only.
    """
    if not s:
        return ""
    s = str(s)
    key = _ref_key(s)
    return key if key else re.sub(r"[^A-Z0-9]", "", s.upper())


def _ref_key(s: str):
    for pattern, fmt in ((_VOL_PAGE, "V{}P{}"), (_YEAR_NUMBER, "{}-{}"), (_YEAR_NUMBER_JOINED, "{}-{}"),
                         (_SLASH_VOL_PAGE, "V{}P{}")):
        m = pattern.search(s)
        if m:
            a, b = m.group(1), int(m.group(2))
            # 'Vol. 2011, Page 009176' is really instrument number 2011-009176
            if fmt.startswith("V") and re.fullmatch(r"(19|20)\d{2}", a) and b >= 1000:
                return f"{a}-{b}"
            return fmt.format(a, b)
    return None


def is_ref(s) -> bool:
    """True when the text contains a recognizable recording reference (not a description)."""
    return bool(s) and _ref_key(str(s)) is not None


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

    from .schema import RECORDED

    iid = val(record, "instrument_id") or doc.recording_ref or doc.file_name
    raw_type = val(record, "instrument_type")
    if isinstance(raw_type, list):          # older configs returned a list; the first is the instrument's own type
        raw_type = raw_type[0] if raw_type else None
    itype = norm_type(str(raw_type).split(";")[0] if raw_type else doc.instrument_type)
    kind = (as_text(val(record, "document_kind")) or "").strip().lower()
    if not kind:
        kind = RECORDED if is_ref(val(record, "instrument_id")) else "other supporting document"
    return {
        "instrument_id": iid,
        "ref_keys": {k for k in (norm_ref(iid), norm_ref(doc.recording_ref)) if k},
        "file_name": doc.file_name,
        "type": itype,
        "category": classify(itype),
        "filing_date": (norm_date(val(record, "filing_date")) or norm_date(src(record, "filing_date"))
                        or norm_date(doc.filing_date)),
        "execution_date": norm_date(val(record, "execution_date")) or norm_date(src(record, "execution_date")),
        "grantors": party_names(val(record, "grantors")),
        "grantees": party_names(val(record, "grantees")),
        "refers_to": [norm_ref(x) for x in (val(record, "refers_to") or []) if is_ref(x)],
        "kind": kind,
        "recorded": RECORDED in kind,
        "acreage": number(val(record, "acreage")),
        "interest": as_text(val(record, "surface_interest")),
        "legal": as_text(val(record, "legal_description_text")),
        "instrument_no": as_text(val(record, "instrument_no")),
        "mineral_exception": as_text(val(record, "mineral_exception")),
        "easements": as_text(val(record, "easements")),
        "liens": as_text(val(record, "liens")),
        "notes": as_text(val(record, "notes")),
        "restrictions": as_text(val(record, "restrictions")),
        "county": as_text(val(record, "county")),
        "found_by": doc.found_by,
        "record": record,
    }
