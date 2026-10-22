"""Building the answer payload that the API expects.

Test types (according to the internal API reference):

``type1``
    single choice — answer is the id of the right option.
``type2``
    multiple choice — answer is the list of the right option ids.
``type3``
    matching — answer is the list of option texts taken in ``sort`` order.
``type4``
    sequencing — same as ``type3``.
``type5``
    free text input: **one field per right answer**.  Each field may list
    several acceptable spellings in ``variants``, so a variant is chosen per
    field independently (``random.choice``); an empty string when there are no
    variants.

The result is always a ``list`` of strings/ids ready to be sent as ``answer``.
"""

from __future__ import annotations

import random
from typing import Any

from .models import AnswerOption, TestDetails, extract_right_answers, parse_options

__all__ = [
    "build_answer",
    "build_answer_for",
    "answer_preview",
    "extract_right_answers",
    "parse_options",
]

#: ``type5`` fields may accept several spellings; pick one per field.
TEXT_INPUT_TYPES = ("type5",)
#: Types whose answer is the right variants in ``sort`` order.
ORDERED_TEXT_TYPES = ("type3", "type4")


def _pick_variant(field: AnswerOption, rng: random.Random | None) -> str:
    """Return one acceptable text for a ``type5`` field ("" when unavailable)."""
    variants = [variant for variant in field.variants if variant != ""]
    if not variants:
        return ""
    return (rng or random).choice(variants)


def build_answer(
    payload_or_details: dict[str, Any] | TestDetails,
    *,
    rng: random.Random | None = None,
) -> list:
    """Return the ``answer`` array for a test.

    ``rng`` makes the result deterministic in tests.
    """
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

    if details.type in TEXT_INPUT_TYPES:
        return [_pick_variant(option, rng) for option in right]
    if details.type in ORDERED_TEXT_TYPES:
        return [
            option.variants[0] if option.variants else option.text
            for option in right
        ]
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
    """Short, *stable* summary of the auto answer for tables and tooltips.

    ``type5`` accepts several spellings per field; a fixed seed keeps the
    preview from flickering between refreshes.
    """
    try:
        answer = build_answer(details, rng=random.Random(details.id))
    except Exception:  # pragma: no cover - defensive, preview must never crash the UI
        return "—"
    if not answer:
        return "—"
    parts = [str(item) for item in answer[:limit]]
    suffix = " …" if len(answer) > limit else ""
    return ", ".join(parts) + suffix


