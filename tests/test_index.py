"""Indexing a tract in Supabase, with a stand-in for the Supabase API (no network)."""
import json
import uuid
from pathlib import Path

import pytest

from titlepilot.config import load_config
from titlepilot.db import DB
from titlepilot.index import index_tract
from titlepilot.pipeline import run_tract
from titlepilot.sample import TRACT, make_sample_tract
from titlepilot.supa import Supabase, SupabaseError, object_key, safe_key

CONFIG = Path(__file__).resolve().parents[1] / "config"


class FakeSupabase:
    """Records what index_tract would send to Supabase."""

    def __init__(self):
        self.objects, self.tables = {}, {"tracts": {}, "runs": {}, "documents": {}, "parties": [], "flags": []}
        self.uploads = 0

    def check_bucket(self):
        pass

    def upload(self, key, data, content_type):
        self.objects[key] = (len(data), content_type)
        self.uploads += 1

    def select(self, table, **filters):
        sel = filters.pop("select", None)
        rows = self.tables[table].values() if isinstance(self.tables[table], dict) else self.tables[table]
        return [r for r in rows if all(r.get(k) == v for k, v in filters.items())]

    def upsert(self, table, rows, on_conflict):
        out = []
        for r in rows:
            key = r[on_conflict]
            cur = self.tables[table].setdefault(key, {"id": str(uuid.uuid4())} if table == "documents" else {})
            cur.update(r)
            out.append(dict(cur))
        return out

    def insert(self, table, rows):
        self.tables[table].extend(rows)

    def delete(self, table, column, values):
        values = set(values)
        self.tables[table] = [r for r in self.tables[table] if r.get(column) not in values]


@pytest.fixture()
def ran(tmp_path):
    data = make_sample_tract(tmp_path / "data")
    db = DB(tmp_path / "runs.sqlite")
    cfg = load_config(CONFIG)
    run_tract(data / TRACT, cfg, db, tmp_path / "out", mock=True, log=lambda *_: None)
    return data / TRACT, db, cfg, tmp_path / "out"


def test_index_uploads_files_and_readings(ran):
    folder, db, cfg, out = ran
    sb = FakeSupabase()
    r = index_tract(folder, sb, db, out_dir=out, rules=cfg.rules, log=lambda *_: None)
    assert r["tract"] == TRACT and r["run"] == 1 and r["files"] > 0
    assert r["uploaded"] == r["files"] + len(r["reports"]) and set(r["reports"]) == {"title report", "review workbook"}
    keys = list(sb.objects)
    assert all(k.startswith(f"tracts/{TRACT}/") for k in keys)
    assert any("/originals/" in k for k in keys) and any("/reports/run-1/" in k for k in keys)
    docs = list(sb.tables["documents"].values())
    read = [d for d in docs if d.get("fields")]
    assert read and all(d["sha256"] and d["tract_id"] == TRACT for d in docs)
    deed = next(d for d in read if d["category"] == "conveyance")
    assert deed["instrument_id"] and deed["grantees"] and deed["filing_date"]
    assert sb.tables["parties"] and {p["role"] for p in sb.tables["parties"]} == {"grantor", "grantee"}
    assert sb.tables["flags"] and all(f["run_id"] == r_["run_id"] for f in sb.tables["flags"]
                                      for r_ in sb.tables["runs"].values())
    assert any(f["what_to_do"] for f in sb.tables["flags"])           # reviewer actions carried over
    tract = sb.tables["tracts"][TRACT]
    assert tract["current_owner"] and tract["latest_run_id"] and tract["status"] == "run"
    json.dumps(list(sb.tables["documents"].values()))                  # everything is JSON-safe


def test_reindex_skips_unchanged_files_and_replaces_parties_and_flags(ran):
    folder, db, cfg, out = ran
    sb = FakeSupabase()
    index_tract(folder, sb, db, out_dir=out, rules=cfg.rules, log=lambda *_: None)
    first = (sb.uploads, len(sb.tables["parties"]), len(sb.tables["flags"]), len(sb.tables["documents"]))
    r = index_tract(folder, sb, db, out_dir=out, rules=cfg.rules, log=lambda *_: None)
    assert r["uploaded"] == 0 and r["unchanged"] == r["files"] + len(r["reports"])   # nothing re-sent
    assert (len(sb.tables["parties"]), len(sb.tables["flags"]), len(sb.tables["documents"])) == first[1:]


def test_files_only_before_a_run(tmp_path):
    data = make_sample_tract(tmp_path / "data")
    sb = FakeSupabase()
    r = index_tract(data / TRACT, sb, None, log=lambda *_: None)
    assert r["run"] is None and r["with_readings"] == 0 and not sb.tables["flags"]
    kinds = {d["kind"] for d in sb.tables["documents"].values()}
    assert "not read yet" in kinds and sb.tables["tracts"][TRACT]["status"] == "indexed"


def test_storage_keys_are_safe():
    assert safe_key("Vol 1279 Pg 279 #2 [copy].pdf") == "Vol 1279 Pg 279 _2 _copy_.pdf"
    assert object_key("tracts", "CCS.TX.SDY.0180", "originals", "Deeds\\WD 1279-279.pdf") == \
        "tracts/CCS.TX.SDY.0180/originals/Deeds/WD 1279-279.pdf"


def test_client_requests(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, status=200, body=None):
            self.status_code, self._body, self.text = status, body if body is not None else {}, ""
        def json(self):
            return self._body

    class Session:
        def request(self, method, url, **kw):
            calls.append((method, url, kw))
            if "/object/sign/" in url:
                return Resp(body={"signedURL": "/object/sign/title-packages/a.pdf?token=x"})
            if url.endswith("/bucket/missing"):
                return Resp(400, {"message": "Bucket not found"})
            return Resp(body=[{"id": "1"}])

    sb = Supabase("https://p.supabase.co/", "KEY", session=Session())
    sb.upload("tracts/T/originals/a b.pdf", b"x", "application/pdf")
    m, url, kw = calls[-1]
    assert m == "POST" and url == "https://p.supabase.co/storage/v1/object/title-packages/tracts/T/originals/a%20b.pdf"
    assert kw["headers"]["x-upsert"] == "true" and kw["headers"]["apikey"] == "KEY"
    assert sb.signed_url("a.pdf") == "https://p.supabase.co/storage/v1/object/sign/title-packages/a.pdf?token=x"
    sb.upsert("documents", [{"storage_path": "a"}], "storage_path")
    assert calls[-1][2]["params"] == {"on_conflict": "storage_path"}
    assert "merge-duplicates" in calls[-1][2]["headers"]["Prefer"]
    sb.delete("parties", "document_id", ["u1", "u2"])
    assert calls[-1][2]["params"] == {"document_id": 'in.("u1","u2")'}
    assert kw["headers"]["Authorization"] == "Bearer KEY"                 # legacy service_role key
    new = Supabase("https://p.supabase.co", "sb_secret_abc", session=Session())
    new.upload("a.pdf", b"x")
    assert calls[-1][2]["headers"]["apikey"] == "sb_secret_abc" and "Authorization" not in calls[-1][2]["headers"]
    with pytest.raises(SupabaseError, match="Bucket not found"):
        Supabase("https://p.supabase.co", "KEY", bucket="missing", session=Session()).check_bucket()
    with pytest.raises(SupabaseError, match="SUPABASE_URL"):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        Supabase(key="k")
