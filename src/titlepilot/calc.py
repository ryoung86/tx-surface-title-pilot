"""Numbers the program works out itself, from what the AI read.

The model copies amounts, rates, terms and calls as written; the arithmetic is done here, so a misread is
the only way a figure can be wrong, and the figure can be traced back to the words.

- Liens: maturity date, remaining term and an estimated remaining balance (standard amortization, payments
  assumed made as scheduled). Always an estimate.
- Easements and leases: expiration date from the term, and abandonment periods in months.
- Metes and bounds: misclosure and area from the parsed calls, compared with the stated acreage.
"""
import math
import re
from datetime import date

from .numbers import words_to_number
from .validate import norm_date, number

# --------------------------------------------------------------------------------------------------- periods
_UNIT_MONTHS = {"year": 12, "yr": 12, "month": 1, "mo": 1, "week": 12 / 52, "day": 12 / 365}
_PERIOD = re.compile(r"(\d+(?:\.\d+)?)\s*\)?\s*(?:consecutive\s+|full\s+|calendar\s+)?(years?|yrs?|months?|mos?|weeks?|days?)\b", re.I)
_WORD_PERIOD = re.compile(r"\b([a-z][a-z\- ]{2,40}?)\s+(?:consecutive\s+|full\s+|calendar\s+)?(years?|months?|weeks?|days?)\b", re.I)
_INSTALLMENTS = re.compile(r"(\d+)\s*\)?\s*(?:equal\s+|consecutive\s+|successive\s+)*(monthly|quarterly|semi-?annual|annual|yearly)\s+(?:installments?|payments?)", re.I)


def months_in(text):
    """'thirty (30) years' -> 360, '365 days' -> 12, '360 monthly installments' -> 360, 'two years' -> 24.
    None if no period is written."""
    t = str(text or "")
    m = _INSTALLMENTS.search(t)
    if m:
        per = {"monthly": 1, "quarterly": 3, "annual": 12, "yearly": 12}.get(m.group(2).lower().replace("-", ""), 6)
        return int(m.group(1)) * per
    m = _PERIOD.search(t)
    if m:
        unit = m.group(2).lower().rstrip("s")
        unit = {"yrs": "yr", "mos": "mo"}.get(unit, unit)
        return round(float(m.group(1)) * _UNIT_MONTHS[unit], 2)
    for m in _WORD_PERIOD.finditer(t):
        words = m.group(1).lower().split()
        for k in range(len(words)):          # the longest run of number words before the unit
            n = words_to_number(" ".join(words[k:]))
            if isinstance(n, int) and n > 0:
                return round(n * _UNIT_MONTHS[m.group(2).lower().rstrip("s")], 2)
    return None


def add_months(d: date, months: float) -> date:
    whole = int(round(months))
    y, m = divmod(d.month - 1 + whole, 12)
    day = min(d.day, [31, 29 if (d.year + y) % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m])
    return date(d.year + y, m + 1, day)


def months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


def _date(s):
    d = norm_date(s)
    return date.fromisoformat(d) if d else None


# ------------------------------------------------------------------------------------------------------ liens
_MONEY = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")


def money(text):
    """'ONE HUNDRED THOUSAND AND NO/100 DOLLARS ($100,000.00)' -> 100000.0. The digits win."""
    m = _MONEY.search(str(text or ""))
    if m:
        return float(m.group(1).replace(",", ""))
    v = number(text)
    return v if v and v >= 1 else None


def rate(text):
    """'eight and one-half percent (8.5%) per annum' -> 0.085. None if not stated or variable with no figure."""
    t = str(text or "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*%|(\d+(?:\.\d+)?)\s*per\s?cent", t, re.I)
    if m:
        return float(m.group(1) or m.group(2)) / 100
    return None


def lien_figures(amount_text, rate_text, term_text, payment_text, execution_date, maturity_text, today=None):
    """Maturity, months left, and estimated remaining balance of a lien. Missing pieces stay None."""
    today = today or date.today()
    start = _date(execution_date)
    term = months_in(term_text) or months_in(payment_text)
    maturity = _date(maturity_text)
    computed = False
    if not maturity and start and term:
        maturity, computed = add_months(start, term), True
    amount, r = money(amount_text), rate(rate_text)
    out = {"amount": amount, "rate": r, "term_months": term, "maturity": maturity.isoformat() if maturity else None,
           "maturity_computed": computed, "matured": None, "months_left": None, "balance": None, "missing": []}
    if maturity:
        out["matured"] = maturity <= today
        out["months_left"] = max(0, months_between(today, maturity)) if not out["matured"] else 0
    if out["matured"]:
        out["balance"] = 0.0
    elif amount and term and start and r is not None:
        n = int(round(term))
        paid = min(n, max(0, months_between(start, today)))
        i = r / 12
        if i == 0:
            out["balance"] = round(amount * (n - paid) / n, 2)
        else:
            out["balance"] = round(amount * ((1 + i) ** n - (1 + i) ** paid) / ((1 + i) ** n - 1), 2)
    for name, v in (("amount", amount), ("interest rate", r), ("term", term), ("execution date", start)):
        if v is None:
            out["missing"].append(name)
    return out


# --------------------------------------------------------------------------------------- easements and leases
PERPETUAL = re.compile(r"perpetu|forever|so long as (?:it is |the same is |said line is )?used|in perpetuity", re.I)


def expiration(term_text, term_years, expiration_text, start_date, perpetual_text=None):
    """(expiration date or None, how it was found). Perpetual grants and grants with no term return None."""
    stated = _date(expiration_text)
    if stated:
        return stated.isoformat(), "stated"
    if PERPETUAL.search(" ".join(str(x or "") for x in (term_text, perpetual_text))) \
            or str(perpetual_text or "").strip().lower().startswith("yes"):
        return None, "perpetual"
    start = _date(start_date)
    months = None
    if term_years and number(term_years):
        months = number(term_years) * 12
    months = months or months_in(term_text)
    if start and months:
        return add_months(start, months).isoformat(), "calculated"
    return None, "no term"


def abandonment_months(period_text, trigger_text=None):
    v = number(period_text) if re.fullmatch(r"\s*\d+(?:\.\d+)?\s*", str(period_text or "")) else None
    return v if v is not None else months_in(period_text) or months_in(trigger_text)


# ------------------------------------------------------------------------------------------- metes and bounds
FEET_PER = {"ft": 1.0, "foot": 1.0, "feet": 1.0, "vara": 100 / 36, "varas": 100 / 36, "vrs": 100 / 36, "vr": 100 / 36,
            "chain": 66.0, "chains": 66.0, "ch": 66.0, "chs": 66.0, "rod": 16.5, "rods": 16.5, "pole": 16.5,
            "poles": 16.5, "perch": 16.5, "perches": 16.5, "link": 0.66, "links": 0.66, "lk": 0.66, "lks": 0.66,
            "m": 3.280839895, "meter": 3.280839895, "meters": 3.280839895, "metre": 3.280839895, "metres": 3.280839895,
            "'": 1.0}
_BEARING = re.compile(
    r"\b(N(?:orth)?|S(?:outh)?)\.?\s*(\d{1,2}(?:\.\d+)?)\s*(?:°|º|deg(?:rees?)?\.?|d\b|-|\s)\s*"
    r"(?:(\d{1,2}(?:\.\d+)?)\s*(?:'|′|’|min(?:utes?)?\.?|m\b|-|\s)\s*)?"
    r"(?:(\d{1,2}(?:\.\d+)?)\s*(?:\"|″|”|''|sec(?:onds?)?\.?|s\b)?\s*)?"
    r"(E(?:ast)?|W(?:est)?)\b", re.I)
_CARDINAL = re.compile(r"^\s*(north|south|east|west|n|s|e|w)\.?\s*$", re.I)
_DIST = re.compile(r"([\d,]*\.?\d+)\s*(feet|foot|ft\.?|'|varas?|vrs\.?|vr\.?|chains?|chs?\.?|ch\.?|rods?|poles?|"
                   r"perch(?:es)?|links?|lks?\.?|lk\.?|meters?|metres?|m\b)", re.I)


def bearing_degrees(text):
    """Azimuth in degrees from north, clockwise: 'N 45°30' E' -> 45.5, 'S 10° W' -> 190."""
    t = str(text or "")
    m = _CARDINAL.match(t)
    if m:
        return {"n": 0.0, "e": 90.0, "s": 180.0, "w": 270.0}[m.group(1)[0].lower()]
    m = _BEARING.search(t)
    if not m:
        return None
    ang = float(m.group(2)) + float(m.group(3) or 0) / 60 + float(m.group(4) or 0) / 3600
    if ang > 90:
        return None
    ns, ew = m.group(1)[0].upper(), m.group(5)[0].upper()
    return {("N", "E"): ang, ("S", "E"): 180 - ang, ("S", "W"): 180 + ang, ("N", "W"): 360 - ang}[(ns, ew)]


def distance_feet(text):
    m = _DIST.search(str(text or ""))
    if not m:
        return None
    unit = m.group(2).lower().rstrip(".")
    return float(m.group(1).replace(",", "")) * FEET_PER.get(unit, 1.0)


def closure(calls: list[str]):
    """Traverse the calls. Returns None if there are too few, else a dict with misclosure (ft), perimeter (ft),
    precision (perimeter / misclosure), area in acres, and the calls that could not be read (curves, etc.)."""
    pts, x, y, perimeter, unread = [(0.0, 0.0)], 0.0, 0.0, 0.0, []
    for c in calls or []:
        parts = [p.strip() for p in str(c).split("|")]
        if not parts or parts[0].upper().startswith("POB") or not any(parts):
            continue
        if parts[0].upper().startswith("CURVE"):
            unread.append(str(c))
            continue
        b, d = bearing_degrees(parts[0]), distance_feet(parts[1] if len(parts) > 1 else parts[0])
        if b is None or d is None:
            unread.append(str(c))
            continue
        x += d * math.sin(math.radians(b))
        y += d * math.cos(math.radians(b))
        perimeter += d
        pts.append((x, y))
    if len(pts) < 4:
        return None
    mis = math.hypot(x, y)
    area = abs(sum(pts[k][0] * pts[k + 1][1] - pts[k + 1][0] * pts[k][1] for k in range(len(pts) - 1))
               + (pts[-1][0] * pts[0][1] - pts[0][0] * pts[-1][1])) / 2
    return {"misclosure_ft": round(mis, 2), "perimeter_ft": round(perimeter, 1),
            "precision": round(perimeter / mis) if mis > 1e-6 else None, "acres": round(area / 43560, 3),
            "calls": len(pts) - 1, "unread": unread}
