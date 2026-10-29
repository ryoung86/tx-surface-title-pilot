"""Name similarity (0-100). Uses rapidfuzz when installed, else a difflib fallback."""
try:
    from rapidfuzz.fuzz import token_sort_ratio
except ImportError:  # pragma: no cover
    from difflib import SequenceMatcher

    def token_sort_ratio(a: str, b: str) -> float:
        a, b = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
        return SequenceMatcher(None, a, b).ratio() * 100
