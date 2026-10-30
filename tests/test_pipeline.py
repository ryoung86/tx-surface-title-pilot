import pathlib
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
    # Liberty County formats from the CCS.TX.SDY.0143 title
    ("708/1", "V708P1"), ("Esmt. 708/1", "V708P1"), ("Vol. 708, Pg. 1", "V708P1"),
    ("2012012111", "2012-12111"), ("OPR 2012012111", "2012-12111"), ("2012-012111", "2012-12111"),
    ("1104/334", "V1104P334"),
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
    ("UCC Financing Statement", "lien"), ("UCC", "lien"), ("Gift Deed", "conveyance"),
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


def test_ucc_holder_is_secured_party():
    from titlepilot.chain import holder
    ucc = {"type": "ucc financing statement", "category": "lien", "grantors": ["Alvaro Banda"], "grantees": ["ISPC"]}
    assert holder(ucc) == ["ISPC"]


# --- CCS.TX.SDY.0143 (Liberty County): first real tract -------------------------------------------
# Built from the verified SEG title (6/17/2025). Reproduces what went wrong on the first live run:
# out-sale picked as current owner, gift deeds as 'other', 1965 WD listed as an easement,
# appraisal card / CAD map in the chain, name runsheet producing PK03 noise and missing 2018003815.

def _real(iid, itype, date, grantors, grantees, acres=None, interest="", refers=(), kind="recorded instrument",
          file=None):
    from titlepilot.intake import DocRow
    from titlepilot.validate import normalize
    f = lambda v: {"value": v, "source_text": "", "confidence": "high"}
    p = lambda names: [{"name": n, "marital_status": "", "spouse": "", "capacity": ""} for n in names]
    rec = {"instrument_id": f(iid), "instrument_type": f(itype), "filing_date": f(date),
           "grantors": f(p(grantors)), "grantees": f(p(grantees)), "refers_to": f(list(refers)),
           "acreage": f(acres or ""), "surface_interest": f(interest), "document_kind": f(kind),
           "county": f("Liberty")}
    return normalize(rec, DocRow(file_name=file or f"{iid}.pdf"))


DUN, GRACE, JR3 = "J. A. Dunagan", "Grace Willis Dunagan", "James A. Dunagan, III"
GIFTS = [("640/91", "1969-01-14", "5%"), ("652/76", "1969-10-09", "5%"), ("667/443", "1970-09-03", "5%"),
         ("674/460", "1971-02-10", "10%"), ("692/843", "1972-03-02", "5%"), ("716/233", "1973-03-12", "5%"),
         ("734/911", "1974-03-26", "5%"), ("751/192", "1975-02-10", "5%"), ("770/903", "1976-02-24", "5%"),
         ("795/435", "1977-03-22", "5%"), ("930/580", "1981-12-22", "14%"), ("952/267", "1982-01-14", "7%"),
         ("1002/385", "1983-12-21", "6%"), ("1004/199", "1984-01-11", "6%"), ("1103/60", "1985-12-31", "6%"),
         ("1104/334", "1996-01-09", "5%"), ("1241/580", "1988-08-04", "1%")]


def _sdy0143(with_supporting=True):
    docs = [_real("Vol. 594, Page 624", "Warranty Deed", "December 15, 1965", ["Rebecca Willis Zipp", "Martha Willis Hudson"],
                  [GRACE, DUN], 640, "all right, title and interest, reserving minerals")]
    docs += [_real(f"Vol. {r.split('/')[0]}, Page {r.split('/')[1]}", "Deed of Gift", d, [DUN, GRACE], [JR3], 268.2,
                   f"an undivided {pct} of the surface only") for r, d, pct in GIFTS]
    docs += [
        _real("Vol. 708, Page 1", "Road Easement", "10/27/1972", ["Martha Willis Hudson", "J. T. Hudson"], [DUN, GRACE],
              refers=["Vol. 594, Page 624", "deeds of even date herewith"]),
        _real("Instrument No. 2011009176", "Special Warranty Deed", "2011-08-03", [JR3], ["Alvaro Banda"], 5.60,
              "all of grantor's interest in the surface"),
        _real("Instrument No. 2012012111", "UCC Financing Statement", "2012-09-19", ["Alvaro Banda"], ["ISPC"], 5.60),
        _real("Instrument No. 2018003815", "Warranty Deed", "2018-02-20", ["Alvaro Banda"], ["Alvaro Banda, Jr."], 1.0,
              "all"),
        _real("Instrument No. 2019003905", "Correction Affidavit", "2019-02-19", ["Alvaro Banda, Jr."], [],
              refers=["Instrument No. 2018003815"]),
    ]
    if with_supporting:
        docs += [_real("", "", "", [], [], kind="tax or appraisal record", file="APPRAISAL/CCS.TX.SDY.0143_APPRAISAL CARD.pdf"),
                 _real("", "", "", [], [], kind="map or plat", file="MAPS AND PLATS/CCS.TX.SDY.0143_CAD MAP.pdf")]
    return docs


def test_sdy0143_current_owner_with_tract_acreage():
    from titlepilot.chain import build_chain
    chain = build_chain(_sdy0143(), subject_acres=4.6)
    assert chain["current_owner"] == ["Alvaro Banda"]
    assert chain["vesting_instrument"] == "Instrument No. 2011009176"
    notes = dict(chain["ownership_notes"])
    assert "out-sale" in notes["Instrument No. 2018003815"]
    assert chain["undated"] == []
    assert {s["file_name"] for s in chain["supporting"]} == {
        "APPRAISAL/CCS.TX.SDY.0143_APPRAISAL CARD.pdf", "MAPS AND PLATS/CCS.TX.SDY.0143_CAD MAP.pdf"}


def test_sdy0143_without_acreage_flags_possible_outsale():
    from titlepilot.chain import build_chain
    chain = build_chain(_sdy0143(), subject_acres=None)
    assert chain["current_owner"] == ["Alvaro Banda, Jr."]          # can't tell without the tract acreage...
    assert any("may be an out-sale" in m for _, m in chain["ownership_notes"])   # ...so it says so


def test_sdy0143_encumbrances_match_verified_title():
    from titlepilot.chain import build_chain
    chain = build_chain(_sdy0143(), subject_acres=4.6)
    enc = {e["instrument_id"]: e for e in chain["encumbrances"]}
    assert set(enc) == {"Vol. 708, Page 1", "Instrument No. 2012012111"}     # not the 1965 warranty deed
    assert enc["Vol. 708, Page 1"]["status"] == "open"
    assert enc["Instrument No. 2012012111"]["holder"] == ["ISPC"]
    assert chain["unclassified"] == ["Instrument No. 2019003905"]          # correction affidavit: listed for review
    gifts = [c for c in chain["conveyances"] if c["type"] == "deed of gift"]
    assert len(gifts) == 17


def test_sdy0143_rules_with_name_runsheet():
    """The agent's file was a name runsheet for Alvaro Banda: no PK03 noise, 2018003815 matched,
    the other hits under his name become review items, not 'not uploaded' errors."""
    from titlepilot.chain import build_chain
    from titlepilot.intake import Intake
    from titlepilot.rules import Ctx, run_rules
    docs = _sdy0143()
    recorded = [d for d in docs if d["recorded"]]
    intake = Intake(tract_id="CCS.TX.SDY.0143", mode="agent runsheet", owners=["Alvaro Banda"])
    intake.runsheet_entries = [{"recording_ref": r, "instrument_type": t, "_kind": "name", "_names": "Alvaro Banda"}
                               for r, t in [("2023009302", "ASSIGNMENT"), ("2020013721", "DEED OF TRUST"),
                                            ("2019003906", "DEED OF TRUST"), ("2018008896", "AFFIDAVIT"),
                                            ("2018003815", "WARRANTY DEED")]]
    chain = build_chain(docs, subject_acres=4.6)
    flags, _ = run_rules(load_config(CONFIG).rules, Ctx(recorded, chain, intake))
    by_rule = {}
    for f in flags:
        by_rule.setdefault(f.rule_id, []).append(f)
    assert "PK03" not in by_rule
    pk02 = [f.message for f in by_rule["PK02"]]
    # the two deeds of trust are listed one by one; the assignment and affidavit are summarized
    assert sum("Name search hit" in m and "DEED OF TRUST" in m for m in pk02) == 2
    assert any(m.startswith("2 other instruments on the name runsheet for Alvaro Banda") for m in pk02)
    assert len(pk02) == 3
    assert not any("2018003815" in m for m in pk02)
    assert "R01" not in by_rule                                           # Dunagans acquired in 1965
    assert [f.instrument_id for f in by_rule["OW01"]] == ["Instrument No. 2018003815"]
    r12 = [f.message for f in by_rule.get("R12", [])]
    assert not any("DEEDS" in m for m in r12)                              # descriptions aren't references


# --- long runsheets (Dunagan_Grace_Willis_Name Runsheet pg 1.pdf was cut off) --------------------

class _FakeStream:
    def __init__(self, resp): self.resp = resp
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self): return self.resp


class _FakeClient:
    """Answers each call with the next canned (text, stop_reason)."""
    def __init__(self, replies):
        from types import SimpleNamespace as NS
        self.calls, self._replies = [], list(replies)
        def stream(**kw):
            self.calls.append(kw)
            text, stop = self._replies.pop(0)
            return _FakeStream(NS(stop_reason=stop, content=[NS(type="text", text=text)],
                                  usage=NS(model_dump=lambda: {})))
        self.messages = NS(stream=stream)


def _entry(ref, t="WD", g1="Grace Willis Dunagan", g2="Someone"):
    return {"recording_ref": ref, "instrument_type": t, "filing_date": "", "grantor": g1, "grantee": g2, "legal": ""}


def test_salvage_keeps_complete_entries_from_cut_off_reply():
    import json
    from titlepilot.extract import salvage_runsheet
    full = json.dumps({"runsheet_kind": "name", "owners_searched": ["Grace Willis Dunagan"], "subject_acreage": "",
                       "subject_description": "", "entries": [_entry(f"{600 + n}/{n}") for n in range(40)]})
    cut = full[: int(len(full) * 0.6)]                       # reply stopped partway through
    out = salvage_runsheet(cut)
    assert out["runsheet_kind"] == "name" and out["owners_searched"] == ["Grace Willis Dunagan"]
    assert 20 <= len(out["entries"]) < 40
    assert all(e["recording_ref"] for e in out["entries"])


def test_pdf_runsheet_read_page_by_page_and_merged(tmp_path):
    import json
    from PIL import Image
    from titlepilot.extract import Extractor
    pdf = tmp_path / "Dunagan_Grace_Willis_Name Runsheet.pdf"
    pages = [Image.new("RGB", (850, 1100), "white") for _ in range(3)]
    pages[0].save(pdf, "PDF", save_all=True, append_images=pages[1:])
    page = lambda refs, kind="name": json.dumps({"runsheet_kind": kind, "owners_searched": ["Grace Willis Dunagan"],
                                                 "subject_acreage": "", "subject_description": "",
                                                 "entries": [_entry(r) for r in refs]})
    page3_cut = page([f"9{n}/1" for n in range(30)])[:900]     # third page is cut off
    ex = Extractor({}, "", mock=True); ex.mock = False; ex._retryable = (ConnectionError,)
    ex.client = _FakeClient([(page(["594/624", "640/91"]), "end_turn"),
                             (page(["640/91", "708/1"]), "end_turn"),          # 640/91 repeats across pages
                             (page3_cut, "max_tokens")])
    out = ex.read_runsheet(pdf, tmp_path / "pages")
    assert len(ex.client.calls) == 3                                       # one call per page
    assert all(c["max_tokens"] >= 32000 for c in ex.client.calls)
    refs = [e["recording_ref"] for e in out["entries"]]
    assert refs[:3] == ["594/624", "640/91", "708/1"] and len(refs) > 3    # merged, de-duplicated, salvage kept
    assert out["runsheet_kind"] == "name"
    assert out["_partial"] and "page/part 3" in out["_partial"][0]


def test_name_runsheet_flags_only_name_search_hits():
    from titlepilot.intake import Intake
    from titlepilot.rules import Ctx, run_rules
    from titlepilot.chain import build_chain
    intake = Intake(tract_id="T", owners=["Grace Willis Dunagan"])
    hits = [("812/40", "D/T"), ("900/12", "Abstract of Judgment"), ("1001/5", "FTL")]
    others = [(f"{700 + n}/{n}", "WD") for n in range(25)]
    intake.runsheet_entries = [{**_entry(r, t), "_kind": "name", "_names": "Grace Willis Dunagan"}
                               for r, t in hits + others]
    flags, _ = run_rules(load_config(CONFIG).rules, Ctx([], build_chain([]), intake))
    pk02 = [f.message for f in flags if f.rule_id == "PK02"]
    assert len(pk02) == 4                                          # 3 hits + 1 summary line, not 28 flags
    assert sum("Name search hit" in m for m in pk02) == 3
    assert any(m.startswith("25 other instruments") for m in pk02)


def test_tract_list_found_in_several_places(tmp_path, monkeypatch):
    from titlepilot.intake import find_tract_list
    tract = tmp_path / "proj" / "tracts" / "CCS.TX.SDY.0143"
    tract.mkdir(parents=True)
    assert find_tract_list(tract) is None or find_tract_list(tract).parent == pathlib.Path.cwd()
    monkeypatch.chdir(tmp_path / "proj")
    (tmp_path / "proj" / "tracts.csv").write_text("tract_id,acreage\nCCS.TX.SDY.0143,4.6\n")
    assert find_tract_list(tract) == (tmp_path / "proj" / "tracts.csv").resolve()      # project folder
    (tmp_path / "proj" / "tracts" / "tracts.csv").write_text("tract_id,acreage\nCCS.TX.SDY.0143,4.6\n")
    assert find_tract_list(tract) == (tmp_path / "proj" / "tracts" / "tracts.csv").resolve()   # beside tracts


def test_unreadable_deed_acreage_is_flagged():
    from titlepilot.chain import build_chain
    docs = [d for d in _sdy0143() if "2018003815" not in d["instrument_id"]]
    docs.append(_real("Instrument No. 2018003815", "Warranty Deed", "2018-02-20", ["Alvaro Banda"],
                      ["Alvaro Banda, Jr."], None, "all"))          # acreage not read
    chain = build_chain(docs, subject_acres=4.6)
    assert any("Acreage could not be read" in m for _, m in chain["ownership_notes"])


def test_title_report_in_seg_format(sample, tmp_path):
    import openpyxl
    db = DB(tmp_path / "runs.sqlite")
    res = run_tract(sample / TRACT, load_config(CONFIG), db, tmp_path / "out", mock=True, log=lambda *_: None)
    wb = openpyxl.load_workbook(res["report"])
    assert wb.sheetnames == ["Ownership ", "Ownership BT Data", "Encumbrances", "Flowchart", "Names Examined",
                             "Index to All Documents", "Runsheet ", "AI Review", "AI Mapping", "AI Flags", "AI Supporting Docs"]
    own = wb["Ownership "]
    assert own["C14"].value == TRACT and own["B11"].value == "Smith Ranch LLC"
    assert own["B1"].value == "Schoeffler Energy Group, Inc."                # template boilerplate kept
    assert own["C28"].value == "1994003300"                                   # vesting deed, SEG style
    rs = wb["Runsheet "]
    assert [rs.cell(13, c).value for c in (1, 2, 3)] == [1, "1978001001", "WD"]
    assert rs.cell(18, 2).value == "2005000777" and rs.cell(19, 2).value is None
    enc = wb["Encumbrances"]
    assert "2001004410" in enc["B44"].value and "Acme Supply" in enc["B44"].value   # open judgment
    assert "1980000123" not in (enc["B44"].value or "")                             # released deed of trust
    assert enc["B29"].value == "None"                                               # pipeline isn't a road
    assert any("TS03" == r[0].value for r in wb["AI Flags"].iter_rows(min_row=2))


def test_name_runsheet_entry_on_tract_abstract_is_listed():
    from titlepilot.intake import Intake
    from titlepilot.rules import Ctx, run_rules
    from titlepilot.chain import build_chain
    intake = Intake(tract_id="T", owners=["James A. Dunagan, III"],
                    legal_description="4.60 acres in the I & G N Railroad Company Survey #4, Abstract 289, Liberty County")
    intake.runsheet_entries = [
        {**_entry("2018003814", "Waiver"), "legal": "Abstract: 289 Survey: I & G N RR CO SURVEY Acres: 1.00",
         "_kind": "name", "_names": "James A. Dunagan, III"},
        {**_entry("2009003727", "Affidavit"), "legal": "SEE INSTRUMENT", "_kind": "name", "_names": "James A. Dunagan, III"},
        {**_entry("953/351", "Lease"), "legal": "Survey: I & GN SURVEY SECTION #6 ABSTRACT #293",
         "_kind": "name", "_names": "James A. Dunagan, III"},
    ]
    flags, _ = run_rules(load_config(CONFIG).rules, Ctx([], build_chain([]), intake))
    pk02 = [f.message for f in flags if f.rule_id == "PK02"]
    assert any("2018003814" in m and "this tract's abstract (A-289)" in m for m in pk02)
    assert any(m.startswith("2 other instruments") for m in pk02)


@pytest.mark.parametrize("text,expected", [
    ("BEGINNING at a 1/2 inch iron rod; THENCE N 45° 30' E 210.5 feet to a point; THENCE S 44 deg W", True),
    ("5.60 acres described by metes and bounds in Exhibit A", True),
    ("being more particularly described by field notes attached", True),
    ("to the POINT OF BEGINNING and containing 1.00 acre", True),
    ("268.2 acres in the I & G N Survey No. 4, Abstract 289, Liberty County, Texas", False),
    ("Lot 4, Block 2, Sunset Acres Subdivision", False),
    ("", False),
])
def test_metes_and_bounds_detection(text, expected):
    from titlepilot.mapping import is_metes_and_bounds
    assert is_metes_and_bounds(text) is expected


def _conv(iid, acres, legal="", grantor="A", grantee="B", date="2000-01-01"):
    return {"instrument_id": iid, "category": "conveyance", "acreage": acres, "legal": legal, "grantors": [grantor],
            "grantees": [grantee], "file_name": iid + ".pdf", "filing_date": date, "ref_keys": {iid}}


def test_mapping_items_out_sales_and_exceptions():
    from types import SimpleNamespace
    from titlepilot.mapping import mapping_items
    parent = _conv("V1103P60", 268.2, "268.2 acres in the I&GN Survey 4", date="1990-01-01")
    vest = _conv("2011-9176", 5.6, "5.60 acres; metes and bounds in Exhibit A", date="2011-01-01")
    out = _conv("2018-3815", 1.0, "1.00 acre out of the 5.60 acre tract", date="2018-01-01")
    chain = {"conveyances": [parent, vest, out], "instruments": [parent, vest, out],
             "vesting_instrument": "2011-9176", "subject_acres": 4.6}
    intake = SimpleNamespace(legal_description="4.60 acres, LESS AND EXCEPT 1 acre described in Document Number "
                                               "2018003815; SAVE AND EXCEPT 0.5 acre in Document Number 2020001234",
                             subject_acres=4.6)
    items = {i["instrument_id"]: i["reasons"] for i in mapping_items(chain, intake)}
    assert "V1103P60" not in items
    assert any("parent tract" in r for r in items["2011-9176"]) and "Metes and bounds description" in items["2011-9176"]
    assert any("out of the 4.6 ac tract" in r for r in items["2018-3815"])
    assert any("Excepted" in r for r in items["2018-3815"])
    assert any("not in the package" in r for r in items[None])


def test_mapping_queue_keeps_mapped_rows(tmp_path):
    import csv
    from titlepilot.mapping import update_queue
    item = {"instrument_id": "2018-3815", "reasons": ["Out-sale"], "acres": 1.0, "grantors": ["A"], "grantees": ["B"],
            "legal": "", "file_name": "x.pdf", "filing_date": None}
    q = update_queue(tmp_path, "T1", 1, [item])
    update_queue(tmp_path, "T2", 2, [item])
    rows = list(csv.DictReader(q.open(encoding="utf-8-sig")))
    rows[0]["status"], rows[0]["mapped_by"] = "mapped", "JD"
    with q.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    update_queue(tmp_path, "T1", 3, [item, {**item, "instrument_id": "2011-9176"}])   # re-run T1
    rows = list(csv.DictReader(q.open(encoding="utf-8-sig")))
    t1 = {r["instrument"]: r for r in rows if r["tract_id"] == "T1"}
    assert t1["2018-3815"]["status"] == "mapped" and t1["2018-3815"]["run_id"] == "1"   # mapper's work kept
    assert t1["2011-9176"]["status"] == "open"
    assert len([r for r in rows if r["tract_id"] == "T2"]) == 1


def test_mapping_notification_payloads(monkeypatch):
    import json
    from titlepilot import mapping
    sent = []

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(mapping.urllib.request, "urlopen", lambda req, timeout: sent.append(req) or Resp())
    item = {"instrument_id": "2018-3815", "reasons": ["Out-sale"], "acres": 1.0}
    assert mapping.notify("T1", 1, [item]) is None or "TITLEPILOT_MAPPING_WEBHOOK" in __import__("os").environ
    assert mapping.notify("T1", 1, [item], url="https://example.test/hook") == 200
    card = json.loads(sent[-1].data)
    assert card["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    mapping.notify("T1", 1, [item], url="https://example.test/hook", fmt="slack")
    assert "2018-3815" in json.loads(sent[-1].data)["text"]
    assert mapping.notify("T1", 1, [], url="https://example.test/hook") is None


def test_live_shape_run_flags_mapping(sample, tmp_path):
    res = run_tract(sample / TRACT, load_config(CONFIG), DB(tmp_path / "m.sqlite"), tmp_path / "out", mock=True,
                    log=lambda *_: None, notify_team=False)
    assert (tmp_path / "out" / "mapping_queue.csv").exists()
    assert len([f for f in res["flags"] if f.rule_id == "MP01"]) == len(res["mapping"])
    import openpyxl
    assert "AI Mapping" in openpyxl.load_workbook(res["report"]).sheetnames


def _map_item():
    return {"instrument_id": "2018-3815", "reasons": ["Out-sale: 1 ac out of the 4.6 ac tract"], "acres": 1.0,
            "grantors": ["Alvaro Banda"], "grantees": ["Alvaro Banda, Jr."], "legal": "1.00 acre out of A-289",
            "file_name": "OPR_2018003815_WD.pdf", "filing_date": "2018-02-20"}


def test_no_email_without_address(monkeypatch):
    from titlepilot import mapping
    for k in ("TITLEPILOT_MAPPING_EMAIL", "TITLEPILOT_MAPPING_WEBHOOK"):
        monkeypatch.delenv(k, raising=False)
    assert mapping.email("T1", 1, [_map_item()]) is None
    assert mapping.send_notifications("T1", 1, [_map_item()]) == []


def test_email_by_smtp_with_report_attached(monkeypatch, tmp_path):
    from titlepilot import mapping
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout): sent["server"] = (host, port)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): sent["tls"] = True
        def login(self, u, p): sent["login"] = u
        def send_message(self, msg): sent["msg"] = msg

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    for k, v in {"TITLEPILOT_MAPPING_EMAIL": "a@segland.com; b@segland.com", "TITLEPILOT_SMTP_HOST": "smtp.office365.com",
                 "TITLEPILOT_SMTP_USER": "me@segland.com", "TITLEPILOT_SMTP_PASSWORD": "x"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TITLEPILOT_MAPPING_WEBHOOK", raising=False)
    monkeypatch.delenv("TITLEPILOT_EMAIL_METHOD", raising=False)
    report = tmp_path / "T1_title_report.xlsx"
    report.write_bytes(b"xlsx")
    res = mapping.send_notifications("T1", 7, [_map_item()], report)
    assert res == [("Email", "sent to a@segland.com, b@segland.com")]
    msg = sent["msg"]
    assert sent["server"] == ("smtp.office365.com", 587) and sent["tls"] and sent["login"] == "me@segland.com"
    assert msg["To"] == "a@segland.com, b@segland.com" and "Mapping needed: T1" in msg["Subject"]
    assert "2018-3815" in msg.get_body().get_content()
    assert [a.get_filename() for a in msg.iter_attachments()] == ["T1_title_report.xlsx"]


def test_email_failure_does_not_stop_run(monkeypatch):
    import sys
    from titlepilot import mapping
    monkeypatch.setenv("TITLEPILOT_MAPPING_EMAIL", "a@segland.com")
    monkeypatch.delenv("TITLEPILOT_SMTP_HOST", raising=False)
    monkeypatch.delenv("TITLEPILOT_EMAIL_METHOD", raising=False)
    monkeypatch.delenv("TITLEPILOT_MAPPING_WEBHOOK", raising=False)
    monkeypatch.setitem(sys.modules, "win32com", None)      # no Outlook here
    monkeypatch.setitem(sys.modules, "win32com.client", None)
    (channel, result), = mapping.send_notifications("T1", 1, [_map_item()])
    assert channel == "Email" and result.startswith("FAILED") and "Outlook" in result
