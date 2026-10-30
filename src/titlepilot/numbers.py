"""Checks that catch misread numbers (a 7 read as a 1, a dropped decimal).

Two checks that don't need a second reading:
1. Shares must add up. When a deed splits the interest among grantees ("each receiving 11.625% ...,
   23.25% ..., 7% ..."), the shares should total 100% of what is conveyed.
2. Words must match digits. Deeds usually write a number twice, "fifteen (15) years",
   "one twenty-fifth (1/25)", "FIVE (5%)". If the words and the digits disagree, one was misread.
"""
import re
from fractions import Fraction

UNITS = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
TENS = {w: 10 * n for n, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
ORDINALS = {"half": 2, "halves": 2, "third": 3, "thirds": 3, "fourth": 4, "fourths": 4, "quarter": 4, "fifth": 5,
            "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12,
            "sixteenth": 16, "twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50, "sixtieth": 60,
            "hundredth": 100, "hundredths": 100, "thousandth": 1000}


NUMBER_WORDS = set(UNITS) | set(TENS) | set(ORDINALS) | {"hundred", "thousand", "and", "half", "haif", "one-half"}


def _ord_word(w):
    """'fifth' -> 5, 'twentieth' -> 20, 'hundredths' -> 100; None if not an ordinal."""
    if w in ORDINALS:
        return ORDINALS[w]
    w = w[:-1] if w.endswith("ths") else w
    if w.endswith("ieth"):
        return _cardinal([w[:-4] + "y"])
    if w.endswith("th"):
        stem = {"fif": "five", "eigh": "eight", "nin": "nine", "twelf": "twelve"}.get(w[:-2], w[:-2])
        return _cardinal([stem])
    return None


def _ord_value(tokens):
    base = _ord_word(tokens[-1])
    if base is None:
        return None
    if len(tokens) == 1:
        return base
    lead = _cardinal(tokens[:-1])            # 'twenty fifth' -> 25
    if lead == 1 and base in (100, 1000):     # 'one hundredths' -> 100
        return base
    return lead + base if lead and base < 10 else None


def words_to_number(text: str):
    """'twenty-five' -> 25, 'one twenty-fifth' -> 1/25, 'three and one-half' -> 7/2, 'one/half' -> 1/2.
    None if the text isn't (only) a number written in words."""
    t = re.sub(r"[-/]", " ", str(text).lower().replace("&", " and ")).strip()
    if " and " in f" {t} ":
        left, right = re.split(r"\band\b", t, maxsplit=1)
        a, b = _cardinal(left.split()), words_to_number(right)
        if a is not None and isinstance(b, Fraction) and b < 1:
            return a + b
        return None
    tokens = t.split()
    if not tokens:
        return None
    if _ord_word(tokens[-1]) is not None and tokens[-1] not in ("hundred", "thousand"):
        for j in range(1, len(tokens)):
            num, den = _cardinal(tokens[:j]), _ord_value(tokens[j:])
            if num is not None and den:
                return Fraction(num, den)
        return None
    return _cardinal(tokens)


def _cardinal(words):
    if not words:
        return None
    total, cur = 0, 0
    for w in words:
        if w in UNITS:
            cur += UNITS[w]
        elif w in TENS:
            cur += TENS[w]
        elif w == "hundred":
            cur = (cur or 1) * 100
        elif w == "thousand":
            total += (cur or 1) * 1000
            cur = 0
        else:
            return None
    return total + cur


def digits_value(text: str):
    t = text.replace(",", "").strip().rstrip("%").strip()
    if "/" in t:
        a, b = t.split("/", 1)
        try:
            return Fraction(a.strip()) / Fraction(b.strip().rstrip("ths").strip())
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return Fraction(t)
    except ValueError:
        return None


_PAIR = re.compile(r"((?:[A-Za-z]+[\s\-/&]+){0,14}[A-Za-z]+)\s*\(\s*(\d[\d,]*(?:\.\d+)?(?:\s*/\s*\d+(?:ths?)?)?)\s*%?\s*\)")


def word_digit_mismatches(text: str) -> list[str]:
    """'FIVE (5%)' is fine; 'SEVEN (1%)' is reported."""
    out = []
    for m in _PAIR.finditer(text or ""):
        words, digits = m.group(1), m.group(2)
        d = digits_value(digits)
        if d is None:
            continue
        tokens = re.sub(r"[-/]", " ", words.lower().replace("&", " and ")).split()
        # the run of number words right before the parenthesis; if a number word sits just before the run
        # we may have only part of the number, so skip rather than report a false mismatch
        k = 0
        while k < len(tokens) and (tokens[-k - 1] in NUMBER_WORDS or _ord_word(tokens[-k - 1])):
            k += 1
        if not k:
            continue
        if k == len(tokens):        # the capture starts inside the number: check the word before it
            before = re.findall(r"[A-Za-z]+|[-&/]", (text or "")[max(0, m.start(1) - 40):m.start(1)].lower())
            if len(tokens) >= 15 or (before and (before[-1] in NUMBER_WORDS or before[-1] in "-&/"
                                                  or _ord_word(before[-1]))):
                continue
        w = words_to_number(" ".join(tokens[-k:]))
        if w is None:
            continue
        if Fraction(w) != d and not (d < 1 and Fraction(w) == d * 100):
            out.append(f"'{m.group(0).strip()}': the words say {_fmt(w)} but the digits say {_fmt(d)}")
    return out


def _fmt(x):
    x = Fraction(x)
    return str(x.numerator) if x.denominator == 1 else (f"{float(x):g}" if x.denominator in (2, 4, 5, 8, 10, 100, 1000) else str(x))


_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def share_value(text: str):
    """A party's share as a percent of the interest conveyed: '11.625%' -> 11.625, 'an undivided 1/2' -> 50."""
    t = str(text or "")
    m = _PCT.search(t)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+)\s*/\s*(\d+)", t)
    if m and int(m.group(2)):
        return 100 * int(m.group(1)) / int(m.group(2))
    w = words_to_number(re.sub(r"\b(undivided|interest|an|of|the)\b", " ", t.lower()))
    if isinstance(w, Fraction) and 0 < w <= 1:
        return float(w) * 100
    return None


def shares_problem(parties) -> str | None:
    """Shares stated for two or more grantees that don't total 100%."""
    shares = [(p.get("name"), share_value(p.get("share"))) for p in parties or [] if isinstance(p, dict) and p.get("share")]
    if len(shares) < 2 or any(v is None for _, v in shares):
        return None
    total = sum(v for _, v in shares)
    if abs(total - 100) > 0.01:
        listed = ", ".join(f"{n} {v:g}%" for n, v in shares)
        return f"Grantee shares add to {total:g}%, not 100% ({listed}): check for a misread number"
    return None
