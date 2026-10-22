"""SQLite cache.

Design notes
------------
* One connection per thread (``sqlite3`` objects are not shareable), in WAL mode
  so the UI thread can read while the sync worker writes.
* All writes go through :func:`transaction` and are committed in batches.
* Test titles are stored in a dedicated column: listing/filtering thousands of
  tests no longer needs to ``json.loads`` every payload.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import config

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS subjects (
    id           INTEGER PRIMARY KEY,
    title        TEXT    NOT NULL DEFAULT '',
    payload_json TEXT    NOT NULL DEFAULT '{}',
    fetched_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id           INTEGER PRIMARY KEY,
    subject_id   INTEGER NOT NULL,
    mode         TEXT    NOT NULL DEFAULT '',
    title        TEXT    NOT NULL DEFAULT '',
    lesson_id    INTEGER,
    fetched_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tests (
    id           INTEGER PRIMARY KEY,
    kbs_id       INTEGER NOT NULL,
    subject_id   INTEGER NOT NULL,
    type         TEXT    NOT NULL DEFAULT '',
    title        TEXT    NOT NULL DEFAULT '',
    payload_json TEXT    NOT NULL,
    fetched_at   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tests_kbs       ON tests(kbs_id);
CREATE INDEX IF NOT EXISTS idx_tests_subject   ON tests(subject_id);
CREATE INDEX IF NOT EXISTS idx_kbs_subject     ON knowledge_bases(subject_id);
CREATE INDEX IF NOT EXISTS idx_tests_type      ON tests(type);
"""

_local = threading.local()
_db_path: Path | None = None


def now() -> int:
    return int(time.time())


def set_database_path(path: str | Path | None) -> None:
    """Point the cache at another file (used by tests and ``--db``)."""
    global _db_path
    close()
    _db_path = Path(path) if path is not None else None


def database_path() -> Path:
    return _db_path or config.DB_PATH


def get_connection() -> sqlite3.Connection:
    path = database_path()
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    current = getattr(_local, "path", None)
    if conn is not None and current != path:
        _close_current()
        conn = None
    if conn is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
        # SQLite's lower()/LIKE only fold ASCII; this keeps Cyrillic search
        # case-insensitive.
        conn.create_function("ulower", 1, lambda value: str(value).lower() if value is not None else None, deterministic=True)
        _local.conn = conn
        _local.path = path
    return conn


def _close_current() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        with contextlib.suppress(sqlite3.Error):
            conn.close()
    _local.conn = None
    _local.path = None


def close() -> None:
    """Close the connection of the *current* thread."""
    _close_current()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init() -> None:
    """Create the schema and run migrations. Safe to call repeatedly."""
    conn = get_connection()
    conn.executescript(SCHEMA)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        _migrate_v1(conn)
    if version < 2:
        _migrate_v2(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """Add the columns that were missing in the very first schema."""
    for table, column, ddl in (
        ("subjects", "title", "TEXT NOT NULL DEFAULT ''"),
        ("knowledge_bases", "title", "TEXT NOT NULL DEFAULT ''"),
        ("knowledge_bases", "lesson_id", "INTEGER"),
        ("tests", "title", "TEXT NOT NULL DEFAULT ''"),
    ):
        if conn.in_transaction:
            conn.execute("COMMIT")
        if column not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _migrate_v2(conn: sqlite3.Connection) -> None:
    """Backfill titles from cached JSON payloads (one-off)."""
    if conn.in_transaction:
        conn.execute("COMMIT")

    test_rows = conn.execute(
        "SELECT id, payload_json FROM tests WHERE title = '' AND payload_json LIKE '%title%'"
    ).fetchall()
    updates = []
    for row in test_rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        title = payload.get("title") or payload.get("name") or ""
        if title:
            updates.append((str(title)[:300], row["id"]))
    if updates:
        conn.executemany("UPDATE tests SET title = ? WHERE id = ?", updates)

    subject_rows = conn.execute(
        "SELECT id, payload_json FROM subjects WHERE title = ''"
    ).fetchall()
    subject_updates = []
    for row in subject_rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        title = payload.get("title") or payload.get("name") or ""
        if title:
            subject_updates.append((str(title)[:300], row["id"]))
    if subject_updates:
        conn.executemany("UPDATE subjects SET title = ? WHERE id = ?", subject_updates)


# --------------------------------------------------------------------------- #
# subjects
# --------------------------------------------------------------------------- #
def upsert_subject(sid: int, title: str, payload: dict[str, Any]) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO subjects(id, title, payload_json, fetched_at) VALUES(?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                title        = excluded.title,
                payload_json = excluded.payload_json,
                fetched_at   = excluded.fetched_at
            """,
            (sid, title or f"Предмет #{sid}", json.dumps(payload, ensure_ascii=False), now()),
        )


def list_subjects() -> list[dict[str, Any]]:
    rows = get_connection().execute(
        "SELECT * FROM subjects ORDER BY title COLLATE NOCASE, id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_subject(sid: int) -> dict[str, Any] | None:
    row = get_connection().execute("SELECT * FROM subjects WHERE id = ?", (sid,)).fetchone()
    return dict(row) if row else None


def rename_subject(sid: int, title: str) -> None:
    with transaction() as conn:
        conn.execute("UPDATE subjects SET title = ? WHERE id = ?", (title, sid))


# --------------------------------------------------------------------------- #
# knowledge bases
# --------------------------------------------------------------------------- #
def upsert_kbs(
    kid: int,
    sid: int,
    mode: str,
    title: str | None,
    lesson_id: int | None,
) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_bases(id, subject_id, mode, title, lesson_id, fetched_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                subject_id = excluded.subject_id,
                mode       = excluded.mode,
                title      = excluded.title,
                lesson_id  = COALESCE(excluded.lesson_id, knowledge_bases.lesson_id),
                fetched_at = excluded.fetched_at
            """,
            (kid, sid, mode or "", title or "", lesson_id, now()),
        )


def list_kbs(sid: int) -> list[dict[str, Any]]:
    rows = get_connection().execute(
        "SELECT * FROM knowledge_bases WHERE subject_id = ? ORDER BY id", (sid,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_kbs(kid: int) -> dict[str, Any] | None:
    row = get_connection().execute(
        "SELECT * FROM knowledge_bases WHERE id = ?", (kid,)
    ).fetchone()
    return dict(row) if row else None


def prune_kbs(sid: int, keep_ids: Sequence[int]) -> int:
    """Drop knowledge bases of *sid* that disappeared from the curriculum tree."""
    keep = set(keep_ids)
    existing = {row["id"] for row in list_kbs(sid)}
    stale = existing - keep
    if not stale:
        return 0
    with transaction() as conn:
        for kid in stale:
            conn.execute("DELETE FROM tests WHERE kbs_id = ?", (kid,))
            conn.execute("DELETE FROM knowledge_bases WHERE id = ?", (kid,))
    return len(stale)


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
def upsert_test(
    tid: int,
    kid: int,
    sid: int,
    ttype: str | None,
    payload: dict[str, Any],
) -> None:
    upsert_tests([(tid, kid, sid, ttype, _title_of(payload), payload)])


def upsert_tests(rows: Iterable[tuple[int, int, int, str | None, str, dict[str, Any]]]) -> int:
    """Bulk insert/update ``(id, kbs_id, subject_id, type, title, payload)`` rows."""
    stamp = now()
    values = [
        (
            tid,
            kid,
            sid,
            (ttype or ""),
            (title or "")[:300],
            json.dumps(payload, ensure_ascii=False),
            stamp,
        )
        for tid, kid, sid, ttype, title, payload in rows
    ]
    if not values:
        return 0
    with transaction() as conn:
        conn.executemany(
            """
            INSERT INTO tests(id, kbs_id, subject_id, type, title, payload_json, fetched_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                kbs_id       = excluded.kbs_id,
                subject_id   = excluded.subject_id,
                type         = excluded.type,
                title        = excluded.title,
                payload_json = excluded.payload_json,
                fetched_at   = excluded.fetched_at
            """,
            values,
        )
    return len(values)


def replace_kbs_tests(
    kid: int,
    sid: int,
    rows: Iterable[tuple[int, str | None, str, dict[str, Any]]],
) -> int:
    """Replace the cached tests of one knowledge base atomically.

    ``rows`` items are ``(test_id, type, title, payload)``.  Tests that were
    deleted server-side disappear from the cache, and a failed sync never wipes
    the previous data because everything happens inside one transaction.
    """
    stamp = now()
    values = [
        (tid, kid, sid, (ttype or ""), (title or "")[:300],
         json.dumps(payload, ensure_ascii=False), stamp)
        for tid, ttype, title, payload in rows
    ]
    with transaction() as conn:
        conn.execute("DELETE FROM tests WHERE kbs_id = ?", (kid,))
        if values:
            conn.executemany(
                "INSERT OR REPLACE INTO tests"
                "(id, kbs_id, subject_id, type, title, payload_json, fetched_at)"
                " VALUES(?,?,?,?,?,?,?)",
                values,
            )
    return len(values)


def _title_of(payload: dict[str, Any]) -> str:
    title = payload.get("title") or payload.get("name") or ""
    return str(title)[:300]


def list_tests(kid: int) -> list[dict[str, Any]]:
    rows = get_connection().execute(
        "SELECT * FROM tests WHERE kbs_id = ? ORDER BY id", (kid,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_test(tid: int) -> dict[str, Any] | None:
    row = get_connection().execute("SELECT * FROM tests WHERE id = ?", (tid,)).fetchone()
    return dict(row) if row else None


def all_tests(sid: int | None = None) -> list[dict[str, Any]]:
    if sid is None:
        rows = get_connection().execute("SELECT * FROM tests ORDER BY subject_id, kbs_id, id")
    else:
        rows = get_connection().execute(
            "SELECT * FROM tests WHERE subject_id = ? ORDER BY kbs_id, id", (sid,)
        )
    return [dict(r) for r in rows.fetchall()]


def _like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _filter_clause(
    search: str,
    subject_id: int | None,
    kbs_id: int | None,
    type_filter: str,
) -> tuple[str, list[Any]]:
    where: list[str] = []
    args: list[Any] = []
    if subject_id is not None:
        where.append("t.subject_id = ?")
        args.append(subject_id)
    if kbs_id is not None:
        where.append("t.kbs_id = ?")
        args.append(kbs_id)
    if type_filter == "supported":
        where.append(f"t.type IN ({config.SUPPORTED_TYPES_SQL})")
    elif type_filter == "other":
        where.append(
            f"(t.type IS NULL OR t.type = '' OR t.type NOT IN ({config.SUPPORTED_TYPES_SQL}))"
        )
    elif type_filter.startswith("type"):
        where.append("t.type = ?")
        args.append(type_filter)
    for token in search.lower().split():
        pattern = _like_pattern(token)
        where.append(
            "(ulower(t.title) LIKE ? ESCAPE '\\' OR CAST(t.id AS TEXT) LIKE ? ESCAPE '\\'"
            " OR ulower(t.type) LIKE ? ESCAPE '\\' OR ulower(k.title) LIKE ? ESCAPE '\\')"
        )
        args.extend([pattern] * 4)
    return (" WHERE " + " AND ".join(where)) if where else "", args


def query_tests(
    search: str = "",
    subject_id: int | None = None,
    kbs_id: int | None = None,
    type_filter: str = "all",
    limit: int = 5000,
    include_payload: bool = False,
) -> list[dict[str, Any]]:
    """Filtered test list for the table view."""
    where, args = _filter_clause(search, subject_id, kbs_id, type_filter)
    payload_column = ", t.payload_json" if include_payload else ""
    sql = (
        "SELECT t.id, t.kbs_id, t.subject_id, t.type, t.title, t.fetched_at"
        + payload_column
        + ","
        " s.title AS subject_title, k.title AS kbs_title, k.mode AS kbs_mode"
        " FROM tests t"
        " LEFT JOIN subjects s ON s.id = t.subject_id"
        " LEFT JOIN knowledge_bases k ON k.id = t.kbs_id"
        + where
        + " ORDER BY t.subject_id, t.kbs_id, t.id LIMIT ?"
    )
    args.append(int(limit))
    return [dict(r) for r in get_connection().execute(sql, args).fetchall()]


def query_tests_count(
    search: str = "",
    subject_id: int | None = None,
    kbs_id: int | None = None,
    type_filter: str = "all",
) -> int:
    where, args = _filter_clause(search, subject_id, kbs_id, type_filter)
    sql = (
        "SELECT COUNT(*) FROM tests t"
        " LEFT JOIN knowledge_bases k ON k.id = t.kbs_id"
        + where
    )
    row = get_connection().execute(sql, args).fetchone()
    return int(row[0] or 0)


# --------------------------------------------------------------------------- #
# aggregates
# --------------------------------------------------------------------------- #
def subject_stats(sid: int) -> dict[str, int]:
    row = get_connection().execute(
        "SELECT COUNT(*) AS total,"
        f" SUM(CASE WHEN type IN ({config.SUPPORTED_TYPES_SQL}) THEN 1 ELSE 0 END) AS supported,"
        " COUNT(DISTINCT kbs_id) AS kbs"
        " FROM tests WHERE subject_id = ?",
        (sid,),
    ).fetchone()
    return {
        "total": row["total"] or 0,
        "supported": row["supported"] or 0,
        "kbs": row["kbs"] or 0,
    }


def kbs_stats(kid: int) -> dict[str, int]:
    row = get_connection().execute(
        "SELECT COUNT(*) AS total,"
        f" SUM(CASE WHEN type IN ({config.SUPPORTED_TYPES_SQL}) THEN 1 ELSE 0 END) AS supported"
        " FROM tests WHERE kbs_id = ?",
        (kid,),
    ).fetchone()
    return {"total": row["total"] or 0, "supported": row["supported"] or 0}


def overall_stats() -> dict[str, int]:
    conn = get_connection()
    subjects = conn.execute("SELECT COUNT(*) FROM subjects").fetchone()[0]
    row = conn.execute(
        "SELECT COUNT(*) AS total,"
        f" SUM(CASE WHEN type IN ({config.SUPPORTED_TYPES_SQL}) THEN 1 ELSE 0 END) AS supported"
        " FROM tests"
    ).fetchone()
    fetched = conn.execute("SELECT MAX(fetched_at) FROM tests").fetchone()[0]
    return {
        "subjects": subjects or 0,
        "total": row["total"] or 0,
        "supported": row["supported"] or 0,
        "updated_at": fetched or 0,
    }


def tree_data() -> list[tuple[dict[str, Any], list[tuple[dict[str, Any], dict[str, int]]]]]:
    """Subjects with their knowledge bases and test counters (2 queries total)."""
    conn = get_connection()
    subjects = [dict(r) for r in conn.execute(
        "SELECT * FROM subjects ORDER BY title COLLATE NOCASE, id"
    ).fetchall()]
    kbs_rows = conn.execute(
        "SELECT k.*,"
        " COUNT(t.id) AS total,"
        f" SUM(CASE WHEN t.type IN ({config.SUPPORTED_TYPES_SQL}) THEN 1 ELSE 0 END) AS supported"
        " FROM knowledge_bases k LEFT JOIN tests t ON t.kbs_id = k.id"
        " GROUP BY k.id ORDER BY k.subject_id, k.id"
    ).fetchall()
    grouped: dict[int, list[tuple[dict[str, Any], dict[str, int]]]] = {}
    for row in kbs_rows:
        item = dict(row)
        counts = {"total": item.pop("total") or 0, "supported": item.pop("supported") or 0}
        grouped.setdefault(item["subject_id"], []).append((item, counts))
    return [(subject, grouped.get(subject["id"], [])) for subject in subjects]


# --------------------------------------------------------------------------- #
# maintenance
# --------------------------------------------------------------------------- #
def delete_subject(sid: int) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM tests WHERE subject_id = ?", (sid,))
        conn.execute("DELETE FROM knowledge_bases WHERE subject_id = ?", (sid,))
        conn.execute("DELETE FROM subjects WHERE id = ?", (sid,))


def delete_kbs(kid: int) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM tests WHERE kbs_id = ?", (kid,))
        conn.execute("DELETE FROM knowledge_bases WHERE id = ?", (kid,))


def clear_cache() -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM tests")
        conn.execute("DELETE FROM knowledge_bases")
        conn.execute("DELETE FROM subjects")
    with contextlib.suppress(sqlite3.Error):
        get_connection().execute("VACUUM")


def db_size_bytes() -> int:
    try:
        return database_path().stat().st_size
    except OSError:
        return 0
