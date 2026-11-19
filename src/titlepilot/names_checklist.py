"""The title agent's names checklist ('SEGI Name Checklist.docx' and the like): every name researched, the
period searched (From / Through) and the indices searched, one column per index marked with an X.

Read from .docx, .xlsx or .csv. The first table whose header has a 'Name' column is used; repeated header rows
and blank rows are skipped. Index columns are mapped to the Names Examined columns of the SEG report.
"""
import csv
import re
import zipfile
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree

from .validate import norm_date

# Names Examined columns E..M of the SEG template, and the header words that mean each one.
INDICES = [
    ("VENDOR", ("grantor", "vendor")),
    ("VENDEE", ("grantee", "vendee")),
    ("MORTGAGES", ("mortgage", "deed of trust", "dt")),
    ("CIVIL SUITS", ("defendant", "plaintiff", "civil", "suit", "judgment", "lis pendens", "district clerk")),
    ("PROBATES", ("probate",)),
    ("OGMLs", ("ogml", "oil", "lease")),
    ("ASSUMED NAME", ("assumed", "dba")),
    ("MARRIAGE", ("marriage",)),
    ("UCC", ("ucc",)),
]
INDEX_NAMES = [n for n, _ in INDICES]
_NAME_HINT = re.compile(r"name", re.I)
_FILE_HINT = re.compile(r"name.*(check\s*list|checklist|list|researched|examined|searched)|names?\s*(researched|examined|run)", re.I)
_NO = {"", "-", "n", "no", "n/a", "na", "none", "0", "false"}


def index_for(header: str):
    """The Names Examined column for a checklist header ('Grantor' -> 'VENDOR'), or None."""
    h = re.sub(r"\s+", " ", (header or "").lower()).strip()
    for col, words in INDICES:
        if any(re.search(rf"\b{re.escape(w)}", h) for w in words):
            return col
    return None


def parse_indices(text) -> set:
    """'vendor; vendee; probates' or 'Grantor, Grantee' -> {'VENDOR', 'VENDEE', ...}."""
    out = set()
    for part in re.split(r"[;,/|]", str(text or "")):
        col = index_for(part) or next((n for n in INDEX_NAMES if n.lower() == part.strip().lower()), None)
        if col:
            out.add(col)
    return out


def find_name_checklist(folder) -> Path | None:
    folder = Path(folder)
    hits = [p for p in sorted(folder.rglob("*")) if p.is_file() and p.suffix.lower() in (".docx", ".xlsx", ".csv")
            and _FILE_HINT.search(p.stem) and "runsheet" not in p.stem.lower() and not p.name.startswith("~$")]
    return hits[0] if hits else None


def _docx_tables(path):
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    for tbl in root.iter(f"{{{ns['w']}}}tbl"):
        rows = []
        for tr in tbl.findall("w:tr", ns):
            cells = []
            for tc in tr.findall("w:tc", ns):
                paras = ["".join(t.text or "" for t in p.iter(f"{{{ns['w']}}}t")) for p in tc.findall(".//w:p", ns)]
                cells.append("\n".join(paras).strip())
            rows.append(cells)
        yield rows


def _xlsx_tables(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    for ws in wb:
        yield [[c for c in row] for row in ws.iter_rows(values_only=True)]


def _csv_tables(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        yield list(csv.reader(f))


def _cell_date(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v or "").strip()
    return norm_date(s) or (s or None)      # a bare year ('1918') is kept as written


def read_name_checklist(path) -> list[dict]:
    """[{'name', 'from', 'to', 'indices': {'VENDOR', ...}}] ('from'/'to' ISO dates, or text such as '1918')."""
    path = Path(path)
    tables = {".docx": _docx_tables, ".xlsx": _xlsx_tables, ".csv": _csv_tables}[path.suffix.lower()](path)
    for rows in tables:
        hdr_i = next((i for i, r in enumerate(rows[:10]) if any(_NAME_HINT.search(str(c or "")) for c in r)), None)
        if hdr_i is None:
            continue
        header = [re.sub(r"\s+", " ", str(c or "")).strip() for c in rows[hdr_i]]
        low = [h.lower() for h in header]
        name_c = next(i for i, h in enumerate(low) if "name" in h)
        from_c = next((i for i, h in enumerate(low) if h.startswith("from")), None)
        to_c = next((i for i, h in enumerate(low) if h.startswith(("through", "thru", "to"))), None)
        idx_cols = {i: index_for(h) for i, h in enumerate(header) if i not in (name_c, from_c, to_c) and index_for(h)}
        out = []
        for r in rows[hdr_i + 1:]:
            r = list(r) + [None] * (len(header) - len(r))
            name = re.sub(r"\s+", " ", str(r[name_c] or "")).strip()
            if not name or _NAME_HINT.search(name) and "research" in name.lower():
                continue                    # blank row or a repeated header
            out.append({"name": name,
                        "from": _cell_date(r[from_c]) if from_c is not None else None,
                        "to": _cell_date(r[to_c]) if to_c is not None else None,
                        "indices": {col for i, col in idx_cols.items()
                                    if str(r[i] or "").strip().lower() not in _NO}})
        return out
    return []
