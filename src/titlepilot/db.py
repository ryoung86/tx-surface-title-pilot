"""SQLite run history and audit trail.

Append-only: triggers block UPDATE and DELETE on every history table, so a
re-run or reopened tract is always a new run, and status changes are events.
"""
import json
import sqlite3
from datetime import datetime, timezone

HISTORY_TABLES = ["runs", "run_events", "run_instruments", "run_outputs", "flags",
                  "corrections", "flag_actions", "signoffs"]

DDL = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, name TEXT, login TEXT UNIQUE, role TEXT, can_sign_off INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, tract_id TEXT, project TEXT, county TEXT, title_agent TEXT, package_date TEXT,
  started_at TEXT, model TEXT, fields_version TEXT, rules_version TEXT, prompt_version TEXT,
  previous_run_id INTEGER REFERENCES runs(id));
CREATE TABLE IF NOT EXISTS run_events(
  id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id), at TEXT, event TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS run_instruments(
  id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id), instrument_id TEXT, file_name TEXT,
  instrument_type TEXT, filing_date TEXT, raw_json TEXT, schema_errors TEXT);
CREATE TABLE IF NOT EXISTS run_outputs(
  id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id), kind TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS flags(
  id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id), instrument_id TEXT, rule_id TEXT,
  severity TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS corrections(
  id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id), instrument_id TEXT, field TEXT,
  ai_value TEXT, new_value TEXT, reviewer_id INTEGER REFERENCES users(id), at TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS flag_actions(
  id INTEGER PRIMARY KEY, flag_id INTEGER REFERENCES flags(id),
  action TEXT CHECK(action IN ('confirmed','dismissed')), reviewer_id INTEGER REFERENCES users(id),
  at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS signoffs(
  id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES runs(id), approver_id INTEGER REFERENCES users(id),
  at TEXT, outputs_json TEXT);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(DDL)
        for t in HISTORY_TABLES:
            for op in ("UPDATE", "DELETE"):
                self.conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS no_{op.lower()}_{t} BEFORE {op} ON {t} "
                    f"BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END")
        self.conn.commit()

    def _insert(self, sql, args):
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur.lastrowid

    def latest_run_id(self, tract_id):
        row = self.conn.execute("SELECT max(id) FROM runs WHERE tract_id = ?", (tract_id,)).fetchone()
        return row[0] if row else None

    def start_run(self, intake, cfg, model):
        prev = self.latest_run_id(intake.tract_id)
        return self._insert(
            "INSERT INTO runs(tract_id, project, county, title_agent, package_date, started_at, model,"
            " fields_version, rules_version, prompt_version, previous_run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (intake.tract_id, intake.project, intake.county, intake.agent, intake.delivery_date, _now(), model,
             cfg.versions["fields"], cfg.versions["rules"], cfg.versions["prompt"], prev))

    def event(self, run_id, event, detail=""):
        self._insert("INSERT INTO run_events(run_id, at, event, detail) VALUES (?,?,?,?)",
                     (run_id, _now(), event, detail))

    def add_instrument(self, run_id, norm, record, errors):
        self._insert(
            "INSERT INTO run_instruments(run_id, instrument_id, file_name, instrument_type, filing_date,"
            " raw_json, schema_errors) VALUES (?,?,?,?,?,?,?)",
            (run_id, norm["instrument_id"], norm["file_name"], norm["type"], norm["filing_date"],
             json.dumps(record), json.dumps(errors)))

    def add_output(self, run_id, kind, data):
        self._insert("INSERT INTO run_outputs(run_id, kind, json) VALUES (?,?,?)",
                     (run_id, kind, json.dumps(data, default=list)))

    def output(self, run_id, kind):
        row = self.conn.execute(
            "SELECT json FROM run_outputs WHERE run_id = ? AND kind = ? ORDER BY id DESC LIMIT 1",
            (run_id, kind)).fetchone()
        return json.loads(row[0]) if row else None

    def add_flags(self, run_id, flags):
        self.conn.executemany(
            "INSERT INTO flags(run_id, instrument_id, rule_id, severity, message) VALUES (?,?,?,?,?)",
            [(run_id, f.instrument_id, f.rule_id, f.severity, f.message) for f in flags])
        self.conn.commit()
