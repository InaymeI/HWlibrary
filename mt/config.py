import os
from pathlib import Path

APP_NAME = "maximumtest-desktop"

def _xdg(env: str, default: str) -> Path:
    base = os.environ.get(env) or os.path.expanduser(default)
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p

DATA_DIR    = _xdg("XDG_DATA_HOME",   "~/.local/share")
CONFIG_DIR  = _xdg("XDG_CONFIG_HOME", "~/.config")
CACHE_DIR   = _xdg("XDG_CACHE_HOME",  "~/.cache")

DB_PATH      = DATA_DIR / "data.sqlite3"
COOKIES_PATH = DATA_DIR / "cookies.json"

BASE_URL = "https://education.maximumtest.ru"

ENDPOINTS = {
    "curriculum_subject": "/api/v1/content/practice/curriculumsubject/{subject_id}",
    "kbs_tests": "/api/v1/content/practice/curriculumsubject/{subject_id}/knowledgebase/{kbs_id}/{mode}/tests",
    "post_answer": "/api/v1/content/practice/curriculumsubject/{subject_id}/tests/{test_id}",
    # Endpoint "list my courses" undocumented. Fill after inspecting devtools.
    "courses": "/api/v1/content/courses",
}

MODES = ("online", "offline", "aftertheory")
SUPPORTED_TYPES = frozenset({"type1", "type2", "type3", "type4", "type5"})

def is_supported(t: str | None) -> bool:
    return bool(t) and t in SUPPORTED_TYPES
