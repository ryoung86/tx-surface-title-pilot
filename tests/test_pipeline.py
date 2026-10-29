import sqlite3
from pathlib import Path

import pytest

from titlepilot.config import load_config
from titlepilot.db import DB
from titlepilot.intake import check_package, find_intake_file, read_intake
from titlepilot.pipeline import run_tract
from titlepilot.sample import TRACT, make_sample_tract
from titlepilot.score import score

CONFIG = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def sample(tmp_path):
    return make_sample_tract(tmp_path / "data")


def test_end_to_end_mock(sample, tmp_path):
    db = DB(tmp_path / "runs.sqlite")
    res = run_tract(sample / TRACT, load_config(CONFIG), db, tmp_path / "out", mock=True, log=lambda *_: None)

    assert res["instruments"] == 6
    assert res["chain"]["current_owner"] == ["Smith Ranch LLC"]
    status = {e["instrument_id"]: e["status"] for e in res["chain"]["encumbrances"]}
    assert status["OPR 1980-000123"] == "released"
    assert status["OPR 2001-004410"] == "open"

    rules = {f.rule_id for f in res["flags"]}
    assert {"TS03", "TS07", "R13"} <= rules
    assert not rules & {"R01", "R06", "R12", "PK01", "TS06"}
    assert res["review"].exists()

    s = score(db, sample / "answer_key")
    assert s["current_owner_correct"] == 1.0
    assert s["encumbrances_found"] == 1.0
    assert s["encumbrance_status_correct"] == 1.0
    assert s["known_issues_flagged"] == 1.0


def test_package_check_finds_missing_and_extra_files(sample):
    folder = sample / TRACT
    (folder / "SAMPLE_2005-000777.pdf").unlink()
    (folder / "stray_scan.pdf").write_bytes(b"%PDF-1.4")
    problems = check_package(folder, read_intake(find_intake_file(folder)))
    assert "Listed but not uploaded: SAMPLE_2005-000777.pdf" in problems
    assert "Uploaded but not on the Document List: stray_scan.pdf" in problems


def test_incomplete_package_is_not_processed(sample, tmp_path):
    (sample / TRACT / "SAMPLE_2005-000777.pdf").unlink()
    db = DB(tmp_path / "runs.sqlite")
    assert run_tract(sample / TRACT, load_config(CONFIG), db, tmp_path / "out", mock=True, log=lambda *_: None) is None


def test_history_is_append_only(sample, tmp_path):
    db = DB(tmp_path / "runs.sqlite")
    run_tract(sample / TRACT, load_config(CONFIG), db, tmp_path / "out", mock=True, log=lambda *_: None)
    with pytest.raises(sqlite3.DatabaseError):
        db.conn.execute("UPDATE runs SET tract_id = 'x'")
    with pytest.raises(sqlite3.DatabaseError):
        db.conn.execute("DELETE FROM flags")


def test_rerun_links_to_previous_run(sample, tmp_path):
    db = DB(tmp_path / "runs.sqlite")
    cfg = load_config(CONFIG)
    first = run_tract(sample / TRACT, cfg, db, tmp_path / "out", mock=True, log=lambda *_: None)
    second = run_tract(sample / TRACT, cfg, db, tmp_path / "out", mock=True, log=lambda *_: None)
    prev = db.conn.execute("SELECT previous_run_id FROM runs WHERE id = ?", (second["run_id"],)).fetchone()[0]
    assert prev == first["run_id"]


def test_schema_fits_structured_output_rules():
    """Strict shape: additionalProperties false, all properties required, no nullable types."""
    schema = load_config(CONFIG).schema

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(node.get("properties", {}))
            assert not isinstance(node.get("type"), list), "nullable/union types count toward a small limit"
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(schema)


def test_sample_mock_records_match_schema(sample):
    import json
    from titlepilot.validate import schema_errors
    schema = load_config(CONFIG).schema
    for f in (sample / TRACT).glob("*.mock.json"):
        assert schema_errors(json.loads(f.read_text()), schema) == [], f.name


def test_parse_json_tolerates_fences_and_text():
    from titlepilot.extract import ExtractionError, parse_json
    assert parse_json('Here it is:\n```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ExtractionError):
        parse_json("no json here")


@pytest.mark.parametrize("raw,key", [
    ("Sample County, Texas OPR 2001-004410", "2001-4410"),
    ("OPR 2001-4410", "2001-4410"),
    ("2001-004410", "2001-4410"),
    ("Vol. 412, p. 88", "V412P88"),
    ("Volume 412 Page 088", "V412P88"),
    ("CABINET A SLIDE 12", "CABINETASLIDE12"),
])
def test_recording_reference_keys(raw, key):
    from titlepilot.validate import norm_ref
    assert norm_ref(raw) == key


def test_sample_pdfs_show_release_reference_and_exception(sample):
    import pypdfium2 as pdfium
    # The fake PDFs are images, so check the mock data that drew them instead of OCR.
    import json
    rel = json.loads((sample / TRACT / "SAMPLE_2004-002210.pdf.mock.json").read_text())
    deed = json.loads((sample / TRACT / "SAMPLE_1994-003300.pdf.mock.json").read_text())
    assert rel["refers_to"]["value"] == ["OPR 1980-000123"]
    assert "SAVE AND EXCEPT" in deed["mineral_exception"]["value"]
    assert len(pdfium.PdfDocument(str(sample / TRACT / "SAMPLE_1994-003300.pdf"))) == 1


@pytest.mark.parametrize("raw,category", [
    ("easement/row", "easement"), ("Easement / ROW", "easement"), ("Pipeline Right-of-Way Easement", "easement"),
    ("judgment", "judgment"), ("Abstract of Judgment", "judgment"), ("Lis Pendens", "judgment"),
    ("Deed of Trust", "lien"), ("Vendor's Lien", "lien"), ("Release of Lien", "release"), ("Partial Release", "release"),
    ("Warranty Deed", "conveyance"), ("Special Warranty Deed", "conveyance"), ("Quitclaim Deed", "conveyance"),
    ("Affidavit of Heirship", "conveyance"), ("Deed", "conveyance"), ("Plat", "other"), ("", "unknown"),
])
def test_instrument_type_categories(raw, category):
    from titlepilot.chain import classify
    from titlepilot.validate import norm_type
    assert classify(norm_type(raw)) == category


def test_live_run_4_types_and_holders():
    """Reproduces what the first live run returned: 'easement/row' was dropped from
    encumbrances and the judgment showed the debtor as holder."""
    from titlepilot.chain import build_chain
    from titlepilot.intake import DocRow
    from titlepilot.validate import normalize

    def rec(iid, itype, date, grantor, grantee, refers=()):
        f = lambda v: {"value": v, "source_text": "", "confidence": "high"}
        p = lambda n: [{"name": n, "marital_status": "", "spouse": "", "capacity": ""}]
        return normalize({"instrument_id": f(iid), "instrument_type": f([itype]), "filing_date": f(date),
                          "grantors": f(p(grantor)), "grantees": f(p(grantee)), "refers_to": f(list(refers))},
                         DocRow(file_name=iid + ".pdf"))

    chain = build_chain([
        rec("OPR 1978-001001", "warranty deed", "04/12/1978", "Mary Doe", "John A. Smith"),
        rec("Sample County, OPR 1980-000123", "deed of trust", "02/01/1980", "John A. Smith", "First State Bank"),
        rec("OPR 1994-003300", "warranty deed", "08/15/1994", "John A. Smith", "Smith Ranch LLC"),
        rec("Sample County, Texas OPR 2001-004410", "judgment", "06/02/2001", "Acme Supply Co.", "John A. Smith"),
        rec("OPR 2004-002210", "release", "05/10/2004", "First State Bank", "John A. Smith", ["OPR 1980-000123"]),
        rec("Sample County OPR 2005-000777", "easement/row", "09/20/2005", "Smith Ranch LLC", "Pecos Pipeline Co."),
    ])
    enc = {e["instrument_id"]: e for e in chain["encumbrances"]}
    assert enc["Sample County OPR 2005-000777"]["holder"] == ["Pecos Pipeline Co."]
    assert enc["Sample County, Texas OPR 2001-004410"]["holder"] == ["Acme Supply Co."]
    assert enc["Sample County, OPR 1980-000123"]["holder"] == ["First State Bank"]
    assert enc["Sample County, OPR 1980-000123"]["status"] == "released"
    assert chain["current_owner"] == ["Smith Ranch LLC"]
    assert chain["unclassified"] == []


def _run(dest, tmp_path, mode):
    root = make_sample_tract(dest, mode)
    db = DB(tmp_path / f"{mode}.sqlite")
    logs = []
    res = run_tract(root / TRACT, load_config(CONFIG), db, tmp_path / "out", mock=True, log=logs.append)
    return root, db, res, logs


def test_runsheet_package(tmp_path):
    root, db, res, logs = _run(tmp_path / "rs", tmp_path, "runsheet")
    assert any("agent runsheet" in line for line in logs)
    assert res["chain"]["current_owner"] == ["Smith Ranch LLC"]
    rules = {f.rule_id for f in res["flags"]}
    assert {"TS03", "TS07"} <= rules
    assert not rules & {"PK01", "PK02", "PK03"}      # runsheet matches the uploads; owners were searched
    assert score(db, root / "answer_key")["current_owner_correct"] == 1.0


def test_runsheet_catches_missing_upload(tmp_path):
    root = make_sample_tract(tmp_path / "rs2", "runsheet")
    (root / TRACT / "SAMPLE_2005-000777.pdf").unlink()   # agent listed it but didn't upload it
    res = run_tract(root / TRACT, load_config(CONFIG), DB(tmp_path / "x.sqlite"), tmp_path / "out",
                    mock=True, log=lambda *_: None)
    assert res is not None                              # still processed; the gap becomes a flag
    pk02 = [f for f in res["flags"] if f.rule_id == "PK02"]
    assert len(pk02) == 1 and "2005-000777" in pk02[0].message


def test_runsheet_catches_extra_upload(tmp_path):
    import json, shutil
    root = make_sample_tract(tmp_path / "rs3", "runsheet")
    t = root / TRACT
    rs = json.loads((t / f"{TRACT}_runsheet.xlsx.mock.json").read_text())
    rs["entries"] = [e for e in rs["entries"] if "2005-000777" not in e["recording_ref"]]
    (t / f"{TRACT}_runsheet.xlsx.mock.json").write_text(json.dumps(rs))
    res = run_tract(t, load_config(CONFIG), DB(tmp_path / "y.sqlite"), tmp_path / "out", mock=True, log=lambda *_: None)
    pk03 = [f for f in res["flags"] if f.rule_id == "PK03"]
    assert [f.instrument_id for f in pk03] == ["OPR 2005-000777"]


def test_folder_only_package(tmp_path):
    root, db, res, logs = _run(tmp_path / "fo", tmp_path, "folder")
    assert any("folder only" in line for line in logs)
    assert res["instruments"] == 6 and res["chain"]["current_owner"] == ["Smith Ranch LLC"]
    pk01 = [f for f in res["flags"] if f.rule_id == "PK01"]
    assert len(pk01) == 1 and "No record of owner name searches" in pk01[0].message
    assert "John A. Smith" in pk01[0].message


def test_runsheet_file_is_not_treated_as_a_document(tmp_path):
    from titlepilot.intake import load_package
    root = make_sample_tract(tmp_path / "rs4", "runsheet")
    (root / TRACT / "agent index.pdf").write_bytes(b"%PDF-1.4")   # a PDF runsheet/index
    pkg = load_package(root / TRACT)
    names = [d.file_name for d in pkg.documents]
    assert "agent index.pdf" not in names and len(names) == 6


def test_runsheet_text_reads_agent_layout(tmp_path):
    from titlepilot.intake import runsheet_text
    root = make_sample_tract(tmp_path / "rs5", "runsheet")
    text = runsheet_text(root / TRACT / f"{TRACT}_runsheet.xlsx")
    assert "Names searched" in text and "OPR 2001-004410" in text


def _nest(tract):
    """Move the sample documents into subfolders, the way an agent might organize them."""
    import shutil
    for f in sorted(tract.glob("SAMPLE_*")):
        sub = tract / ("Deeds" if any(k in f.name for k in ("1978", "1994")) else "Liens and Easements")
        if "2001" in f.name:
            sub = sub / "Name searches"
        sub.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), sub / f.name)


@pytest.mark.parametrize("mode", ["intake", "runsheet", "folder"])
def test_documents_in_subfolders(tmp_path, mode):
    root = make_sample_tract(tmp_path / mode, mode)
    _nest(root / TRACT)
    assert not list((root / TRACT).glob("*.pdf"))          # nothing left at the top level
    logs = []
    res = run_tract(root / TRACT, load_config(CONFIG), DB(tmp_path / "s.sqlite"), tmp_path / "out",
                    mock=True, log=logs.append)
    assert res is not None, logs
    assert res["instruments"] == 6
    assert res["chain"]["current_owner"] == ["Smith Ranch LLC"]
    files = {i["file_name"] for i in res["chain"]["instruments"]}
    assert "Liens and Easements/Name searches/SAMPLE_2001-004410.pdf" in files
    assert not {f.rule_id for f in res["flags"]} & {"PK02", "PK03"}
