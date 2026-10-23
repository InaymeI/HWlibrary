"""Explicit, cancellable submission of cached answers for a single lesson."""
from __future__ import annotations

import random
from dataclasses import dataclass

from PyQt6.QtCore import QThread, pyqtSignal

from . import config, db
from .answers import build_answer
from .api import AccessDeniedError, Api, ApiError, AuthError, RateLimitError
from .models import test_details


@dataclass(frozen=True)
class Submission:
    subject_id: int
    test_id: int
    lesson_id: int
    answer: list
    spent: int


def lesson_plan(lesson_id: int) -> tuple[list[Submission], int]:
    """Snapshot cached tasks only; never guess the entity lesson id."""
    lesson = db.get_lesson(lesson_id)
    entity_id = int((lesson or {}).get("entity_lesson_id") or 0)
    if entity_id <= 0:
        raise ValueError("Неизвестен entity lessonId. Сначала синхронизируйте предмет.")
    plan, seen, skipped = [], set(), 0
    for homework in db.lesson_homeworks(lesson_id):
        for row in db.homework_tests(int(homework["id"])):
            key = (int(homework["id"]), int(row["id"]))
            if key in seen:
                continue
            seen.add(key)
            details = test_details(row)
            answer = build_answer(details) if details.supported else []
            if not answer or any(value == "" for value in answer):
                skipped += 1
                continue
            plan.append(Submission(key[0], key[1], entity_id, answer, random.randint(45, 90)))
    return plan, skipped


class LessonSender(QThread):
    item_done = pyqtSignal(int, int, str)

    def __init__(self, api: Api, plan: list[Submission], parent=None):
        super().__init__(parent)
        self.api, self.plan = api, list(plan)

    def stop(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        for item in self.plan:
            remaining = config.sync_delay()
            while remaining > 0 and not self.isInterruptionRequested():
                self.msleep(int(min(0.1, remaining) * 1000))
                remaining -= 0.1
            if self.isInterruptionRequested():
                break
            try:
                payload = self.api.post_answer(
                    item.subject_id, str(item.lesson_id), item.test_id, item.answer, item.spent
                )
                ok = isinstance(payload, dict) and (
                    payload.get("result") is True
                    or str(payload.get("result")).lower() == "true"
                    or str(payload.get("percent")) in ("100", "100.0")
                )
                message = "принято" if ok else "сервер не подтвердил правильный ответ"
            except (AuthError, AccessDeniedError, RateLimitError) as exc:
                self.item_done.emit(item.subject_id, item.test_id, str(exc))
                break
            except ApiError as exc:
                message = str(exc)
            except Exception:  # never put task answers/cookies in a traceback dialog
                message = "непредвиденная ошибка отправки"
            self.item_done.emit(item.subject_id, item.test_id, message)
