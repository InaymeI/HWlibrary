"""Application constants, paths and endpoint definitions.

Nothing in this module touches the disk at import time: directories are created
lazily by :func:`ensure_dirs` / the database / cookie store.  This keeps the
module safe to import from tests and from short-lived CLI helpers.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "maximumtest-desktop"
APP_TITLE = "MaximumTest Library"
APP_VERSION = "0.5.7"
ORG_NAME = "maximumtest-desktop"

# ``MT_*`` overrides are handy for development (demo databases, staging hosts).
BASE_URL = os.environ.get("MT_BASE_URL", "https://education.maximumtest.ru").rstrip("/")

ENDPOINTS = {
    "curriculum_subject": "/api/v1/content/practice/curriculumsubject/{subject_id}",
    "kbs_tests": (
        "/api/v1/content/practice/curriculumsubject/{subject_id}"
        "/knowledgebase/{kbs_id}/{mode}/tests"
    ),
    "post_answer": "/api/v1/content/practice/curriculumsubject/{subject_id}/tests/{test_id}",
    # v3 API: a course ("education group") expands into lessons; each lesson
    # structure lists its practice blocks, whose entityInfo.id is the subject.
    "education_group": "/api/v3/student/education-groups/{course_id}",
    "lesson_structure": (
        "/api/v3/student/education-groups/{course_id}/structures/{lesson_id}"
    ),
}

#: Lessons per request; the backend pages by cursor, not by page number.
COURSE_PAGE_SIZE = 50
#: Safety limit for course pagination (a course never has 500 lessons).
COURSE_MAX_PAGES = 10

MODES = ("online", "offline", "aftertheory")
#: Types the application can render and answer.
SUPPORTED_TYPES = frozenset({"type1", "type2", "type3", "type4", "type5"})
SUPPORTED_TYPES_SQL = ", ".join(f"'{t}'" for t in sorted(SUPPORTED_TYPES))

REQUEST_TIMEOUT = (10, 30)  # (connect, read) seconds
#: Retries for idempotent GETs (handled by urllib3).
REQUEST_RETRIES = 2
#: POST /tests raises retries to 3 attempts with a 400 ms pause (API reference).
POST_ATTEMPTS = 3
POST_RETRY_DELAY = 0.4
#: Pause between sync requests, in seconds: ``random.uniform(low, high)`` before
#: every request.  Roughly a second with jitter — the reference notes that rate
#: limiting is unknown, and a human-paced rhythm is the safest bet.
#: Override with ``MT_SYNC_DELAY="0.6,1.4"`` (or ``0`` to disable).
SYNC_DELAY_RANGE = (0.6, 1.4)

#: How much rarer the deep walk is: it walks every course, lesson, homework and
#: section, so the same rhythm would mean hundreds of requests in a row.
DEEP_SYNC_PACE = 1.5

#: A ``lessons_block`` may contain lessons, and those may nest further; the
#: depth limit keeps a malformed answer from looping forever.
COURSE_STRUCTURE_DEPTH = 4


def _delay_range_from_env(name: str = "MT_SYNC_DELAY") -> tuple[float, float] | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    text = raw.strip().replace(",", " ").split()
    try:
        values = [max(0.0, float(part)) for part in text]
    except ValueError:
        return None
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (values[0], values[0])
    low, high = values[0], values[1]
    return (min(low, high), max(low, high))


_env_delay = _delay_range_from_env()
if _env_delay is not None:
    SYNC_DELAY_RANGE = _env_delay

_env_deep_delay = _delay_range_from_env("MT_DEEP_SYNC_DELAY")
if _env_deep_delay is not None:
    DEEP_SYNC_DELAY_RANGE = _env_deep_delay
else:
    DEEP_SYNC_DELAY_RANGE = (
        round(SYNC_DELAY_RANGE[0] * DEEP_SYNC_PACE, 3),
        round(SYNC_DELAY_RANGE[1] * DEEP_SYNC_PACE, 3),
    )


def delay_range(deep: bool = False) -> tuple[float, float]:
    """Pause range in seconds: the deep walk deliberately runs rarer."""
    return DEEP_SYNC_DELAY_RANGE if deep else SYNC_DELAY_RANGE


def sync_delay(deep: bool = False) -> float:
    """A random pause (in seconds) to sleep before the next request."""
    import random

    # ``MT_SYNC_DELAY=0`` (tests, cron) disables pacing for both modes.
    if SYNC_DELAY_RANGE[1] <= 0:
        return 0.0
    low, high = delay_range(deep)
    if high <= 0:
        return 0.0
    if high <= low:
        return max(0.0, low)
    return random.uniform(max(0.0, low), high)
#: Time range imitated in `spentTime` for an answer (API reference: 45-90 s).
SPENT_TIME_RANGE = (45, 90)


def _base_dir(env: str, default: str, override_env: str | None = None) -> Path:
    if override_env and os.environ.get(override_env):
        return Path(os.environ[override_env]).expanduser()
    base = os.environ.get(env) or os.path.expanduser(default)
    return Path(base).expanduser() / APP_NAME


DATA_DIR = _base_dir("XDG_DATA_HOME", "~/.local/share", "MT_DATA_DIR")
CONFIG_DIR = _base_dir("XDG_CONFIG_HOME", "~/.config", "MT_CONFIG_DIR")
CACHE_DIR = _base_dir("XDG_CACHE_HOME", "~/.cache", "MT_CACHE_DIR")

DB_PATH = DATA_DIR / "data.sqlite3"
COOKIES_PATH = DATA_DIR / "cookies.json"


def ensure_dirs() -> None:
    """Create the application directories (idempotent)."""
    for directory in (DATA_DIR, CONFIG_DIR, CACHE_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def is_supported(t: str | None) -> bool:
    return bool(t) and t in SUPPORTED_TYPES


def mode_label(mode: str | None) -> str:
    return {
        "online": "онлайн",
        "offline": "офлайн",
        "aftertheory": "после теории",
    }.get(mode or "", mode or "—")
