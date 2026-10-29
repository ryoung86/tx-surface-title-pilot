"""Work out what the title agent delivered for a tract, and check it before extraction.

A tract folder can come in three ways, checked in this order:
  1. intake form    - the Intake Form / Document List workbook (fullest checks)
  2. agent runsheet - the agent's own runsheet or index (a file with "runsheet" or
                      "index" in its name); Claude reads it after the documents
  3. folder only    - just the documents; the tract ID is the folder name
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import openpyxl
from .fuzzy import token_sort_ratio

DOC_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}

# Intake Form labels (column A) -> attribute. Matched by "starts with".
LABELS = {
    "tract id": "tract_id",
    "project / client": "project",
    "county": "county",
    "legal description": "legal_description",
    "search period": "search_period",
    "owners searched by name": "owners",
    "records searched": "records_searched",
    "title agent": "agent",
    "delivery date": "delivery_date",
    "agent notes": "notes",
}


def _clean(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    if not s or s.lower().startswith("example"):
        return None
    return s


@dataclass
class DocRow:
    file_name: str
    instrument_type: str | None = None
    recording_ref: str | None = None
    filing_date: str | None = None
    grantor: str | None = None
    grantee: str | None = None
    found_by: str | None = None
    owner_searched: str | None = None
    notes: str | None = None


@dataclass
class Intake:
    tract_id: str | None = None
    project: str | None = None
    county: str | None = None
    legal_description: str | None = None
    search_period: str | None = None
    owners: list = field(default_factory=list)
    records_searched: str | None = None
    agent: str | None = None
    delivery_date: str | None = None
    notes: str | None = None
    documents: list = field(default_factory=list)
    source: Path | None = None
    mode: str = "intake form"
    runsheet_path: Path | None = None
    runsheet_entries: list = field(default_factory=list)


RUNSHEET_WORDS = ("runsheet", "run sheet", "run_sheet", "run-sheet", "index")
RUNSHEET_EXTS = DOC_EXTS | {".xlsx", ".csv"}


def is_runsheet(p: Path) -> bool:
    return p.suffix.lower() in RUNSHEET_EXTS and any(w in p.stem.lower() for w in RUNSHEET_WORDS)


def find_runsheet(folder) -> Path | None:
    found = sorted(p for p in Path(folder).iterdir() if p.is_file() and is_runsheet(p) and not p.name.startswith("~$"))
    return found[0] if found else None


def document_files(folder) -> list[Path]:
    return sorted(p for p in Path(folder).iterdir()
                  if p.is_file() and p.suffix.lower() in DOC_EXTS and not is_runsheet(p))


def load_package(folder) -> Intake:
    """Intake form if present, else the agent's runsheet, else the folder's documents."""
    folder = Path(folder)
    path = find_intake_file(folder)
    if path:
        return read_intake(path)
    runsheet = find_runsheet(folder)
    return Intake(tract_id=folder.name, mode="agent runsheet" if runsheet else "folder only",
                  runsheet_path=runsheet, documents=[DocRow(file_name=p.name) for p in document_files(folder)])


def runsheet_text(path, max_rows=3000) -> str:
    """Spreadsheet or CSV runsheet as plain text for Claude to read."""
    path = Path(path)
    lines = []
    if path.suffix.lower() == ".csv":
        import csv
        with path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
            lines = [" | ".join(r) for r in csv.reader(f)]
    else:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        for ws in wb.worksheets:
            lines.append(f"# Sheet: {ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                if any(cells):
                    lines.append(" | ".join(cells).rstrip(" |"))
    return "\n".join(lines[:max_rows])


def find_intake_file(folder) -> Path | None:
    for p in sorted(Path(folder).glob("*.xlsx")):
        if p.name.startswith("~$"):
            continue
        try:
            wb = openpyxl.load_workbook(p, read_only=True)
        except Exception:
            continue
        if "Intake Form" in wb.sheetnames and "Document List" in wb.sheetnames:
            return p
    return None


def read_intake(path) -> Intake:
    wb = openpyxl.load_workbook(path, data_only=True)
    intake = Intake(source=Path(path))

    for row in wb["Intake Form"].iter_rows(values_only=True):
        if not row or row[0] is None or len(row) < 2:
            continue
        label = str(row[0]).strip().lower()
        for key, attr in LABELS.items():
            if label.startswith(key):
                value = _clean(row[1])
                if attr == "owners":
                    owners = [re.sub(r"\(.*?\)", "", o).strip() for o in (value or "").split(";")]
                    intake.owners = [o for o in owners if o]
                else:
                    setattr(intake, attr, value)
                break

    ws = wb["Document List"]
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not _clean(row[0]):
            continue
        if row[8] and str(row[8]).strip().lower().startswith("example"):
            continue  # template example rows
        intake.documents.append(DocRow(*[_clean(v) for v in row[:9]]))
    return intake


def check_package(folder, intake: Intake) -> list[str]:
    """Problems that should go back to the title agent before extraction.

    Without an intake form there is nothing to compare against yet; the runsheet
    is compared after extraction by rules PK02 and PK03.
    """
    folder = Path(folder)
    problems = []
    if intake.mode != "intake form":
        if not intake.documents:
            problems.append("No documents (PDF, TIFF or image files) found in the folder")
        return problems
    if not intake.tract_id:
        problems.append("Intake form has no tract ID")
    elif folder.name != intake.tract_id:
        problems.append(f"Folder name '{folder.name}' does not match tract ID '{intake.tract_id}'")
    if not intake.documents:
        problems.append("Document List is empty")

    files = {p.name for p in document_files(folder)}
    listed = {d.file_name for d in intake.documents}
    problems += [f"Listed but not uploaded: {f}" for f in sorted(listed - files)]
    problems += [f"Uploaded but not on the Document List: {f}" for f in sorted(files - listed)]

    if not intake.owners:
        problems.append("No owners listed under 'Owners searched by name'")
    for d in intake.documents:
        if (d.found_by or "").lower() != "name search":
            continue
        if not d.owner_searched:
            problems.append(f"{d.file_name}: name search with no owner name")
        elif not any(token_sort_ratio(d.owner_searched.lower(), o.lower()) >= 85 for o in intake.owners):
            problems.append(f"{d.file_name}: owner '{d.owner_searched}' is not in the intake owners list")
    return problems
