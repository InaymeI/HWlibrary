"""Parsing of MaximumTest API payloads into small immutable objects.

The UI must never guess where a title or a question lives inside the raw JSON:
everything is normalised here, once, and cached.
"""

from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from . import config

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def strip_html(value: Any) -> str:
    """Turn a fragment of HTML into a single line of readable text."""
    if value is None:
        return ""
    text = _TAG_RE.sub(" ", str(value))
    text = html.unescape(text)
    return _SPACE_RE.sub(" ", text).strip()


def normalize_html(value: Any) -> str:
    """Return an HTML fragment that QTextDocument can render nicely."""
    if value is None:
        return ""
    if isinstance(value, dict):
        value = value.get("html") or value.get("content") or value.get("text") or ""
    text = str(value).strip()
    if not text:
        return ""
    if "<" not in text:
        return f"<p>{html.escape(text).replace(chr(10), '<br>')}</p>"
    return text


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class AnswerOption:
    id: str
    text: str
    variants: tuple[str, ...]
    is_right: bool
    sort: int

    @property
    def display(self) -> str:
        if self.text:
            return self.text
        return "—"


@dataclass(frozen=True)
class TestDetails:
    """Everything the UI needs about a single test."""

    id: int
    subject_id: int
    kbs_id: int
    type: str
    title: str
    question_html: str
    question_text: str
    options: tuple[AnswerOption, ...]
    lesson_id: int | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def supported(self) -> bool:
        return config.is_supported(self.type)

    @property
    def type_label(self) -> str:
        return self.type or "—"

    @property
    def right_options(self) -> tuple[AnswerOption, ...]:
        return tuple(option for option in self.options if option.is_right)

    @property
    def has_right_answers(self) -> bool:
        return bool(self.right_options)


def _variant_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if item is not None and str(item) != "")
    return (str(value),)


def _parse_option(item: dict[str, Any], index: int) -> AnswerOption | None:
    if not isinstance(item, dict):
        return None
    variants = _variant_list(item.get("variants") or item.get("variant") or item.get("value"))
    text = " · ".join(dict.fromkeys(variants)) if variants else strip_html(
        item.get("text") or item.get("title") or item.get("content")
    )
    is_right = bool(
        item.get("isRightAnswer")
        if item.get("isRightAnswer") is not None
        else item.get("isRight") or item.get("right") or item.get("isCorrect")
    )
    # ``sort`` may legitimately be 0 (the API starts numbering at zero), so it
    # must not be collapsed into "missing" by a falsy fallback.
    sort_value = _as_int(item.get("sort"))
    return AnswerOption(
        id=str(item.get("id", index)),
        text=text,
        variants=variants,
        is_right=is_right,
        sort=index if sort_value is None else sort_value,
    )


#: Right answers may be nested (GET /tests/{id} shape from the API reference):
#: ``data.studentTestResult.educationTest.educationTestRightAnswers``
#: or ``data.educationTestRightAnswers``.
_NESTED_ANSWER_PATHS = (
    ("data", "studentTestResult", "educationTest"),
    ("studentTestResult", "educationTest"),
    ("data",),
)


def _answers_from(node: Any) -> list[Any] | None:
    if not isinstance(node, dict):
        return None
    for key in (
        "educationTestAnswers",
        "educationTestRightAnswers",
        "educationTest",
        "answers",
        "testAnswers",
        "variants",
    ):
        value = node.get(key)
        if isinstance(value, list) and value:
            return value
    return None


_TEST_NODE_KEYS = (
    "content", "question", "text", "statement", "type",
    "educationTestAnswers", "educationTestRightAnswers",
)


def test_node(payload: dict[str, Any]) -> dict[str, Any]:
    """Find the object that actually holds the test fields.

    Answers may be wrapped in ``data.studentTestResult.educationTest``; in that
    case the condition and type live there as well, not at the top level.
    """
    if not isinstance(payload, dict):
        return {}
    if any(key in payload for key in _TEST_NODE_KEYS):
        return payload
    for path in _NESTED_ANSWER_PATHS:
        node: Any = payload
        for step in path:
            node = node.get(step) if isinstance(node, dict) else None
        if isinstance(node, dict) and any(key in node for key in _TEST_NODE_KEYS):
            return node
    return payload


def _raw_answers(payload: dict[str, Any]) -> list[Any]:
    found = _answers_from(payload)
    if found:
        return found
    for path in _NESTED_ANSWER_PATHS:
        node: Any = payload
        for step in path:
            node = node.get(step) if isinstance(node, dict) else None
        found = _answers_from(node)
        if found:
            return found
    return []


def extract_right_answers(payload: dict[str, Any]) -> list[AnswerOption]:
    """Right answer options sorted by their ``sort`` field."""
    options = [opt for opt in parse_options(payload) if opt.is_right]
    return sorted(options, key=lambda option: option.sort)


def parse_options(payload: dict[str, Any]) -> tuple[AnswerOption, ...]:
    parsed = []
    for index, item in enumerate(_raw_answers(payload)):
        option = _parse_option(item, index)
        if option is not None:
            parsed.append(option)
    return tuple(parsed)


def _question_html(payload: dict[str, Any]) -> str:
    for key in ("content", "question", "text", "statement", "description", "body"):
        if payload.get(key):
            return normalize_html(payload[key])
    return ""


def _title(payload: dict[str, Any], fallback: str) -> str:
    title = payload.get("title") or payload.get("name") or payload.get("caption") or ""
    title = strip_html(title) if "<" in str(title) else str(title).strip()
    return title or fallback


def _lesson_id(payload: dict[str, Any]) -> int | None:
    for key in ("lessonId", "lesson_id", "lesson"):
        value = payload.get(key)
        if isinstance(value, dict):
            value = value.get("id")
        lesson = _as_int(value)
        if lesson:
            return lesson
    return None


@lru_cache(maxsize=256)
def _parse_cached(
    test_id: int,
    subject_id: int,
    kbs_id: int,
    cached_type: str,
    fetched_at: int,
    payload_json: str,
) -> TestDetails:
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    node = test_node(payload)
    ttype = str(node.get("type") or payload.get("type") or cached_type or "").strip()
    question = _question_html(node) or _question_html(payload)
    return TestDetails(
        id=test_id,
        subject_id=subject_id,
        kbs_id=kbs_id,
        type=ttype,
        title=_title(node, _title(payload, f"Тест #{test_id}")),
        question_html=question,
        question_text=strip_html(question),
        options=parse_options(payload),
        lesson_id=_lesson_id(node) or _lesson_id(payload),
        raw=payload,
    )


def test_details(row: dict[str, Any]) -> TestDetails:
    """Build :class:`TestDetails` from a database row (cached by id+mtime)."""
    return _parse_cached(
        int(row["id"]),
        int(row["subject_id"]),
        int(row["kbs_id"]),
        str(row.get("type") or ""),
        int(row.get("fetched_at") or 0),
        str(row["payload_json"]),
    )


def clear_cache() -> None:
    _parse_cached.cache_clear()


# --------------------------------------------------------------------------- #
# subjects / knowledge bases
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class KbsEntry:
    id: int
    mode: str
    title: str
    lesson_id: int | None

    def as_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mode": self.mode,
            "title": self.title,
            "lesson_id": self.lesson_id,
        }


def _kbs_from_node(node: dict[str, Any], mode: str) -> KbsEntry | None:
    kid = _as_int(node.get("id") or node.get("knowledgeBaseId") or node.get("kbsId"))
    if kid is None:
        return None
    title = node.get("title") or node.get("name") or node.get("caption") or ""
    lesson = node.get("lessonId") or node.get("lesson_id") or node.get("lesson")
    if isinstance(lesson, dict):
        lesson = lesson.get("id")
    return KbsEntry(
        id=kid,
        mode=str(node.get("mode") or mode or ""),
        title=strip_html(title),
        lesson_id=_as_int(lesson),
    )


CONTAINER_KEYS = (
    "tree", "modes", "knowledgeBases", "knowledge_bases", "sections",
    "data", "course", "curriculum", "result", "items", "children",
)


def extract_kbs_entries(base: dict[str, Any] | None) -> list[KbsEntry]:
    """Collect knowledge bases from a curriculum-subject payload.

    Handles the documented ``tree: {online: [...], offline: [...]}`` shape and
    the looser structures the backend has been seen to return (nested
    ``children`` / ``knowledgeBases`` lists, a flat list, ``data``/``course``
    wrappers).  Entries are de-duplicated by id and ordered by mode.
    """
    if not isinstance(base, dict):
        return []

    found: dict[int, KbsEntry] = {}

    def add(entry: KbsEntry) -> None:
        current = found.get(entry.id)
        if current is None:
            found[entry.id] = entry
            return
        # Keep the richer entry, but never lose the lesson id.
        found[entry.id] = KbsEntry(
            id=entry.id,
            mode=current.mode or entry.mode,
            title=current.title or entry.title,
            lesson_id=current.lesson_id or entry.lesson_id,
        )

    def walk(node: Any, mode: str, depth: int, allow_add: bool) -> None:
        if depth > 8 or node is None:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, mode, depth + 1, True)
            return
        if not isinstance(node, dict):
            return
        mode_keys = [key for key in node if key in config.MODES]
        if mode_keys:
            # A {online: [...], offline: [...]} container, not a knowledge base.
            for key in mode_keys:
                walk(node[key], key, depth + 1, True)
            for key, value in node.items():
                if key not in config.MODES and key in CONTAINER_KEYS:
                    walk(value, mode, depth + 1, True)
            return
        if allow_add:
            entry = _kbs_from_node(node, mode)
            if entry is not None:
                add(entry)
        nested_mode = str(node.get("mode") or mode)
        for key, value in node.items():
            if key in CONTAINER_KEYS and isinstance(value, (list, dict)):
                walk(value, nested_mode, depth + 1, True)

    walk(base, "", 0, False)

    ordered_modes = list(config.MODES)
    return sorted(
        found.values(),
        key=lambda entry: (
            ordered_modes.index(entry.mode) if entry.mode in ordered_modes else 99,
            entry.id,
        ),
    )


def unwrap_test(item: dict[str, Any]) -> dict[str, Any] | None:
    """Return the actual test object from list items that wrap it.

    The reference mentions the ``studentTestResult.educationTest`` wrapper for
    the single-test endpoint; being tolerant here costs nothing.
    """
    if item.get("id") is not None:
        return item
    for key in ("educationTest", "test", "data"):
        inner = item.get(key)
        if isinstance(inner, dict):
            if inner.get("id") is not None:
                return inner
            nested = unwrap_test(inner)
            if nested is not None:
                return nested
    return None


def extract_tests(payload: Any) -> list[dict[str, Any]]:
    """Pull a list of test objects out of an unknown response shape."""
    if isinstance(payload, list):
        return [
            test
            for item in payload
            if isinstance(item, dict)
            for test in [unwrap_test(item)]
            if test is not None
        ]
    if isinstance(payload, dict):
        for key in ("tests", "items", "data", "educationTests", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return extract_tests(value)
            if isinstance(value, dict):
                nested = extract_tests(value)
                if nested:
                    return nested
        unwrapped = unwrap_test(payload)
        if unwrapped is not None and (
            "type" in unwrapped or "educationTestAnswers" in unwrapped
        ):
            return [unwrapped]
    return []


def subject_title(payload: dict[str, Any] | None, fallback: str) -> str:
    if isinstance(payload, dict):
        for key in ("title", "name", "subjectName", "caption"):
            value = payload.get(key)
            if value:
                return strip_html(value) or fallback
    return fallback


# --------------------------------------------------------------------------- #
# courses, lessons and practice blocks (v3 API)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LessonEntry:
    """One lesson of a course.

    ``id`` is the database lesson id used by the v3 endpoints; ``entity_id`` is
    the "entity lesson id" from the site URL — exactly the value the answer
    endpoint expects in ``lessonId``.
    """

    id: int
    entity_id: int | None
    title: str
    position: int

    @property
    def subtitle(self) -> str:
        if self.entity_id and self.entity_id != self.id:
            return f"урок #{self.id} · entity {self.entity_id}"
        return f"урок #{self.id}"

    def as_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "title": self.title,
            "position": self.position,
        }


@dataclass(frozen=True)
class PracticeBlock:
    """A ``curriculum_practice_block`` child of a lesson structure."""

    subject_id: int
    title: str
    position: int

    def as_row(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "title": self.title,
            "position": self.position,
        }


def _payload_list(payload: Any) -> list[Any]:
    """Unwrap ``{"data": [...]}`` and friends into a plain list."""
    node: Any = payload
    for _ in range(5):
        if isinstance(node, list):
            return node
        if not isinstance(node, dict):
            return []
        for key in ("data", "items", "lessons", "results", "rows", "response"):
            value = node.get(key)
            if isinstance(value, (list, dict)):
                node = value
                break
        else:
            return []
    return node if isinstance(node, list) else []


def extract_lessons(payload: Any) -> list[LessonEntry]:
    """Read lessons from ``GET /api/v3/student/education-groups/{id}``.

    The lesson's own ``id`` addresses the structure endpoint, while
    ``entityInfo.id`` is the entity lesson id used as ``lessonId`` in answers.
    """
    lessons: list[LessonEntry] = []
    seen: set[int] = set()
    for index, item in enumerate(_payload_list(payload)):
        if not isinstance(item, dict):
            continue
        entity = item.get("entityInfo") or item.get("entity_info") or {}
        if not isinstance(entity, dict):
            entity = {}
        lesson_id = _as_int(item.get("id"))
        entity_id = _as_int(
            entity.get("id")
            or item.get("entityId")
            or item.get("entity_id")
            or item.get("entityLessonId")
        )
        if lesson_id is None:
            lesson_id = entity_id
        if lesson_id is None or lesson_id in seen:
            continue
        seen.add(lesson_id)
        title = strip_html(
            item.get("title")
            or item.get("name")
            or entity.get("title")
            or entity.get("name")
            or ""
        )
        position = _as_int(item.get("position") or item.get("sort") or item.get("order"))
        lessons.append(
            LessonEntry(
                id=lesson_id,
                entity_id=entity_id,
                title=title or f"Урок #{entity_id or lesson_id}",
                position=index if position is None else position,
            )
        )
    return lessons


def _practice_blocks_in(node: Any, out: list[PracticeBlock], depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(node, list):
        for item in node:
            _practice_blocks_in(item, out, depth + 1)
        return
    if not isinstance(node, dict):
        return
    entity = node.get("entityInfo") or node.get("entity_info") or {}
    if not isinstance(entity, dict):
        entity = {}
    kind = str(node.get("type") or node.get("blockType") or entity.get("type") or "")
    subject_id = _as_int(
        entity.get("id")
        or node.get("entityId")
        or node.get("entity_id")
        or (node.get("id") if "practice" in kind else None)
    )
    is_practice = kind in ("curriculum_practice_block", "practice_block", "curriculum_practice")
    if is_practice and subject_id is not None:
        title = strip_html(
            entity.get("title")
            or entity.get("name")
            or node.get("title")
            or node.get("name")
            or ""
        )
        position = _as_int(node.get("position") or node.get("sort") or node.get("order"))
        out.append(
            PracticeBlock(
                subject_id=subject_id,
                title=title or f"Практика #{subject_id}",
                position=len(out) if position is None else position,
            )
        )
    for key in ("children", "items", "blocks", "structure", "data", "elements"):
        value = node.get(key)
        if isinstance(value, (list, dict)):
            _practice_blocks_in(value, out, depth + 1)


def extract_practice_blocks(payload: Any) -> list[PracticeBlock]:
    """Collect practice blocks (subjects) from a lesson structure payload."""
    found: list[PracticeBlock] = []
    _practice_blocks_in(payload, found)
    unique: dict[int, PracticeBlock] = {}
    for block in found:
        unique.setdefault(block.subject_id, block)
    return list(unique.values())


def extract_course_title(payload: Any, fallback: str) -> str:
    """Best-effort course name; the API may omit it entirely."""
    if isinstance(payload, dict):
        for key in ("title", "name", "caption", "courseName"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return strip_html(value) or fallback
        for key in ("course", "group", "educationGroup", "meta", "data"):
            value = payload.get(key)
            if isinstance(value, dict):
                title = extract_course_title(value, "")
                if title:
                    return title
    return fallback


def test_title(row: dict[str, Any]) -> str:
    return str(row.get("title") or f"Тест #{row.get('id')}")


def relative_time(timestamp: int | float | None) -> str:
    """Human friendly "5 минут назад" for cache age."""
    if not timestamp:
        return "никогда"
    delta = max(0, int(time.time() - float(timestamp)))
    if delta < 60:
        return "только что"
    if delta < 3600:
        return f"{delta // 60} мин назад"
    if delta < 86400:
        return f"{delta // 3600} ч назад"
    if delta < 86400 * 7:
        return f"{delta // 86400} дн назад"
    import datetime as _dt

    return _dt.datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y")
