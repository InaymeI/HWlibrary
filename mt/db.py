import json
import sqlite3
import time
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS subjects (
    id           INTEGER PRIMARY KEY,
    title        TEXT,
    payload_json TEXT,
    fetched_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id           INTEGER PRIMARY KEY,
    subject_id   INTEGER NOT NULL,
    mode         TEXT NOT NULL,
    title        TEXT,
    lesson_id    INTEGER,
    fetched_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tests (
    id           INTEGER PRIMARY KEY,
    kbs_id       INTEGER NOT NULL,
    subject_id   INTEGER NOT NULL,
    type         TEXT,
    payload_json TEXT NOT NULL,
    fetched_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tests_kbs     ON tests(kbs_id);
CREATE INDEX IF NOT EXISTS idx_tests_subject ON tests(subject_id);
CREATE INDEX IF NOT EXISTS idx_kbs_subject   ON knowledge_bases(subject_id);
"""

def now() -> int:
    return int(time.time())

@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init():
    with connect() as c:
        c.executescript(SCHEMA)

# ---------- subjects ----------
def upsert_subject(sid: int, title: str, payload: dict):
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO subjects(id,title,payload_json,fetched_at) VALUES(?,?,?,?)",
            (sid, title, json.dumps(payload, ensure_ascii=False), now()),
        )

def list_subjects():
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM subjects ORDER BY title").fetchall()]

def get_subject(sid: int):
    with connect() as c:
        r = c.execute("SELECT * FROM subjects WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else None

# ---------- kbs ----------
def upsert_kbs(kid: int, sid: int, mode: str, title: str | None, lesson_id: int | None):
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO knowledge_bases(id,subject_id,mode,title,lesson_id,fetched_at) VALUES(?,?,?,?,?,?)",
            (kid, sid, mode, title, lesson_id, now()),
        )

def list_kbs(sid: int):
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM knowledge_bases WHERE subject_id=? ORDER BY id", (sid,)
        ).fetchall()]

# ---------- tests ----------
def upsert_test(tid: int, kid: int, sid: int, ttype: str | None, payload: dict):
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO tests(id,kbs_id,subject_id,type,payload_json,fetched_at) VALUES(?,?,?,?,?,?)",
            (tid, kid, sid, ttype, json.dumps(payload, ensure_ascii=False), now()),
        )

def list_tests(kid: int):
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM tests WHERE kbs_id=? ORDER BY id", (kid,)
        ).fetchall()]

def get_test(tid: int):
    with connect() as c:
        r = c.execute("SELECT * FROM tests WHERE id=?", (tid,)).fetchone()
        return dict(r) if r else None

def all_tests(sid: int | None = None):
    with connect() as c:
        q = "SELECT * FROM tests"
        args = ()
        if sid is not None:
            q += " WHERE subject_id=?"
            args = (sid,)
        q += " ORDER BY subject_id, kbs_id, id"
        return [dict(r) for r in c.execute(q, args).fetchall()]

def stats_for_subject(sid: int) -> dict:
    with connect() as c:
        r = c.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN type IN ('type1','type2','type3','type4','type5') THEN 1 ELSE 0 END) AS supported"
            " FROM tests WHERE subject_id=?",
            (sid,),
        ).fetchone()
        return {"total": r["total"] or 0, "supported": r["supported"] or 0}
