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
    def right_options(self) -> tuple[AnswerOption, ...]:
        return tuple(option for option in self.options if option.is_right)

    @property
    def has_right_answers(self) -> bool:
        return bool(self.right_options)


#: Keys that usually carry a human readable name, best first.
_TITLE_KEYS = (
    "title", "name", "caption", "displayName", "display_name", "shortName",
    "fullName", "subjectName", "subject_name", "lessonName", "courseName",
    "label", "heading",
)
#: Strings that are just numbers/ids must never be shown as a name.
_NUMERIC_RE = re.compile(r"^[\s\d/._#№-]+$")
#: «Предмет #111130»: a course is never named after its own number, so any
#: digits after the word make it a placeholder.
_COURSE_PLACEHOLDER_RE = re.compile(
    r"^(?:предмет|курс|subject|course)\s*[#№:]?\s*\d+$", re.IGNORECASE
)
#: «Домашка №1» is a real name, so for the other kinds only «#» or a long
#: number counts as an id.
_PLACEHOLDER_RE = re.compile(
    r"^(?:домашка|урок|раздел|тест|задание|homework|lesson)"
    r"\s*(?:#\s*\d+|№\s*\d{4,})$",
    re.IGNORECASE,
)
#: «id 42», «ID: 42», «№ 42» — an id with a label.
_ID_LABEL_RE = re.compile(r"^(?:id|ид|no\.?|номер|#|№)\s*[:#№]?\s*\d+$", re.IGNORECASE)


def looks_like_id(text: str) -> bool:
    """True for "12345", "#12345", "Предмет #42", "id 42" and other non-names."""
    value = " ".join((text or "").split())
    if not value:
        return True
    if (
        _COURSE_PLACEHOLDER_RE.match(value)
        or _PLACEHOLDER_RE.match(value)
        or _ID_LABEL_RE.match(value)
    ):
        return True
    return bool(_NUMERIC_RE.match(value))


def pick_title(node: Any, *, depth: int = 0) -> str:
    """Find a human readable name anywhere inside an API object.

    The backend spreads names over ``title``/``name``/``entityInfo`` and a few
    nested containers, so a shallow lookup often fails and the UI ends up with
    «Предмет #111137».  This walks the object (breadth-first, bounded) and
    returns the first plausible name — never a bare number.
    """
    if depth > 4 or node is None:
        return ""
    if isinstance(node, str):
        text = strip_html(node)
        return "" if looks_like_id(text) else text
    if isinstance(node, list):
        for item in node:
            found = pick_title(item, depth=depth + 1)
            if found:
                return found
        return ""
    if not isinstance(node, dict):
        return ""

    for key in _TITLE_KEYS:
        value = node.get(key)
        if isinstance(value, str):
            text = strip_html(value)
            if text and not looks_like_id(text):
                return text

    # Preferred nested containers first, then everything else.
    for key in ("entityInfo", "entity_info", "educationTest", "course", "group",
                "educationGroup", "education_group", "data", "result", "meta",
                "lesson", "subject", "curriculum"):
        value = node.get(key)
        if isinstance(value, (dict, list)):
            found = pick_title(value, depth=depth + 1)
            if found:
                return found
    for key, value in node.items():
        if key in _TITLE_KEYS or not isinstance(value, (dict, list)):
            continue
        found = pick_title(value, depth=depth + 1)
        if found:
            return found
    return ""


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


def subject_title(payload: dict[str, Any] | None, fallback: str = "") -> str:
    """Name of a homework («домашка» = subject); never a bare id.

    Only the subject-ish fields are read: a wildcard search would happily
    return the name of its first section («Раздел 1») and rename the домашка.
    """
    if not isinstance(payload, dict):
        return fallback
    for key in ("title", "name", "subjectName", "subject_name", "caption", "displayName"):
        value = payload.get(key)
        if isinstance(value, str):
            text = strip_html(value)
            if text and not looks_like_id(text):
                return text
    for key in ("subject", "entityInfo", "entity_info", "data", "meta"):
        value = payload.get(key)
        if isinstance(value, dict):
            title = subject_title(value, "")
            if title:
                return title
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
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

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
            "payload": self.raw,
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


#: ``type`` values of education-group nodes that group lessons instead of being
#: a lesson themselves.  Some courses answer the list endpoint with such nodes
#: (``children: null``) and only reveal the lessons in ``/structures/<id>``.
BLOCK_TYPES = frozenset({
    "lessons_block", "lesson_block", "lessons_group", "lesson_group",
    "module", "section", "block", "group",
})
#: Substrings that mark a container even when the exact type is unknown.
_BLOCK_MARKERS = ("block", "group", "module", "section")
#: ``entityInfo.type`` values that are pauses in the schedule, not lessons.
REST_TYPES = frozenset({"vacation", "holiday", "break", "rest", "day_off"})
#: Keys a ``/structures/<id>`` payload may wrap the node in.
_STRUCTURE_KEYS = ("data", "structure", "node", "result")


def course_nodes(payload: Any) -> list[dict[str, Any]]:
    """Top-level items of an education-group page (``data`` or the list itself)."""
    return [item for item in _payload_list(payload) if isinstance(item, dict)]


def node_id(node: Any) -> int | None:
    """``id`` of an education-group node, as an int."""
    return _as_int(node.get("id")) if isinstance(node, dict) else None


def is_lesson_node(node: Any) -> bool:
    """True when an education-group item is a lesson, not a container of them.

    A lesson always carries ``entityInfo`` with its own id.  A ``lessons_block``
    («III семестр») has ``entityInfo: null`` and ``children: null`` — its lessons
    are only returned by ``GET …/structures/<block id>``.  Shapes that match
    neither rule keep the previous behaviour (treated as a lesson), so courses
    that never use blocks are parsed exactly as before.
    """
    if not isinstance(node, dict):
        return False
    entity = node.get("entityInfo") or node.get("entity_info") or {}
    if isinstance(entity, dict) and _as_int(entity.get("id")):
        return True
    kind = str(node.get("type") or "").strip().lower()
    if kind in BLOCK_TYPES or any(marker in kind for marker in _BLOCK_MARKERS):
        return False
    children = node.get("children")
    return not (isinstance(children, list) and children)


def is_rest_node(node: Any) -> bool:
    """True for «Каникулы: Зима» rows: they have an entity but are not lessons."""
    entity = node.get("entityInfo") or node.get("entity_info") or {}
    if not isinstance(entity, dict):
        return False
    return str(entity.get("type") or "").strip().lower() in REST_TYPES


def is_lesson_item(node: Any) -> bool:
    """True for a child of a block that is a lesson or a nested block.

    A lesson structure also contains theory/practice blocks; their
    ``entityInfo`` carries a subject id, so they must not be mistaken for
    lessons while a block is being expanded.
    """
    if not isinstance(node, dict):
        return False
    kind = str(node.get("type") or "").strip().lower()
    if not kind:
        return True  # unknown: let the caller look inside
    if any(marker in kind for marker in ("practice", "homework", "theory", "test")):
        return False
    if kind in BLOCK_TYPES or any(marker in kind for marker in _BLOCK_MARKERS):
        return True
    if "lesson" in kind:
        return True
    # anything else with an entity of its own is a lesson-like row
    entity = node.get("entityInfo") or node.get("entity_info") or {}
    return bool(isinstance(entity, dict) and _as_int(entity.get("id")))


def structure_node(payload: Any) -> dict[str, Any]:
    """The node itself from a ``/structures/<id>`` response."""
    node: Any = payload
    for _ in range(4):
        if not isinstance(node, dict):
            return {}
        for key in _STRUCTURE_KEYS:
            value = node.get(key)
            if isinstance(value, dict):
                node = value
                break
        else:
            return node
    return node if isinstance(node, dict) else {}


def structure_children(payload: Any) -> list[dict[str, Any]]:
    """Children of a structure node — the lessons inside a ``lessons_block``."""
    children = structure_node(payload).get("children")
    if isinstance(children, list):
        return [child for child in children if isinstance(child, dict)]
    return []


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
        title = pick_title(item)
        position = _as_int(item.get("position") or item.get("sort") or item.get("order"))
        lessons.append(
            LessonEntry(
                id=lesson_id,
                entity_id=entity_id,
                title=title or f"Урок #{entity_id or lesson_id}",
                position=index if position is None else position,
                raw=item,
            )
        )
    return lessons


#: Block type names that mean «this node is a домашка».
_PRACTICE_TYPES = frozenset({
    "curriculum_practice_block",
    "curriculum_practice",
    "curriculum_homework_block",
    "curriculum_homework",
    "practice_block",
    "practice",
    "lesson_practice",
    "homework",
})
#: ``entityInfo.type`` values that mark a homework entity.
_PRACTICE_ENTITY_TYPES = frozenset({"subject", "practice", "homework"})


def _is_practice_node(kind: str, entity: dict[str, Any], node: dict[str, Any]) -> bool:
    lowered = kind.strip().lower()
    if "practice" in lowered or "homework" in lowered:
        return True
    if lowered in _PRACTICE_TYPES:
        return True
    entity_type = str(
        entity.get("type") or entity.get("entityType") or entity.get("entity_type") or ""
    ).strip().lower()
    if entity_type in _PRACTICE_ENTITY_TYPES:
        return True
    # A node that explicitly points at a subject is a homework reference even
    # when its own type is missing or unknown.
    return any(
        _as_int(source.get(key)) is not None
        for source in (entity, node)
        for key in ("subjectId", "subject_id")
    )


def _practice_blocks_in(node: Any, out: list[PracticeBlock], depth: int = 0) -> None:
    """Collect homeworks from any nesting the backend chooses.

    The structure endpoint has been seen to put practice blocks under
    ``children``, ``blocks``, ``content`` and even inside theory groups, so the
    walker descends into every container instead of a fixed key list.
    """
    if depth > 10 or node is None:
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
    kind = str(node.get("type") or node.get("blockType") or node.get("kind") or "")
    if not kind:
        kind = str(entity.get("type") or entity.get("entityType") or "")

    if _is_practice_node(kind, entity, node):
        subject_id = _as_int(
            entity.get("id")
            or entity.get("subjectId")
            or entity.get("subject_id")
            or node.get("subjectId")
            or node.get("subject_id")
            or node.get("entityId")
            or node.get("entity_id")
            or node.get("id")
        )
        if subject_id is not None:
            title = pick_title(node)
            position = _as_int(
                node.get("position") or node.get("sort") or node.get("order")
            )
            out.append(
                PracticeBlock(
                    subject_id=subject_id,
                    title=title or f"Домашка #{subject_id}",
                    position=len(out) if position is None else position,
                )
            )

    for key, value in node.items():
        if key in ("entityInfo", "entity_info"):
            continue
        if isinstance(value, (dict, list)):
            _practice_blocks_in(value, out, depth + 1)


def extract_practice_blocks(payload: Any) -> list[PracticeBlock]:
    """Collect homeworks (subjects) from a lesson structure payload."""
    found: list[PracticeBlock] = []
    _practice_blocks_in(payload, found)
    unique: dict[int, PracticeBlock] = {}
    for block in found:
        unique.setdefault(block.subject_id, block)
    return sorted(unique.values(), key=lambda block: block.position)


#: Exact keys that carry the предмет name.  ``courseTitleForModule`` is the
#: field the site itself uses; the rest are tolerated spellings.
_COURSE_TITLE_KEYS = (
    "courseTitleForModule", "course_title_for_module", "courseTitle",
    "course_title", "courseName", "course_name", "moduleTitle", "module_title",
    "educationGroupName", "education_group_name", "groupName", "group_name",
)
#: Any key like ``courseTitle``/``course_title``/``moduleName`` is a candidate.
_COURSE_TITLE_KEY_RE = re.compile(
    r"^(?:course|module|educationgroup|education_group|group)"
    r"(?:title|name|titleformodule|title_for_module)$",
    re.IGNORECASE,
)


def _normalize_name(value: Any) -> str:
    """A display-ready name from an API value, or ``""``."""
    if isinstance(value, dict):
        # ``{"value": "..."}`` / ``{"title": "..."}`` wrappers are common.
        for key in ("value", "title", "name", "text"):
            if key in value:
                text = _normalize_name(value[key])
                if text:
                    return text
        return ""
    if not isinstance(value, (str, int)):
        return ""
    text = " ".join(strip_html(str(value)).split())
    if not text or looks_like_id(text):
        return ""
    return text


def find_course_title(payload: Any) -> str:
    """The предмет name from ``courseTitleForModule`` (or similar) anywhere.

    The education-groups response may omit (or numerically mask) the top-level
    name while lesson items, practice blocks and homework payloads carry
    ``courseTitleForModule`` — the field the site itself uses for the предмет.
    The walk is deep (the field sits several levels down in real responses) and
    key-based rather than path-based, so a nesting change does not break it.
    """
    def walk(node: Any, depth: int) -> str:
        if depth > 14 or node is None:
            return ""
        if isinstance(node, list):
            for item in node:
                found = walk(item, depth + 1)
                if found:
                    return found
            return ""
        if not isinstance(node, dict):
            return ""
        for key in _COURSE_TITLE_KEYS:
            if key in node:
                text = _normalize_name(node[key])
                if text:
                    return text
        for key, value in node.items():
            if isinstance(key, str) and _COURSE_TITLE_KEY_RE.match(key):
                text = _normalize_name(value)
                if text:
                    return text
        for value in node.values():
            if isinstance(value, (dict, list)):
                found = walk(value, depth + 1)
                if found:
                    return found
        return ""

    return walk(payload, 0)


def extract_course_title(payload: Any, fallback: str = "") -> str:
    """Name of a course («предмет»), never a bare id.

    The education-groups payload usually carries the course name at the top
    level (``title``/``name``) or inside a course-ish container.  Lesson items
    are only used for *course* fields — taking a lesson's own title would
    label the whole subject with one lesson's name.
    """
    # 0. The field the site itself uses for the предмет name.
    module_title = find_course_title(payload)
    if module_title:
        return module_title
    course_keys = (
        "courseName", "course_name", "groupName", "group_name",
        "educationGroupName", "education_group_name",
    )
    if isinstance(payload, dict):
        # 1. Root level name.
        for key in ("title", "name", "caption", "displayName", "display_name", "fullName"):
            value = payload.get(key)
            if isinstance(value, str):
                text = strip_html(value)
                if text and not looks_like_id(text):
                    return text
        for key in course_keys:
            value = payload.get(key)
            if isinstance(value, str):
                text = strip_html(value)
                if text and not looks_like_id(text):
                    return text
        # 2. Course-ish containers at the root.
        for key in ("course", "group", "educationGroup", "education_group",
                    "entityInfo", "entity_info", "meta", "curriculum"):
            value = payload.get(key)
            if isinstance(value, dict):
                title = extract_course_title(value, "")
                if title:
                    return title
        # 3. Course fields of the lesson items.
        for item in _payload_list(payload):
            if not isinstance(item, dict):
                continue
            entity = item.get("entityInfo") or item.get("entity_info") or {}
            if not isinstance(entity, dict):
                entity = {}
            for source in (item, entity):
                for key in course_keys:
                    value = source.get(key)
                    if isinstance(value, str):
                        text = strip_html(value)
                        if text and not looks_like_id(text):
                            return text
                for nested_key in ("course", "educationGroup", "education_group", "group"):
                    nested = source.get(nested_key)
                    if isinstance(nested, dict):
                        title = extract_course_title(nested, "")
                        if title:
                            return title
            # A single-lesson course page may name the course after its lesson.
            if entity.get("type") in ("course", "education_group") or item.get("type") == "course":
                title = pick_title(item)
                if title:
                    return title
    return fallback


# --------------------------------------------------------------------------- #
# lesson presentation (date, status, progress, topics)
# --------------------------------------------------------------------------- #
_RU_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)

_DATE_KEY_RE = re.compile(
    r"(date|day|start|begin|beginat|startsat|starts_at|scheduled|planned)", re.I
)
_ISO_DATE_RE = re.compile(
    r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"
    r"(?:[T ](?P<h1>\d{2}):(?P<min1>\d{2}))?"
)
_DOTTED_DATE_RE = re.compile(r"(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4})")
_TIME_RANGE_RE = re.compile(r"(?P<h1>\d{1,2}:\d{2})\s*[-–—]\s*(?P<h2>\d{1,2}:\d{2})")


def format_lesson_date(value: Any) -> str:
    """``"2025-09-03T19:40:00"`` → ``"3 сентября"`` (empty when unknown)."""
    text = str(value or "").strip()
    if not text:
        return ""
    match = _ISO_DATE_RE.search(text)
    if match:
        day = int(match.group("d"))
        month = int(match.group("m"))
        if 1 <= month <= 12:
            return f"{day} {_RU_MONTHS[month - 1]}"
    match = _DOTTED_DATE_RE.search(text)
    if match:
        day, month = int(match.group("d")), int(match.group("m"))
        if 1 <= month <= 12:
            return f"{day} {_RU_MONTHS[month - 1]}"
    return ""


def _first_string(node: Any, keys: tuple[str, ...], depth: int = 0) -> Any:
    if not isinstance(node, dict) or depth > 3:
        return None
    for key in keys:
        value = node.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return value
    for value in node.values():
        if isinstance(value, dict):
            found = _first_string(value, keys, depth + 1)
            if found is not None:
                return found
    return None


def _topics(node: Any) -> tuple[str, ...]:
    if not isinstance(node, dict):
        return ()
    collected: list[str] = []
    for key in ("topics", "themes", "topicsList", "lessonTopics", "theme", "topic"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            collected.append(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, dict):
                    title = item.get("title") or item.get("name") or item.get("text")
                    if title:
                        collected.append(str(title))
                elif item is not None and str(item).strip():
                    collected.append(str(item))
    for container in ("entityInfo", "entity", "lesson"):
        inner = node.get(container)
        if isinstance(inner, dict):
            collected.extend(_topics(inner))
    return tuple(dict.fromkeys(strip_html(text) for text in collected if strip_html(text)))


def _status(node: dict[str, Any]) -> tuple[str, str]:
    raw = _first_string(
        node,
        ("visitStatus", "attendance", "status", "state", "visitState", "visited", "isVisited"),
    )
    if raw is None:
        return "", ""
    text = str(raw).strip().lower()
    if text in {"visited", "attended", "present", "true", "1", "yes"}:
        return "Посещено", "success"
    if text in {"viewed", "watched", "opened"}:
        return "Просмотрено", "outline"
    if text in {"missing", "absent", "false", "0", "no"}:
        return "Пропущено", "danger"
    if text in {"planned", "future", "scheduled"}:
        return "Запланировано", "muted"
    value = strip_html(raw)
    return (value, "success") if value else ("", "")


def _progress(node: dict[str, Any]) -> int | None:
    for key in ("progress", "homeworkProgress", "progressPercent", "completedPercent"):
        value = node.get(key)
        if isinstance(value, dict):
            value = value.get("percent") or value.get("value") or value.get("progress")
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            continue
        if 0 <= number <= 100:
            return number
    return None


@dataclass(frozen=True)
class LessonMeta:
    """Human facing bits of a lesson: when it was, what it covered."""

    number: int | None
    title: str
    date_text: str
    status: str
    status_kind: str
    progress: int | None
    time_text: str
    topics: tuple[str, ...]

    @property
    def heading(self) -> str:
        if self.number is not None:
            return f"Урок {self.number}"
        return self.title or "Урок"

    @property
    def online(self) -> bool:
        return bool(self.time_text)


def lesson_meta(row: dict[str, Any], payload: dict[str, Any] | None = None) -> LessonMeta:
    """Read date/status/progress/topics out of a cached lesson row."""
    node = payload if isinstance(payload, dict) and payload else {}
    if not node:
        try:
            node = json.loads(row.get("payload_json") or "{}")
        except (TypeError, ValueError):
            node = {}
    entity = node.get("entityInfo") if isinstance(node.get("entityInfo"), dict) else {}

    date_text = ""
    for source in (node, entity):
        for key, value in source.items():
            if _DATE_KEY_RE.search(str(key)) and isinstance(value, (str, int, float)):
                date_text = format_lesson_date(value)
                if date_text:
                    break
        if date_text:
            break

    number = _as_int(
        node.get("number") or node.get("index") or node.get("lessonNumber") or node.get("position")
    )
    topics = _topics(node) or _topics(entity)
    status, status_kind = _status(node)
    progress = _progress(node)

    time_text = ""
    start = node.get("timeStart") or node.get("startTime") or node.get("time_start")
    end = node.get("timeEnd") or node.get("endTime") or node.get("time_end")
    if start and end:
        time_text = f"{start}-{end}"
    if not time_text:
        match = _TIME_RANGE_RE.search(str(node.get("time") or node.get("timeRange") or ""))
        if match:
            time_text = f"{match.group('h1')}-{match.group('h2')}"
    if not time_text:
        hour = node.get("hour") or node.get("startHour")
        if hour is not None and end:
            time_text = f"{hour}-{end}"

    title = strip_html(node.get("title") or node.get("name") or entity.get("title") or "")
    return LessonMeta(
        number=number,
        title=title or str(row.get("title") or ""),
        date_text=date_text,
        status=status,
        status_kind=status_kind,
        progress=progress,
        time_text=time_text,
        topics=topics,
    )


def test_title(row: dict[str, Any]) -> str:
    return str(row.get("title") or f"Тест #{row.get('id')}")


def plural(n: int, one: str, few: str, many: str) -> str:
    """Russian plural form for *n*: 1 тест, 2 теста, 5 тестов."""
    value = abs(int(n))
    if value % 10 == 1 and value % 100 != 11:
        return one
    if 2 <= value % 10 <= 4 and not 12 <= value % 100 <= 14:
        return few
    return many


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
