"""Background synchronisation of courses, subjects and their tests.

The worker is a :class:`QThread` so the UI stays responsive.  All database
writes happen in this thread (``sqlite3`` connections are per-thread) and are
batched: one transaction per knowledge base.

Two entry points share one queue:

* a **course** (``/student/courses/<id>/lessons``) expands into lessons →
  ``curriculum_practice_block`` children → subjects → knowledge bases → tests;
* a **subject** is synchronised directly, as before.

Both can be mixed in a single run; the UI shows one summary at the end.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from . import config, db
from .api import Api, ApiError, AuthError
from .models import (
    extract_course_title,
    extract_kbs_entries,
    extract_lessons,
    extract_practice_blocks,
    extract_tests,
    find_course_title,
    plural,
    subject_title,
)

#: Keys a tests payload may wrap the array in.
_ARRAY_KEYS = ("tests", "items", "data", "educationTests", "result")


def _is_tests_array(payload: Any) -> bool:
    """True when *payload* looks like a tests list (possibly wrapped).

    A non-array answer means the mode is wrong for this knowledge base, so the
    worker should try the next mode instead of caching garbage.
    """
    if isinstance(payload, list):
        return True
    if isinstance(payload, dict):
        return any(isinstance(payload.get(key), list) for key in _ARRAY_KEYS)
    return False


def _dedupe_tests(tests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one object per ``test.id`` (the reference warns about repeats)."""
    unique: dict[int, dict[str, Any]] = {}
    for test in tests:
        try:
            test_id = int(test["id"])
        except (KeyError, TypeError, ValueError):
            continue
        unique.setdefault(test_id, test)
    return list(unique.values())


@dataclass
class SyncResult:
    subject_id: int = 0
    kbs: int = 0
    tests: int = 0
    errors: int = 0
    cancelled: bool = False
    error: str | None = None
    seconds: float = 0.0
    #: Only set for a course run.
    course_id: int | None = None
    lessons: int = 0
    subjects: int = 0


class SyncThread(QThread):
    """Synchronise courses and/or subjects, one after another."""

    progress = pyqtSignal(int, int, str)   # done, total, message
    log = pyqtSignal(str)                  # human readable log line
    subject_done = pyqtSignal(object)      # SyncResult
    finished_all = pyqtSignal(list)        # list[SyncResult]
    failed = pyqtSignal(str, str)          # title, message

    def __init__(
        self,
        api: Api,
        subject_ids: list[int] | None = None,
        course_ids: list[int] | None = None,
        parent=None,
    ) -> None:
        if parent is None and isinstance(course_ids, QObject):
            # Backwards compatibility: SyncThread(api, subject_ids, parent).
            parent, course_ids = course_ids, None
        super().__init__(parent)
        self.api = api
        self.subject_ids = list(subject_ids or [])
        self.course_ids = list(course_ids or [])
        self._stop = False

    # ------------------------------------------------------------------ #
    def stop(self) -> None:
        self._stop = True

    def _pause(self) -> None:
        """Sleep a random ~1 s before the next request (human-paced parsing).

        The delay is re-drawn for every request, so a long sync never looks like
        a fixed-rate bot.  Cancellation is honoured in 100 ms slices.
        """
        remaining = config.sync_delay()
        if remaining <= 0:
            return
        while remaining > 0 and not self._stop:
            step = min(0.1, remaining)
            self.msleep(int(step * 1000))
            remaining -= step

    # ------------------------------------------------------------------ #
    def run(self) -> None:  # noqa: D401 - Qt entry point
        low, high = config.SYNC_DELAY_RANGE
        if high > 0:
            self.log.emit(f"— пауза между запросами: {low:.1f}–{high:.1f} с (случайная)")
        results: list[SyncResult] = []
        queue: list[tuple[str, int]] = [("course", cid) for cid in self.course_ids]
        queue += [("subject", sid) for sid in self.subject_ids]
        for kind, ident in queue:
            if self._stop:
                break
            if kind == "course":
                result = self._sync_course(ident)
            else:
                result = self._sync_subject(ident)
                self.subject_done.emit(result)
            results.append(result)
            if result.error and str(result.error).startswith("auth"):
                # Session expired: stop the whole queue instead of hammering.
                break
        # Persist any name that could only be read from a payload («Предмет
        # #111137» → «История России»), so the next launch shows it too.
        db.repair_course_titles()
        self.finished_all.emit(results)

    # ------------------------------------------------------------------ #
    def _sync_course(self, course_id: int) -> SyncResult:
        """Course → lessons → practice blocks → subjects → tests."""
        started = time.monotonic()
        result = SyncResult(course_id=course_id)
        try:
            self.log.emit(f"→ предмет #{course_id}")
            self.progress.emit(0, 1, f"предмет #{course_id}")
            payload, lessons = self._fetch_course(course_id)
            title = extract_course_title(payload, "")
            if not title:
                # The payload may omit the name (or send only a number):
                # keep a real cached name instead of showing «Предмет #111137».
                cached = db.get_course(course_id)
                if cached and not db.course_title_is_generic(course_id):
                    title = str(cached["title"])
                else:
                    title = f"Предмет #{course_id}"
            if not lessons:
                # «Ошибки и пустые ответы НЕ кэшировать».
                result.error = "сервер вернул предмет без уроков"
                self.log.emit("  пустой ответ — кэш предмета не изменён")
                return result
            db.upsert_course(course_id, title, payload if isinstance(payload, dict) else {})
            db.replace_course_lessons(course_id, [lesson.as_row() for lesson in lessons])
            result.lessons = len(lessons)
            self.log.emit(f"  «{title}» · уроков: {len(lessons)}")

            subject_ids: list[int] = []
            subject_titles: dict[int, str] = {}
            for position, lesson in enumerate(lessons):
                if self._stop:
                    result.cancelled = True
                    self.log.emit("  остановлено пользователем")
                    break
                self.progress.emit(position, len(lessons) + 1, f"урок «{lesson.title}»")
                try:
                    structure = self.api.get_lesson_structure(course_id, lesson.id)
                    if db.course_title_is_generic(course_id):
                        # The list endpoint may omit the name; lesson items
                        # carry `courseTitleForModule`.
                        module_title = find_course_title(structure)
                        if module_title:
                            db.rename_course(course_id, module_title)
                            title = module_title
                    blocks = extract_practice_blocks(structure)
                    db.set_lesson_subjects(lesson.id, [block.as_row() for block in blocks])
                    for block in blocks:
                        subject_titles.setdefault(block.subject_id, block.title)
                        if block.subject_id not in subject_ids:
                            subject_ids.append(block.subject_id)
                    self.log.emit(f"  ▸ {lesson.title}: домашек {len(blocks)}")
                except AuthError:
                    raise
                except ApiError as exc:
                    result.errors += 1
                    self.log.emit(f"  ✗ урок «{lesson.title}»: {exc}")
                except Exception as exc:  # noqa: BLE001 - never kill the whole sync
                    result.errors += 1
                    self.log.emit(f"  ✗ урок «{lesson.title}»: {type(exc).__name__}: {exc}")
                self._pause()

            if result.cancelled:
                return result

            total = len(lessons) + max(len(subject_ids), 1)
            for position, subject_id in enumerate(subject_ids):
                if self._stop:
                    result.cancelled = True
                    self.log.emit("  остановлено пользователем")
                    break
                self.progress.emit(
                    len(lessons) + position, total, f"домашка #{subject_id}"
                )
                sub = self._sync_subject(
                    subject_id,
                    report_progress=False,
                    title_hint=subject_titles.get(subject_id, ""),
                )
                result.subjects += 1
                result.kbs += sub.kbs
                result.tests += sub.tests
                result.errors += sub.errors
                self.subject_done.emit(sub)
                if sub.error:
                    result.errors += 1
                    if str(sub.error).startswith("auth"):
                        result.error = sub.error
                        break
                self._pause()
            self.progress.emit(total, total, "готово")
        except AuthError as exc:
            result.error = f"auth: {exc}"
            self.failed.emit(exc.title, str(exc))
        except ApiError as exc:
            result.error = str(exc)
            self.failed.emit(exc.title, str(exc))
        except Exception as exc:  # noqa: BLE001
            result.error = f"{type(exc).__name__}: {exc}"
            self.failed.emit("Ошибка синхронизации", result.error)
        finally:
            result.seconds = time.monotonic() - started
            db.close()
        return result

    # ------------------------------------------------------------------ #
    def _fetch_course(self, course_id: int) -> tuple[Any, list]:
        """All lessons of a course, following ``lastKnownId`` pagination.

        The backend ignores ``page``; the cursor is the ``id`` of the last
        lesson already seen.  A page that brings nothing new ends the course.
        """
        page_size = config.COURSE_PAGE_SIZE
        first = self.api.get_education_group(course_id, per_page=page_size)
        batch = extract_lessons(first)
        lessons = list(batch)
        requests = 1
        while batch and not self._stop:
            if requests >= config.COURSE_MAX_PAGES:
                self.log.emit(f"  остановлено после {requests} страниц (лимит)")
                break
            self._pause()
            payload = self.api.get_education_group(
                course_id, last_known_id=batch[-1].id, per_page=page_size
            )
            requests += 1
            known = {lesson.id for lesson in lessons}
            batch = [lesson for lesson in extract_lessons(payload) if lesson.id not in known]
            lessons.extend(batch)
        return first, lessons

    # ------------------------------------------------------------------ #
    def _sync_subject(
        self, subject_id: int, *, report_progress: bool = True, title_hint: str = ""
    ) -> SyncResult:
        started = time.monotonic()
        result = SyncResult(subject_id=subject_id)
        try:
            self.log.emit(f"→ домашка #{subject_id}")
            if report_progress:
                self.progress.emit(0, 1, f"домашка #{subject_id}")
            base = self.api.get_curriculum_subject(subject_id)
            title = subject_title(base, "")
            course_id = db.course_id_for_subject(subject_id)
            if course_id is not None and db.course_title_is_generic(course_id):
                module_title = find_course_title(base)
                if module_title:
                    db.rename_course(course_id, module_title)
                    self.log.emit(f"  предмет назван по courseTitleForModule: {module_title}")
            entries = [entry.as_row() for entry in extract_kbs_entries(base)]
            if not title and not entries:
                # «Ошибки и пустые ответы НЕ кэшировать»: keep the previous data.
                result.error = "сервер вернул пустую домашку"
                self.log.emit("  пустой ответ — кэш не изменён")
                return result
            if db.title_is_generic(title):
                # Keep a real name: from the lesson structure, the cache or the
                # «Домашка #id» placeholder — in that order.
                cached = db.get_subject(subject_id) or {}
                hint = title_hint or str(cached.get("title") or "")
                title = hint if not db.title_is_generic(hint) else f"Домашка #{subject_id}"
            db.upsert_subject(subject_id, title, base)
            self.log.emit(f"  «{title}»")
            self.log.emit(f"  разделов в дереве: {len(entries)}")
            total = max(len(entries), 1)
            seen_ids: list[int] = []

            for position, kbs in enumerate(entries):
                if self._stop:
                    result.cancelled = True
                    self.log.emit("  остановлено пользователем")
                    break
                kid = int(kbs["id"])
                if report_progress:
                    self.progress.emit(position, total, f"раздел #{kid}")
                try:
                    count = self._sync_kbs(subject_id, kbs)
                    seen_ids.append(kid)
                    result.kbs += 1
                    result.tests += count
                    self.log.emit(f"  ✓ раздел #{kid}: {count} {plural(count, 'тест', 'теста', 'тестов')}")
                except AuthError:
                    raise
                except ApiError as exc:
                    result.errors += 1
                    self.log.emit(f"  ✗ раздел #{kid}: {exc}")
                except Exception as exc:  # noqa: BLE001 - never kill the whole sync
                    result.errors += 1
                    self.log.emit(f"  ✗ раздел #{kid}: {type(exc).__name__}: {exc}")
                if report_progress:
                    self.progress.emit(position + 1, total, f"раздел #{kid}")
                self._pause()

            # Prune only when every section answered: a transient error must not
            # delete a cached homework just because it was not fetched this time.
            prunable = not result.cancelled and not result.errors and seen_ids
            if prunable:
                removed = db.prune_kbs(subject_id, seen_ids)
                if removed:
                    self.log.emit(f"  удалено устаревших разделов: {removed}")
            if report_progress:
                self.progress.emit(total, total, "готово")
        except AuthError as exc:
            result.error = f"auth: {exc}"
            self.failed.emit(exc.title, str(exc))
        except ApiError as exc:
            result.error = str(exc)
            self.failed.emit(exc.title, str(exc))
        except Exception as exc:  # noqa: BLE001
            result.error = f"{type(exc).__name__}: {exc}"
            self.failed.emit("Ошибка синхронизации", result.error)
        finally:
            result.seconds = time.monotonic() - started
            db.close()
        return result

    # ------------------------------------------------------------------ #
    def _sync_kbs(self, subject_id: int, kbs: dict[str, Any]) -> int:
        """Fetch one knowledge base, trying other modes if the first one fails.

        Mode order: the mode the KBS was found in, then online, offline,
        aftertheory — the first response that is a tests array wins.
        Empty arrays are not cached (the previous cache is preserved).
        """
        kid = int(kbs["id"])
        preferred = kbs.get("mode") or "online"
        modes = [preferred] + [mode for mode in config.MODES if mode != preferred]
        last_error: Exception | None = None
        for mode in modes:
            if self._stop:
                raise RuntimeError("остановлено")
            try:
                payload = self.api.get_kbs_tests(subject_id, kid, mode)
            except AuthError:
                raise
            except ApiError as exc:
                last_error = exc
                continue
            if not _is_tests_array(payload):
                last_error = ApiError(f"режим {mode}: неожиданный формат ответа")
                self.log.emit(f"    режим {mode}: не массив тестов, пробую другой")
                continue

            db.upsert_kbs(kid, subject_id, mode, kbs.get("title"), kbs.get("lesson_id"))
            tests = _dedupe_tests(extract_tests(payload))
            rows = []
            for test in tests:
                title = test.get("title") or test.get("name") or ""
                rows.append((int(test["id"]), str(test.get("type") or ""), str(title), test))
            if mode != preferred:
                self.log.emit(f"    режим {preferred} недоступен, использован {mode}")
            if not rows:
                # "Ошибки и пустые ответы НЕ кэшировать": keep what we already have.
                cached = len(db.list_tests(kid))
                if cached:
                    self.log.emit(
                        f"    пустой ответ ({mode}) — сохранён прежний кэш: {cached} тестов"
                    )
                else:
                    self.log.emit(f"    пустой ответ ({mode}) — нечего кэшировать")
                return 0
            # Merge, never replace: a shorter response must not delete tests
            # that are already cached (the library is meant to keep everything).
            total_cached = db.merge_kbs_tests(kid, subject_id, rows)
            if total_cached > len(rows):
                self.log.emit(
                    f"    в базе осталось прежних заданий: {total_cached - len(rows)}"
                )
            return total_cached
        if last_error is not None:
            raise last_error
        return 0
