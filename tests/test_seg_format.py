"""SEG report format details (owner address, legal tied to the vesting deed, title end date) and scoring
against SEG's finished COT workbook."""
import openpyxl
import pytest

from titlepilot.config import load_config
from titlepilot.db import DB
from titlepilot.pipeline import run_tract
from titlepilot.report import format_address, owner_block, tied_legal
from titlepilot.sample import TRACT, make_sample_tract
from titlepilot.score_seg import compare, to_markdown

from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "config"

VESTING = {"instrument_id": "Vol. 346, Page 115", "type": "special warranty deed", "file_name": "OPR_346_115.pdf",
           "execution_date": "2017-04-12", "filing_date": "2017-04-21",
           "grantors": ["PJK Family Limited Partnership"],
           "grantees": ["Mallard Land Development, LLC, a Texas limited liability company"],
           "legal": "Section 21, Block 36, Township 3 South, T&P Ry. Co. Survey, Abstract No. 401, Glasscock County, Texas"}


@pytest.mark.parametrize("raw, expected", [
    ("PO BOX 52267 MIDLAND TX 79710", "P.O. Box 52267\nMidland, Texas 79710"),
    ("PO BOX 52267 MIDLAND TX 79710 (from CAD)", "P.O. Box 52267\nMidland, Texas 79710"),
    ("500 W TEXAS AVE STE 1200 MIDLAND TX 79701-4203", "500 W Texas Ave Ste 1200\nMidland, Texas 79701-4203"),
    ("somewhere odd", "somewhere odd"),
])
def test_format_address(raw, expected):
    assert format_address(raw) == expected


def test_owner_block_uses_vesting_name_and_cad_address():
    chain = {"current_owner": ["Mallard Land Development, LLC"], "tax": {"cad_mailing_address": "PO BOX 52267 MIDLAND TX 79710"}}
    assert owner_block("Mallard Land Development, LLC", VESTING, chain) == (
        "Mallard Land Development, LLC,\na Texas limited liability company\nP.O. Box 52267\nMidland, Texas 79710")
    two = {"current_owner": ["A", "B"], "tax": {"cad_mailing_address": "PO BOX 1 MIDLAND TX 79710"}}
    assert owner_block("A", None, two) == "A"          # CAD address isn't assigned when there are several owners


def test_legal_description_tied_to_vesting_deed():
    text = tied_legal(VESTING["legal"], 642.3, VESTING, "Glasscock County")
    assert text.startswith("642.3 acres, more or less, of Section 21, Block 36")
    assert ("being more particularly described in that certain Special Warranty Deed dated April 12, 2017, from PJK "
            "Family Limited Partnership, as Grantor, to Mallard Land Development, LLC, a Texas limited liability company, "
            "as Grantee, recorded in Volume 346, Page 115, of the Official Public Records of Glasscock County, Texas") in text
    already = "642.3 acres, being more particularly described in that certain deed"
    assert tied_legal(already, 642.3, VESTING, "Glasscock") == already
    assert tied_legal("All of Section 21", None, None, None) == "All of Section 21"


@pytest.fixture
def sample_run(tmp_path):
    folder = make_sample_tract(tmp_path / "data")
    (folder / "tracts.csv").write_text(f"tract_id,acreage,certified_through\n{TRACT},160,02/07/2023\n")
    res = run_tract(folder / TRACT, load_config(CONFIG), DB(tmp_path / "runs.sqlite"), tmp_path / "out", mock=True,
                    log=lambda *_: None)
    return res


def test_report_end_date_is_certification_date(sample_run):
    own = openpyxl.load_workbook(sample_run["report"])["Ownership "]
    assert own["E17"].value.date().isoformat() == "2023-02-07"
    assert "under Instrument No. 1994003300, of the Official Public Records" in own["B22"].value


def _seg_workbook(path, rows, owner="Smith Ranch LLC\nP.O. Box 1\nSample, Texas 79000", end="2023-02-07"):
    from datetime import date
    wb = openpyxl.Workbook()
    sor = wb.active
    sor.title = "SOR"
    sor["H5"], sor["I5"] = "Gross Acres:", 160
    sor["A8"], sor["A9"] = "LEGAL DESCRIPTION", "160 acres ... recorded under Instrument No. 1994003300 ..."
    for c, v in zip("ACDEH", ["Surface Ownership", "Fractional Interest", "Decimal Interest", "Net\nAcres", "Property ID"]):
        sor[f"{c}12"] = v
    sor["A13"], sor["C13"], sor["D13"], sor["E13"], sor["H13"] = owner, 1, 1, 160, 4242
    sor["A14"] = "TOTAL:"
    sor["E23"], sor["G23"] = "Title Start Date:", date(1978, 4, 12)
    sor["E24"], sor["G24"] = "Title End Date:", date.fromisoformat(end)
    cot = wb.create_sheet("SURFACE-COT")
    for c, v in zip("ABCDEFG", ["NO.", "VOL/ PAGE", "TYPE", "DATE", "DATE FILED", "GRANTOR", "GRANTEE"]):
        cot[f"{c}4"] = v
    for n, r in enumerate(rows, 6):
        for c, v in zip("ABCDEFG", r):
            cot[f"{c}{n}"] = v
    wb.save(path)
    return path


def test_score_against_seg_cot(sample_run, tmp_path):
    from datetime import date
    seg = _seg_workbook(tmp_path / "seg.xlsx", [
        [1, "1978001001", "Warranty Deed", None, date(1978, 4, 12), "Mary Doe", "John A. Smith"],
        [2, "1994003300", "Warranty Deed", None, date(1994, 8, 15), "John A. Smith", "Smith Ranch LLC"],
        [3, "Cause No. 99", "Probate", "1/1/1990\nDate of Death", None, "Estate of Nobody", "The Public"],
    ])
    res = compare(sample_run["report"], seg)
    sm = res["summary"]
    assert (sm["seg_instruments"], sm["found"], sm["missed"]) == (3, 2, 1)
    assert sm["dates_agree"] == 2
    checks = {c["item"]: c["match"] for c in res["checks"]}
    assert checks["Current surface owner(s)"] and checks["Gross acres"] and checks["Vesting deed"]
    assert checks["Title end date"] and checks["Legal description ties to vesting deed"]
    assert not checks["Owner address"]                     # sample tract has no CAD printout
    assert any("2005" in o["ref"] for o in res["not_on_seg"])      # the pipeline easement isn't on the COT
    md = to_markdown(res)
    assert "found: 2 of 3" in md and "Cause No. 99" in md


def test_names_examined_has_from_and_to_for_every_name(sample_run):
    ws = openpyxl.load_workbook(sample_run["report"])["Names Examined"]
    rows = [r for r in ws.iter_rows(min_row=5, values_only=True) if r[1]]
    assert rows and all(r[2] and r[3] for r in rows), rows


@pytest.mark.parametrize("a, b, same", [
    ("Kay Wrage Gunn", "Kay Lee Wrage Gunn", True),
    ("John Bridge Gunn, Jr.", "John B. Gunn, Jr.", True),
    ("John B. Gunn", "John B. Gunn, Jr.", False),
    ("Gunn, Robert Niels", "Robert Niels Gunn", True),
    ("Mallard Land Development, LLC, a Texas limited liability company", "Mallard Land Development, LLC", True),
    ("Perry R. Horton, Co-Trustee", "Perry R. Horton, Trustee", True),
    ("Francis Shannon", "Frances Shannon", True),
    ("Lee Gunn", "Kay Gunn", False),
])
def test_names_examined_merges_name_variants(a, b, same):
    from titlepilot.report import _same_person
    assert _same_person(a, b) is same


def _docx(path, rows):
    import zipfile
    cell = lambda t: f'<w:tc><w:p><w:r><w:t>{t}</w:t></w:r></w:p></w:tc>'
    body = "".join("<w:tr>" + "".join(cell(c) for c in r) + "</w:tr>" for r in rows)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body><w:tbl>{body}</w:tbl></w:body></w:document>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)
    return path


def test_read_name_checklist_docx(tmp_path):
    from titlepilot.names_checklist import find_name_checklist, read_name_checklist
    hdr = ["Names Researched (Last Name First)", "From", "Through", "Grantor", "Probate", "Defendant"]
    _docx(tmp_path / "SEGI Name Checklist.docx", [hdr, ["Chesser, D. A.", "11/22/1906", "4/10/1937", "X", "", ""],
                                                  hdr, ["Parrish, Joe", "1918", "2/15/1937", "X", "X", ""],
                                                  ["", "", "", "", "", ""]])
    path = find_name_checklist(tmp_path)
    assert path.name == "SEGI Name Checklist.docx"
    rows = read_name_checklist(path)
    assert rows == [{"name": "Chesser, D. A.", "from": "1906-11-22", "to": "1937-04-10", "indices": {"VENDOR"}},
                    {"name": "Parrish, Joe", "from": "1918", "to": "1937-02-15", "indices": {"VENDOR", "PROBATES"}}]


def test_names_examined_marks_indices_from_checklist(tmp_path):
    folder = make_sample_tract(tmp_path / "data")
    (folder / TRACT / "Names Checklist.csv").write_text(
        "Name,From,Through,Grantor,Grantee,Deed of Trust,Probate,Defendant,UCC\n"
        "\"Smith, John A.\",4/12/1978,8/15/1994,X,X,X,,X,\n"
        "Smith Ranch LLC,8/15/1994,2/7/2023,X,X,X,X,X,X\n")
    res = run_tract(folder / TRACT, load_config(CONFIG), DB(tmp_path / "runs.sqlite"), tmp_path / "out", mock=True,
                    log=lambda *_: None)
    ws = openpyxl.load_workbook(res["report"])["Names Examined"]
    header = [ws.cell(3, c).value for c in range(2, 14)]
    rows = {r[1]: dict(zip(header, r[1:13])) for r in ws.iter_rows(min_row=5, values_only=True) if r[1]}
    john = next(v for k, v in rows.items() if "John A. Smith" in k)
    assert [john[k] for k in ("VENDOR", "VENDEE", "MORTGAGES", "CIVIL SUITS", "PROBATES", "UCC")] == ["X", "X", "X", "X", None, None]
    assert john["TO"].date().isoformat() == "1994-08-15"
    ranch = rows["Smith Ranch LLC"]
    assert all(ranch[k] == "X" for k in ("VENDOR", "VENDEE", "MORTGAGES", "CIVIL SUITS", "PROBATES", "UCC"))
    mary = next(v for k, v in rows.items() if "Mary Doe" in k)       # not on the checklist: no index marks
    assert not any(mary[k] for k in ("VENDOR", "VENDEE", "PROBATES"))


def test_tract_wide_indices_when_no_checklist(tmp_path):
    folder = make_sample_tract(tmp_path / "data")
    (folder / "tracts.csv").write_text(f"tract_id,acreage,indices_examined\n{TRACT},160,vendor; vendee; probates\n")
    res = run_tract(folder / TRACT, load_config(CONFIG), DB(tmp_path / "runs.sqlite"), tmp_path / "out", mock=True,
                    log=lambda *_: None)
    ws = openpyxl.load_workbook(res["report"])["Names Examined"]
    rows = [r for r in ws.iter_rows(min_row=5, values_only=True) if r[1]]
    assert rows and all(r[4] == "X" and r[5] == "X" and r[8] == "X" and r[6] is None for r in rows)
