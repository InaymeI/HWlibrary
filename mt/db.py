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
from .models import find_course_title, looks_like_id, subject_title

SCHEMA_VERSION = 4

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
    if version < 4:
        _migrate_v4()
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


def _migrate_v4() -> None:
    """Replace cached id-like предмет names with the real ones (one-off).

    Older builds stored «Предмет #111137» (or a bare id) because they never
    looked at ``courseTitleForModule``.  The payloads are already in the cache,
    so the names can be repaired without another sync.
    """
    repair_course_titles()


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
    if not row:
        return None
    subject = dict(row)
    if title_is_generic(subject.get("title")):
        real = subject_title_from_cache(int(sid))
        if real:
            subject["title"] = real
    return subject


def rename_subject(sid: int, title: str) -> None:
    """Store a name the user typed by hand (overrides the automatic one)."""
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
            (cid, title or f"Предмет #{cid}", json.dumps(payload, ensure_ascii=False), now()),
        )


def get_course(cid: int) -> dict[str, Any] | None:
    row = get_connection().execute("SELECT * FROM courses WHERE id = ?", (cid,)).fetchone()
    if not row:
        return None
    course = dict(row)
    if title_is_generic(course.get("title")):
        real = course_title_from_cache(int(cid))
        if real:
            course["title"] = real
    return course


def _course_stat_rows() -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT c.*,
            (SELECT COUNT(*) FROM lessons l WHERE l.course_id = c.id) AS lesson_count,
            (SELECT COUNT(*) FROM (
                SELECT DISTINCT ls.subject_id FROM lessons l
                JOIN lesson_subjects ls ON ls.lesson_id = l.id
                WHERE l.course_id = c.id)) AS homework_count,
            (SELECT COUNT(DISTINCT k.id) FROM knowledge_bases k
                WHERE k.subject_id IN (
                    SELECT ls.subject_id FROM lessons l
                    JOIN lesson_subjects ls ON ls.lesson_id = l.id
                    WHERE l.course_id = c.id)) AS section_count
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
        if title_is_generic(item.get("title")):
            # Old caches may still hold «Предмет #111137»: read the name from
            # the payloads that are already on disk.
            real = course_title_from_cache(int(item["id"]))
            if real:
                item["title"] = real
        item["lesson_count"] = item.get("lesson_count") or 0
        item["homework_count"] = item.get("homework_count") or 0
        item["section_count"] = item.get("section_count") or 0
        item["subject_count"] = item["homework_count"]  # backwards compatible name
        item["total"], item["supported"] = totals.get(int(item["id"]), (0, 0))
        result.append(item)
    return result


def rename_course(cid: int, title: str) -> None:
    with transaction() as conn:
        conn.execute("UPDATE courses SET title = ? WHERE id = ?", (title, cid))


def title_is_generic(title: str | None) -> bool:
    """True for «Предмет #111137», «#111130» or a bare id like ``111137``.

    «Домашка №1» is a real name from the site and is *not* generic.
    """
    return looks_like_id(str(title or ""))


def _load_json(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}


def subject_title_from_cache(subject_id: int) -> str:
    """The домашка name recoverable from its cached payload."""
    row = get_connection().execute(
        "SELECT payload_json FROM subjects WHERE id = ?", (subject_id,)
    ).fetchone()
    if not row:
        return ""
    payload = _load_json(row["payload_json"])
    title = subject_title(payload, "") if isinstance(payload, dict) else ""
    return "" if title_is_generic(title) else title


def course_title_from_cache(cid: int) -> str:
    """The предмет name recoverable from payloads already in the cache.

    Looks at the course payload, then the lessons of the course (their items
    carry ``courseTitleForModule``), then the homeworks of the course.  Used to
    repair old caches without a network round-trip.
    """
    conn = get_connection()
    row = conn.execute("SELECT payload_json FROM courses WHERE id = ?", (cid,)).fetchone()
    if row:
        title = find_course_title(_load_json(row["payload_json"]))
        if title:
            return title
    for lesson in conn.execute(
        "SELECT payload_json FROM lessons WHERE course_id = ?", (cid,)
    ).fetchall():
        title = find_course_title(_load_json(lesson["payload_json"]))
        if title:
            return title
    for subject in conn.execute(
        """
        SELECT s.payload_json AS payload_json FROM subjects s
        WHERE s.id IN (
            SELECT ls.subject_id FROM lessons l
            JOIN lesson_subjects ls ON ls.lesson_id = l.id
            WHERE l.course_id = ?
        )
        """,
        (cid,),
    ).fetchall():
        title = find_course_title(_load_json(subject["payload_json"]))
        if title:
            return title
    return ""


def repair_course_titles() -> int:
    """Rename every предмет/домашка with an id-like name; returns the count."""
    conn = get_connection()
    updates = []
    for row in conn.execute("SELECT id, title FROM courses").fetchall():
        if not title_is_generic(row["title"]):
            continue
        title = course_title_from_cache(int(row["id"]))
        if title and not title_is_generic(title):
            updates.append((title, int(row["id"])))
    if updates:
        conn.executemany("UPDATE courses SET title = ? WHERE id = ?", updates)
    subject_updates = []
    for row in conn.execute("SELECT id, title FROM subjects").fetchall():
        if not title_is_generic(row["title"]):
            continue
        title = subject_title_from_cache(int(row["id"]))
        if title and not title_is_generic(title):
            subject_updates.append((title, int(row["id"])))
    if subject_updates:
        conn.executemany("UPDATE subjects SET title = ? WHERE id = ?", subject_updates)
    if updates or subject_updates:
        conn.commit()
    return len(updates) + len(subject_updates)


def course_title_is_generic(cid: int) -> bool:
    row = get_connection().execute(
        "SELECT title FROM courses WHERE id = ?", (cid,)
    ).fetchone()
    return True if row is None else title_is_generic(row["title"])


def list_courses() -> list[dict[str, Any]]:
    return _course_stat_rows()


def course_stats(cid: int) -> dict[str, int]:
    row = next((item for item in _course_stat_rows() if item["id"] == cid), None)
    if row is None:
        return {"lessons": 0, "homeworks": 0, "sections": 0, "total": 0, "supported": 0}
    return {
        "lessons": row["lesson_count"],
        "homeworks": row["homework_count"],
        "sections": row["section_count"],
        "subjects": row["homework_count"],
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
        subject_title = item.pop("subject_title")
        lesson_title = item.pop("lesson_title")
        item["title"] = subject_title or lesson_title or f"Домашка #{item['id']}"
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
    """Knowledge bases (sections) of the given homeworks, with test counters."""
    if not subject_ids:
        return []
    marks = ",".join("?" * len(subject_ids))
    sql = f"""
        SELECT k.*, COUNT(t.id) AS total, {_supported_sum()} AS supported
        FROM knowledge_bases k
        LEFT JOIN tests t ON t.kbs_id = k.id
        WHERE k.subject_id IN ({marks})
        GROUP BY k.id
        ORDER BY k.id
    """
    rows = []
    for row in get_connection().execute(sql, subject_ids).fetchall():
        item = dict(row)
        item["total"] = item.get("total") or 0
        item["supported"] = item.get("supported") or 0
        rows.append(item)
    return rows




def _homework_select(where: str) -> str:
    """Homeworks = «subjects» (practice blocks) with their counters.

    A homework (subject) owns knowledge bases (sections), and a lesson owns
    homeworks through ``lesson_subjects`` — that link comes from the lesson
    structure and is the only reliable one (``knowledge_bases.lesson_id`` is
    often empty or points at another entity).
    """
    return f"""
        SELECT ls.subject_id AS id,
               ls.lesson_id AS lesson_id,
               ls.position AS position,
               COALESCE(NULLIF(s.title, ''), NULLIF(ls.title, ''),
                        'Домашка #' || ls.subject_id) AS title,
               COALESCE(NULLIF(ls.title, ''), NULLIF(s.title, ''),
                        'Домашка #' || ls.subject_id) AS lesson_subject_title,
               (SELECT COUNT(*) FROM knowledge_bases k
                    WHERE k.subject_id = ls.subject_id) AS sections,
               (SELECT COUNT(*) FROM tests t
                    WHERE t.subject_id = ls.subject_id) AS total,
               (SELECT COALESCE({_supported_sum()}, 0) FROM tests t
                    WHERE t.subject_id = ls.subject_id) AS supported
        FROM lesson_subjects ls
        LEFT JOIN subjects s ON s.id = ls.subject_id
        {where}
        ORDER BY ls.position, ls.subject_id
    """


def _run_homework_select(where: str, args: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for row in get_connection().execute(_homework_select(where), args).fetchall():
        item = dict(row)
        if title_is_generic(item.get("title")):
            real = subject_title_from_cache(int(item["id"]))
            if real:
                item["title"] = real
        item["sections"] = item.get("sections") or 0
        item["total"] = item.get("total") or 0
        item["supported"] = item.get("supported") or 0
        rows.append(item)
    return rows


def lesson_homeworks(lesson_id: int) -> list[dict[str, Any]]:
    """Homeworks of a lesson: the practice blocks of its structure."""
    return _run_homework_select("WHERE ls.lesson_id = ?", [int(lesson_id)])


def course_homeworks(course_id: int) -> dict[str, Any]:
    """Lessons of a course, each with its homeworks (practice blocks)."""
    lessons = [
        dict(row)
        for row in get_connection().execute(
            "SELECT * FROM lessons WHERE course_id = ? ORDER BY position, id",
            (course_id,),
        ).fetchall()
    ]
    grouped: dict[int, list[dict[str, Any]]] = {int(lesson["id"]): [] for lesson in lessons}
    rows = _run_homework_select(
        "WHERE ls.lesson_id IN (SELECT id FROM lessons WHERE course_id = ?)", [course_id]
    )
    for row in rows:
        grouped.setdefault(int(row["lesson_id"]), []).append(row)
    return {
        "lessons": [(lesson, grouped.get(int(lesson["id"]), [])) for lesson in lessons],
        "homeworks": rows,
    }


def homework_sections(subject_id: int) -> list[dict[str, Any]]:
    """Sections (knowledge bases) of a homework with test counters."""
    return _kbs_rows_with_stats([subject_id])


def homework_tests(subject_id: int) -> list[dict[str, Any]]:
    """All tests of a homework, ordered by its sections."""
    rows = get_connection().execute(
        """
        SELECT t.* FROM tests t
        JOIN knowledge_bases k ON k.id = t.kbs_id
        WHERE t.subject_id = ?
        ORDER BY k.id, t.id
        """,
        (subject_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def homework_row(subject_id: int) -> dict[str, Any] | None:
    """Homework header data: title, counters and where it sits."""
    rows = _run_homework_select("WHERE ls.subject_id = ?", [int(subject_id)])
    subject = get_subject(subject_id)
    if not rows and not subject:
        return None
    base = rows[0] if rows else {}
    counts = subject_stats(subject_id)
    homework = dict(base)
    homework["id"] = int(subject_id)
    for candidate in (
        (subject or {}).get("title"),
        base.get("title"),
        subject_title_from_cache(int(subject_id)),
    ):
        if candidate and not title_is_generic(candidate):
            homework["title"] = candidate
            break
    else:
        homework["title"] = f"Домашка #{subject_id}"
    homework["sections"] = len(_kbs_rows_with_stats([subject_id]))
    homework["total"] = counts["total"]
    homework["supported"] = counts["supported"]
    path = subject_course_path(subject_id)
    homework["lesson"] = None
    homework["course"] = None
    if path:
        homework["lesson"] = get_lesson(path["lesson_id"])
        homework["course"] = get_course(path["course_id"])
    return homework


def course_lessons_tree(course_id: int | None = None) -> list[tuple[dict[str, Any], list[tuple[dict[str, Any], list[dict[str, Any]]]]]]:
    """Courses → lessons → homeworks, ready for the sidebar."""
    result = []
    for course in _course_stat_rows():
        if course_id is not None and course["id"] != course_id:
            continue
        groups = course_homeworks(course["id"])
        result.append((course, groups["lessons"]))
    return result


def standalone_homeworks() -> list[dict[str, Any]]:
    """Homeworks that are not reachable through any course (synced by subject)."""
    rows = get_connection().execute(
        f"""
        SELECT s.id AS id, s.title AS title,
               (SELECT COUNT(*) FROM knowledge_bases k
                    WHERE k.subject_id = s.id) AS sections,
               (SELECT COUNT(*) FROM tests t WHERE t.subject_id = s.id) AS total,
               (SELECT COALESCE({_supported_sum()}, 0) FROM tests t
                    WHERE t.subject_id = s.id) AS supported
        FROM subjects s
        WHERE s.id NOT IN (SELECT DISTINCT subject_id FROM lesson_subjects)
        ORDER BY s.title COLLATE NOCASE, s.id
        """
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        if title_is_generic(item.get("title")):
            real = subject_title_from_cache(int(item["id"]))
            if real:
                item["title"] = real
        item["sections"] = item.get("sections") or 0
        item["total"] = item.get("total") or 0
        item["supported"] = item.get("supported") or 0
        result.append(item)
    return result


def course_subject_ids() -> set[int]:
    """Subjects that are reachable through a course (and thus synced with it)."""
    rows = get_connection().execute(
        "SELECT DISTINCT subject_id FROM lesson_subjects"
    ).fetchall()
    return {int(row["subject_id"]) for row in rows}


def course_id_for_subject(subject_id: int) -> int | None:
    """First course whose lesson contains this homework (if any)."""
    row = get_connection().execute(
        """
        SELECT l.course_id AS course_id
        FROM lesson_subjects ls
        JOIN lessons l ON l.id = ls.lesson_id
        WHERE ls.subject_id = ?
        ORDER BY l.position, l.id LIMIT 1
        """,
        (int(subject_id),),
    ).fetchone()
    return int(row["course_id"]) if row else None


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
    """Lessons of a course in display order, each with its homeworks."""
    return db_course_homeworks_lessons(course_homeworks(course_id))


def db_course_homeworks_lessons(groups: dict[str, Any]) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    return list(groups.get("lessons") or [])


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
            " OR ulower(t.type) LIKE ? ESCAPE '\\' OR ulower(k.title) LIKE ? ESCAPE '\\'"
            " OR ulower(s.title) LIKE ? ESCAPE '\\'"
            " OR t.subject_id IN (SELECT ls.subject_id FROM lessons l"
            " JOIN lesson_subjects ls ON ls.lesson_id = l.id"
            " JOIN courses c ON c.id = l.course_id"
            " WHERE ulower(c.title) LIKE ? ESCAPE '\\'))"
        )
        args.extend([pattern] * 6)
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
        " LEFT JOIN subjects s ON s.id = t.subject_id"
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
