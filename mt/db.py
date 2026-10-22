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

SCHEMA_VERSION = 3

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

CREATE TABLE IF NOT EXISTS courses (
    id           INTEGER PRIMARY KEY,
    title        TEXT    NOT NULL DEFAULT '',
    payload_json TEXT    NOT NULL DEFAULT '{}',
    fetched_at   INTEGER NOT NULL
);

--: A lesson belongs to a course; ``entity_lesson_id`` is the id used in URLs
--: and in the ``lessonId`` field of an answer.
CREATE TABLE IF NOT EXISTS lessons (
    id               INTEGER NOT NULL,
    course_id        INTEGER NOT NULL,
    entity_lesson_id INTEGER,
    title            TEXT    NOT NULL DEFAULT '',
    position         INTEGER NOT NULL DEFAULT 0,
    payload_json     TEXT    NOT NULL DEFAULT '{}',
    fetched_at       INTEGER NOT NULL,
    PRIMARY KEY (course_id, id)
);

--: ``curriculum_practice_block`` children of a lesson: subject + display data.
CREATE TABLE IF NOT EXISTS lesson_subjects (
    lesson_id  INTEGER NOT NULL,
    subject_id INTEGER NOT NULL,
    title      TEXT    NOT NULL DEFAULT '',
    position   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (lesson_id, subject_id)
);

CREATE INDEX IF NOT EXISTS idx_tests_kbs       ON tests(kbs_id);
CREATE INDEX IF NOT EXISTS idx_tests_subject   ON tests(subject_id);
CREATE INDEX IF NOT EXISTS idx_kbs_subject     ON knowledge_bases(subject_id);
CREATE INDEX IF NOT EXISTS idx_tests_type      ON tests(type);
CREATE INDEX IF NOT EXISTS idx_lessons_course  ON lessons(course_id);
CREATE INDEX IF NOT EXISTS idx_lesson_subjects ON lesson_subjects(subject_id);
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
# courses and lessons (v3 structure)
# --------------------------------------------------------------------------- #
def _supported_sum(column: str = "t.type") -> str:
    return f"SUM(CASE WHEN {column} IN ({config.SUPPORTED_TYPES_SQL}) THEN 1 ELSE 0 END)"


def upsert_course(cid: int, title: str, payload: dict[str, Any]) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO courses(id, title, payload_json, fetched_at) VALUES(?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                title        = excluded.title,
                payload_json = excluded.payload_json,
                fetched_at   = excluded.fetched_at
            """,
            (cid, title or f"Курс #{cid}", json.dumps(payload, ensure_ascii=False), now()),
        )


def get_course(cid: int) -> dict[str, Any] | None:
    row = get_connection().execute("SELECT * FROM courses WHERE id = ?", (cid,)).fetchone()
    return dict(row) if row else None


def _course_stat_rows() -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT c.*,
            (SELECT COUNT(*) FROM lessons l WHERE l.course_id = c.id) AS lesson_count,
            (SELECT COUNT(*) FROM (
                SELECT DISTINCT ls.subject_id FROM lessons l
                JOIN lesson_subjects ls ON ls.lesson_id = l.id
                WHERE l.course_id = c.id)) AS subject_count,
            (SELECT COUNT(DISTINCT k.id) FROM knowledge_bases k
                WHERE k.subject_id IN (
                    SELECT ls.subject_id FROM lessons l
                    JOIN lesson_subjects ls ON ls.lesson_id = l.id
                    WHERE l.course_id = c.id)) AS homework_count
        FROM courses c
        ORDER BY c.title COLLATE NOCASE, c.id
        """
    ).fetchall()
    totals: dict[int, tuple[int, int]] = {}
    for row in conn.execute(
        f"""
        SELECT cs.course_id AS course_id, COUNT(t.id) AS total,
               {_supported_sum()} AS supported
        FROM (
            SELECT DISTINCT l.course_id AS course_id, ls.subject_id AS subject_id
            FROM lessons l JOIN lesson_subjects ls ON ls.lesson_id = l.id
        ) cs
        LEFT JOIN tests t ON t.subject_id = cs.subject_id
        GROUP BY cs.course_id
        """
    ).fetchall():
        totals[int(row["course_id"])] = (row["total"] or 0, row["supported"] or 0)
    result = []
    for row in rows:
        item = dict(row)
        item["lesson_count"] = item.get("lesson_count") or 0
        item["subject_count"] = item.get("subject_count") or 0
        item["homework_count"] = item.get("homework_count") or 0
        item["total"], item["supported"] = totals.get(int(item["id"]), (0, 0))
        result.append(item)
    return result


def list_courses() -> list[dict[str, Any]]:
    return _course_stat_rows()


def course_stats(cid: int) -> dict[str, int]:
    row = next((item for item in _course_stat_rows() if item["id"] == cid), None)
    if row is None:
        return {"lessons": 0, "subjects": 0, "total": 0, "supported": 0}
    return {
        "lessons": row["lesson_count"],
        "subjects": row["subject_count"],
        "total": row["total"],
        "supported": row["supported"],
    }


def replace_course_lessons(course_id: int, lessons: Iterable[dict[str, Any]]) -> int:
    """Store the lessons of a course; lessons missing from *lessons* vanish."""
    stamp = now()
    values = []
    keep: set[int] = set()
    for lesson in lessons:
        lesson_id = int(lesson["id"])
        keep.add(lesson_id)
        values.append(
            (
                lesson_id,
                course_id,
                lesson.get("entity_id"),
                str(lesson.get("title") or "")[:300],
                int(lesson.get("position") or 0),
                json.dumps(lesson.get("payload") or {}, ensure_ascii=False),
                stamp,
            )
        )
    with transaction() as conn:
        existing = {
            row["id"]
            for row in conn.execute(
                "SELECT id FROM lessons WHERE course_id = ?", (course_id,)
            ).fetchall()
        }
        for stale in existing - keep:
            conn.execute("DELETE FROM lesson_subjects WHERE lesson_id = ?", (stale,))
            conn.execute(
                "DELETE FROM lessons WHERE course_id = ? AND id = ?", (course_id, stale)
            )
        if values:
            conn.executemany(
                """
                INSERT INTO lessons(id, course_id, entity_lesson_id, title, position,
                                    payload_json, fetched_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(course_id, id) DO UPDATE SET
                    entity_lesson_id = excluded.entity_lesson_id,
                    title            = excluded.title,
                    position         = excluded.position,
                    payload_json     = excluded.payload_json,
                    fetched_at       = excluded.fetched_at
                """,
                values,
            )
    return len(values)


def set_lesson_subjects(lesson_id: int, subjects: Iterable[dict[str, Any]]) -> None:
    values = [
        (
            int(subject["subject_id"]),
            lesson_id,
            str(subject.get("title") or "")[:300],
            int(subject.get("position") or 0),
        )
        for subject in subjects
    ]
    with transaction() as conn:
        conn.execute("DELETE FROM lesson_subjects WHERE lesson_id = ?", (lesson_id,))
        if values:
            conn.executemany(
                "INSERT OR REPLACE INTO lesson_subjects"
                "(subject_id, lesson_id, title, position) VALUES(?,?,?,?)",
                values,
            )


def get_lesson(lesson_id: int) -> dict[str, Any] | None:
    row = get_connection().execute(
        "SELECT * FROM lessons WHERE id = ? ORDER BY course_id LIMIT 1", (lesson_id,)
    ).fetchone()
    return dict(row) if row else None


def lesson_subject_ids(lesson_id: int) -> list[int]:
    rows = get_connection().execute(
        "SELECT subject_id FROM lesson_subjects WHERE lesson_id = ?"
        " ORDER BY position, subject_id",
        (lesson_id,),
    ).fetchall()
    return [int(row["subject_id"]) for row in rows]


def lesson_subjects(lesson_id: int) -> list[dict[str, Any]]:
    """Subjects of a lesson with test counters."""
    sql = f"""
        SELECT ls.subject_id AS id, ls.title AS lesson_title, ls.position,
               s.title AS subject_title,
               COUNT(t.id) AS total,
               {_supported_sum()} AS supported
        FROM lesson_subjects ls
        LEFT JOIN subjects s ON s.id = ls.subject_id
        LEFT JOIN tests t ON t.subject_id = ls.subject_id
        WHERE ls.lesson_id = ?
        GROUP BY ls.subject_id
        ORDER BY ls.position, ls.subject_id
    """
    rows = []
    for row in get_connection().execute(sql, (lesson_id,)).fetchall():
        item = dict(row)
        item["title"] = item.pop("subject_title") or item.pop("lesson_title") or f"Предмет #{item['id']}"
        item["total"] = item.get("total") or 0
        item["supported"] = item.get("supported") or 0
        rows.append(item)
    return rows


def subject_course_path(subject_id: int) -> dict[str, Any] | None:
    """Course and lesson a subject belongs to (for breadcrumbs)."""
    row = get_connection().execute(
        """
        SELECT l.id AS lesson_id, l.title AS lesson_title,
               c.id AS course_id, c.title AS course_title
        FROM lesson_subjects ls
        JOIN lessons l ON l.id = ls.lesson_id
        JOIN courses c ON c.id = l.course_id
        WHERE ls.subject_id = ?
        ORDER BY l.position, l.id LIMIT 1
        """,
        (subject_id,),
    ).fetchone()
    return dict(row) if row else None


def _kbs_rows_with_stats(subject_ids: list[int]) -> list[dict[str, Any]]:
    """Knowledge bases (homeworks) of the given subjects with test counters."""
    if not subject_ids:
        return []
    marks = ",".join("?" * len(subject_ids))
    sql = f"""
        SELECT k.*, COUNT(t.id) AS total, {_supported_sum()} AS supported
        FROM knowledge_bases k
        LEFT JOIN tests t ON t.kbs_id = k.id
        WHERE k.subject_id IN ({marks})
        GROUP BY k.id
        ORDER BY k.lesson_id IS NULL, k.lesson_id, k.id
    """
    rows = []
    for row in get_connection().execute(sql, subject_ids).fetchall():
        item = dict(row)
        item["total"] = item.get("total") or 0
        item["supported"] = item.get("supported") or 0
        rows.append(item)
    return rows


def subject_lessons(subject_id: int) -> list[dict[str, Any]]:
    """Lessons (занятия) of a subject, in course order."""
    rows = get_connection().execute(
        """
        SELECT DISTINCT l.* FROM lessons l
        JOIN lesson_subjects ls ON ls.lesson_id = l.id
        WHERE ls.subject_id = ?
        ORDER BY l.course_id, l.position, l.id
        """,
        (subject_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def lesson_homeworks(lesson_id: int) -> list[dict[str, Any]]:
    """Homeworks (knowledge bases) that belong to a lesson.

    The curriculum tree stores the *entity* lesson id in ``lesson_id``, which is
    exactly the value kept in ``lessons.entity_lesson_id``.
    """
    lesson = get_lesson(lesson_id)
    if not lesson or not lesson.get("entity_lesson_id"):
        return []
    subjects = [
        int(row["id"]) for row in lesson_subjects(lesson_id)
    ]
    rows = _kbs_rows_with_stats(subjects)
    return [row for row in rows if str(row.get("lesson_id")) == str(lesson["entity_lesson_id"])]


def subject_homeworks(subject_id: int) -> dict[str, Any]:
    """Lessons of a subject with their homeworks, plus unlinked homeworks.

    This is what the subject page renders: «Занятие 1 · 3 сентября» with the
    homework of that lesson inside.
    """
    lessons = subject_lessons(subject_id)
    rows = _kbs_rows_with_stats([subject_id])
    by_entity: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get("lesson_id") or "")
        if key:
            by_entity.setdefault(key, []).append(row)

    grouped: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    used: set[int] = set()
    for lesson in lessons:
        key = str(lesson.get("entity_lesson_id") or "")
        homeworks = by_entity.get(key, []) if key else []
        used.update(int(homework["id"]) for homework in homeworks)
        grouped.append((lesson, homeworks))
    orphans = [row for row in rows if int(row["id"]) not in used]
    if not grouped:
        orphans = rows
    return {"lessons": grouped, "orphans": orphans}


def course_homeworks(course_id: int) -> dict[str, Any]:
    """Same grouping for a whole course (subjects of all of its lessons)."""
    lessons = [
        dict(row)
        for row in get_connection().execute(
            "SELECT * FROM lessons WHERE course_id = ? ORDER BY position, id",
            (course_id,),
        ).fetchall()
    ]
    if not lessons:
        return {"lessons": [], "orphans": []}
    subject_ids = sorted({
        int(row["subject_id"])
        for row in get_connection().execute(
            "SELECT DISTINCT ls.subject_id FROM lessons l"
            " JOIN lesson_subjects ls ON ls.lesson_id = l.id WHERE l.course_id = ?",
            (course_id,),
        ).fetchall()
    })
    rows = _kbs_rows_with_stats(subject_ids)
    by_entity: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get("lesson_id") or "")
        if key:
            by_entity.setdefault(key, []).append(row)
    grouped = []
    used: set[int] = set()
    for lesson in lessons:
        key = str(lesson.get("entity_lesson_id") or "")
        homeworks = by_entity.get(key, []) if key else []
        used.update(int(homework["id"]) for homework in homeworks)
        grouped.append((lesson, homeworks))
    orphans = [row for row in rows if int(row["id"]) not in used]
    return {"lessons": grouped, "orphans": orphans}


def homework_tests(kbs_id: int) -> list[dict[str, Any]]:
    """Tests of a homework in display order."""
    return list_tests(kbs_id)


def homework_row(kbs_id: int) -> dict[str, Any] | None:
    kbs = get_kbs(kbs_id)
    if not kbs:
        return None
    counts = kbs_stats(kbs_id)
    kbs.update(counts)
    lesson = None
    if kbs.get("lesson_id"):
        lesson = get_connection().execute(
            "SELECT * FROM lessons WHERE entity_lesson_id = ?"
            " ORDER BY course_id, position LIMIT 1",
            (kbs["lesson_id"],),
        ).fetchone()
    kbs["lesson"] = dict(lesson) if lesson else None
    return kbs


def subject_of_homework(kbs_id: int) -> dict[str, Any] | None:
    row = get_connection().execute(
        "SELECT * FROM subjects WHERE id = (SELECT subject_id FROM knowledge_bases WHERE id = ?)",
        (kbs_id,),
    ).fetchone()
    return dict(row) if row else None


def course_subject_ids() -> set[int]:
    """Subjects that are reachable through a course (and thus synced with it)."""
    rows = get_connection().execute(
        "SELECT DISTINCT subject_id FROM lesson_subjects"
    ).fetchall()
    return {int(row["subject_id"]) for row in rows}


def lesson_id_for_subject(subject_id: int) -> int | None:
    """Entity lesson id of the first course lesson that contains *subject_id*.

    This is the value the answer endpoint expects, so it precedes any guess
    made from the curriculum tree or a pasted link.
    """
    row = get_connection().execute(
        """
        SELECT l.entity_lesson_id AS entity_id
        FROM lesson_subjects ls JOIN lessons l ON l.id = ls.lesson_id
        WHERE ls.subject_id = ? AND l.entity_lesson_id IS NOT NULL
        ORDER BY l.course_id, l.position, l.id LIMIT 1
        """,
        (subject_id,),
    ).fetchone()
    return int(row["entity_id"]) if row and row["entity_id"] else None


def course_lessons(course_id: int) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Lessons of a course in display order, each with its subjects."""
    lessons = [
        dict(row)
        for row in get_connection().execute(
            "SELECT * FROM lessons WHERE course_id = ? ORDER BY position, id",
            (course_id,),
        ).fetchall()
    ]
    if not lessons:
        return []
    marks = ",".join("?" * len(lessons))
    grouped: dict[int, list[dict[str, Any]]] = {int(row["id"]): [] for row in lessons}
    sql = f"""
        SELECT ls.lesson_id, ls.subject_id AS id, ls.title AS lesson_title, ls.position,
               s.title AS subject_title,
               COUNT(t.id) AS total,
               {_supported_sum()} AS supported
        FROM lesson_subjects ls
        LEFT JOIN subjects s ON s.id = ls.subject_id
        LEFT JOIN tests t ON t.subject_id = ls.subject_id
        WHERE ls.lesson_id IN ({marks})
        GROUP BY ls.lesson_id, ls.subject_id
        ORDER BY ls.position, ls.subject_id
    """
    lesson_ids = [int(row["id"]) for row in lessons]
    for row in get_connection().execute(sql, lesson_ids).fetchall():
        item = dict(row)
        lesson_key = int(item.pop("lesson_id"))
        grouped.setdefault(lesson_key, []).append({
            "id": item["id"],
            "title": item["subject_title"] or item["lesson_title"] or f"Предмет #{item['id']}",
            "position": item["position"] or 0,
            "total": item["total"] or 0,
            "supported": item["supported"] or 0,
        })
    return [(lesson, grouped.get(int(lesson["id"]), [])) for lesson in lessons]


def course_tree() -> list[tuple[dict[str, Any], list[tuple[dict[str, Any], list[dict[str, Any]]]]]]:
    """Courses → lessons → subjects, ready for the sidebar."""
    return [
        (course, course_lessons(course["id"]))
        for course in _course_stat_rows()
    ]


def delete_course(cid: int) -> None:
    """Forget the course structure; cached subjects and tests stay."""
    with transaction() as conn:
        conn.execute(
            "DELETE FROM lesson_subjects WHERE lesson_id IN"
            " (SELECT id FROM lessons WHERE course_id = ?)",
            (cid,),
        )
        conn.execute("DELETE FROM lessons WHERE course_id = ?", (cid,))
        conn.execute("DELETE FROM courses WHERE id = ?", (cid,))


def clear_courses() -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM lesson_subjects")
        conn.execute("DELETE FROM lessons")
        conn.execute("DELETE FROM courses")


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


def merge_kbs_tests(
    kid: int,
    sid: int,
    rows: Iterable[tuple[int, str | None, str, dict[str, Any]]],
) -> int:
    """Insert/update the tests of one knowledge base *without deleting* others.

    The library is a cache the user keeps: a response that happens to be shorter
    than what is already stored (pagination quirks, a test hidden for a while)
    must not wipe the rest.  Returns how many tests the homework holds now.
    """
    stamp = now()
    values = [
        (tid, kid, sid, (ttype or ""), (title or "")[:300],
         json.dumps(payload, ensure_ascii=False), stamp)
        for tid, ttype, title, payload in rows
    ]
    with transaction() as conn:
        if values:
            conn.executemany(
                "INSERT OR REPLACE INTO tests"
                "(id, kbs_id, subject_id, type, title, payload_json, fetched_at)"
                " VALUES(?,?,?,?,?,?,?)",
                values,
            )
        total = conn.execute(
            "SELECT COUNT(*) FROM tests WHERE kbs_id = ?", (kid,)
        ).fetchone()[0]
    return int(total or 0)


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
    courses = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
    return {
        "subjects": subjects or 0,
        "courses": courses or 0,
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
        conn.execute("DELETE FROM lesson_subjects")
        conn.execute("DELETE FROM lessons")
        conn.execute("DELETE FROM courses")
    with contextlib.suppress(sqlite3.Error):
        get_connection().execute("VACUUM")


def db_size_bytes() -> int:
    try:
        return database_path().stat().st_size
    except OSError:
        return 0
