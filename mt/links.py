"""Parsing of MaximumTest links into navigation targets.

The site exposes several entry points, all of which the app accepts as a
single pasted string:

``/student/courses/111137/lessons``
    предмет: раскрывается в уроки → домашки → разделы → задания;

``/lesson/4772366/subjects/51610/practice/kbs/8931/tests/289165?type=online``
    конкретное задание; ``/lesson/<id>`` — это урок (``entity lesson id``),
    ``?type=`` — режим;

``/subjects/51610/`` и ``/kbs/8931/``
    домашка и её раздел.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

from . import config

_TEST_RE = re.compile(
    r"/lesson/(?P<lesson>\d+)/subjects/(?P<subject>\d+)/practice"
    r"(?:/kbs/(?P<kbs>\d+))?"
    r"(?:/tests/(?P<test>\d+))?"
)
_SUBJECT_RE = re.compile(r"/subjects/(?P<subject>\d+)")
_LESSON_SUBJECT_RE = re.compile(r"/lesson/(?P<lesson>\d+)/subjects/(?P<subject>\d+)")
_COURSE_RE = re.compile(r"/student/courses/(?P<course>\d+)")
_KBS_RE = re.compile(r"/(?:knowledgebase|kbs)/(?P<kbs>\d+)")
_MODE_RE = re.compile(r"[?&](?:type|mode)=(?P<mode>[a-z]+)")
_BARE_ID_RE = re.compile(r"^\d{2,}$")
_ANY_ID_RE = re.compile(r"\d{2,}")


@dataclass(frozen=True)
class ParsedLink:
    """A recognised part of the site the app can open or synchronise."""

    kind: str  #: ``course`` | ``test`` | ``subject`` | ``kbs``
    course_id: int | None = None
    #: ``entity_lesson_id`` from ``/lesson/<id>/`` — this is the value the
    #: answer endpoint expects in ``lessonId``.
    lesson_id: int | None = None
    subject_id: int | None = None
    kbs_id: int | None = None
    test_id: int | None = None
    mode: str = ""

    @property
    def is_course(self) -> bool:
        return self.kind == "course"


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_link(text: str | None) -> ParsedLink | None:
    """Recognise a pasted URL (or a bare id) and return what it points at.

    The order matters: a task URL contains course, lesson, subject, kbs and
    mode at once, so the most specific pattern is tried first.
    """
    raw = unquote((text or "").strip())
    if not raw:
        return None

    if _BARE_ID_RE.match(raw):
        return ParsedLink(kind="subject", subject_id=int(raw))

    mode_match = _MODE_RE.search(raw)
    mode = (mode_match.group("mode") if mode_match else "") or ""
    if mode not in config.MODES:
        mode = ""

    course_match = _COURSE_RE.search(raw)
    course_id = _int(course_match.group("course")) if course_match else None

    test_match = _TEST_RE.search(raw)
    if test_match:
        subject_id = _int(test_match.group("subject"))
        lesson_id = _int(test_match.group("lesson"))
        kbs_id = _int(test_match.group("kbs"))
        test_id = _int(test_match.group("test"))
        kind = "test" if test_id is not None else "subject"
        return ParsedLink(
            kind=kind,
            course_id=course_id,
            lesson_id=lesson_id,
            subject_id=subject_id,
            kbs_id=kbs_id,
            test_id=test_id,
            mode=mode,
        )

    subject_match = _LESSON_SUBJECT_RE.search(raw) or _SUBJECT_RE.search(raw)
    if subject_match:
        return ParsedLink(
            kind="subject",
            course_id=course_id,
            lesson_id=_int(subject_match.groupdict().get("lesson")),
            subject_id=_int(subject_match.group("subject")),
            mode=mode,
        )

    if course_id is not None:
        return ParsedLink(kind="course", course_id=course_id, mode=mode)

    kbs_match = _KBS_RE.search(raw)
    if kbs_match:
        return ParsedLink(kind="kbs", kbs_id=_int(kbs_match.group("kbs")), mode=mode)

    # Last resort: the first reasonably long number in the string.  Keeps the
    # old "paste an id" behaviour working.
    any_id = _ANY_ID_RE.search(raw)
    if any_id:
        return ParsedLink(kind="subject", subject_id=int(any_id.group(0)))
    return None
