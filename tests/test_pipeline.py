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
