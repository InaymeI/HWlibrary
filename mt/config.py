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
APP_VERSION = "0.2.0"
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
    # Undocumented, used only for an optional "what can I open?" probe.
    "courses": "/api/v1/content/courses",
}

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
#: Pause between sync requests. The reference notes a 300-400 ms delay for
#: existing scripts and that rate limiting is unknown, so stay conservative.
SYNC_DELAY_MS = 300
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
LOG_PATH = CACHE_DIR / "app.log"


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
