"""Building the answer payload that the API expects.

MaximumTest test types (as observed in the web client):

``type1``-``type2``
    single / multiple choice — answer is a list of option ids.
``type3``, ``type4``
    text inputs — answer is the list of the right variants in order.
``type5``
    matching / sequencing — answer is one *variant* picked per right answer.

The previous implementation picked a random variant per right answer
independently, which can produce mixed-up pairs (left from one variant set,
right from another).  Here a consistent variant index is chosen once per test.
"""

from __future__ import annotations

import random
from typing import Any

from .models import AnswerOption, TestDetails, extract_right_answers, parse_options, test_details

__all__ = [
    "build_answer",
    "build_answer_for",
    "answer_preview",
    "extract_right_answers",
    "parse_options",
]


def _consistent_variant_index(right: list[AnswerOption], rng: random.Random | None) -> int:
    """Pick one variant column that is available for *all* right answers."""
    sizes = [len(option.variants) for option in right]
    if not sizes or min(sizes) == 0:
        return 0
    common = min(sizes)
    if common == 1:
        return 0
    return (rng or random).randrange(common)


def build_answer(
    payload_or_details: dict[str, Any] | TestDetails,
    *,
    rng: random.Random | None = None,
) -> list:
    """Return the answer payload for a test."""
    details = (
        payload_or_details
        if isinstance(payload_or_details, TestDetails)
        else _details_from_payload(payload_or_details)
    )
    right = [option for option in details.options if option.is_right]
    if not right:
        # Some payloads only ship `educationTestRightAnswers`.
        right = list(extract_right_answers(details.raw))
    right.sort(key=lambda option: option.sort)

    if details.type == "type5":
        index = _consistent_variant_index(right, rng)
        return [
            option.variants[index] if len(option.variants) > index else ""
            for option in right
        ]
    if details.type in ("type3", "type4"):
        return [option.variants[0] if option.variants else option.text for option in right]
    return [str(option.id) for option in right]


def build_answer_for(details: TestDetails) -> list:
    return build_answer(details)


def _details_from_payload(payload: dict[str, Any]) -> TestDetails:
    from .models import _lesson_id, _question_html, _title  # local import: private helpers

    return TestDetails(
        id=int(payload.get("id") or 0),
        subject_id=0,
        kbs_id=0,
        type=str(payload.get("type") or ""),
        title=_title(payload, "Тест"),
        question_html=_question_html(payload),
        question_text="",
        options=parse_options(payload),
        lesson_id=_lesson_id(payload),
        raw=payload,
    )


def answer_preview(details: TestDetails, limit: int = 3) -> str:
    """Short human-readable summary of the auto answer (for buttons/tooltips)."""
    try:
        answer = build_answer(details)
    except Exception:  # pragma: no cover - defensive, preview must never crash the UI
        return "—"
    if not answer:
        return "—"
    parts = [str(item) for item in answer[:limit]]
    suffix = " …" if len(answer) > limit else ""
    return ", ".join(parts) + suffix


def details_from_row(row: dict[str, Any]) -> TestDetails:
    return test_details(row)
