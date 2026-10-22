"""Background synchronisation of subjects and their tests.

The worker is a :class:`QThread` so the UI stays responsive.  All database
writes happen in this thread (``sqlite3`` connections are per-thread) and are
batched: one transaction per knowledge base.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

from . import config, db
from .api import Api, ApiError, AuthError
from .models import extract_kbs_entries, extract_tests, subject_title

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
    subject_id: int
    kbs: int = 0
    tests: int = 0
    errors: int = 0
    cancelled: bool = False
    error: str | None = None
    seconds: float = 0.0


class SyncThread(QThread):
    """Synchronise one subject (or several, one after another)."""

    progress = pyqtSignal(int, int, str)   # done, total, message
    log = pyqtSignal(str)                  # human readable log line
    subject_done = pyqtSignal(object)      # SyncResult
    finished_all = pyqtSignal(list)        # list[SyncResult]
    failed = pyqtSignal(str, str)          # title, message

    def __init__(self, api: Api, subject_ids: list[int], parent=None) -> None:
        super().__init__(parent)
        self.api = api
        self.subject_ids = list(subject_ids)
        self._stop = False

    # ------------------------------------------------------------------ #
    def stop(self) -> None:
        self._stop = True

    @property
    def stopping(self) -> bool:
        return self._stop

    # ------------------------------------------------------------------ #
    def run(self) -> None:  # noqa: D401 - Qt entry point
        results: list[SyncResult] = []
        for index, subject_id in enumerate(self.subject_ids):
            if self._stop:
                break
            result = self._sync_subject(subject_id, index)
            results.append(result)
            self.subject_done.emit(result)
            if result.error and isinstance(result.error, str) and result.error.startswith("auth"):
                # Session expired: stop the whole queue instead of hammering.
                break
        self.finished_all.emit(results)

    # ------------------------------------------------------------------ #
    def _sync_subject(self, subject_id: int, index: int) -> SyncResult:
        started = time.monotonic()
        result = SyncResult(subject_id=subject_id)
        try:
            self.log.emit(f"→ предмет #{subject_id}")
            self.progress.emit(0, 1, f"предмет #{subject_id}")
            base = self.api.get_curriculum_subject(subject_id)
            title = subject_title(base, "")
            entries = [entry.as_row() for entry in extract_kbs_entries(base)]
            if not title and not entries:
                # «Ошибки и пустые ответы НЕ кэшировать»: keep the previous data.
                result.error = "сервер вернул пустой предмет"
                self.log.emit("  пустой ответ — кэш не изменён")
                return result
            title = title or f"Предмет #{subject_id}"
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
                self.progress.emit(position, total, f"раздел #{kid}")
                try:
                    count = self._sync_kbs(subject_id, kbs)
                    seen_ids.append(kid)
                    result.kbs += 1
                    result.tests += count
                    self.log.emit(f"  ✓ раздел #{kid}: {count} тестов")
                except AuthError:
                    raise
                except ApiError as exc:
                    result.errors += 1
                    self.log.emit(f"  ✗ раздел #{kid}: {exc}")
                except Exception as exc:  # noqa: BLE001 - never kill the whole sync
                    result.errors += 1
                    self.log.emit(f"  ✗ раздел #{kid}: {type(exc).__name__}: {exc}")
                self.progress.emit(position + 1, total, f"раздел #{kid}")
                if config.SYNC_DELAY_MS:
                    self.msleep(config.SYNC_DELAY_MS)

            if not result.cancelled and seen_ids:
                removed = db.prune_kbs(subject_id, seen_ids)
                if removed:
                    self.log.emit(f"  удалено устаревших разделов: {removed}")
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
            db.replace_kbs_tests(kid, subject_id, rows)
            return len(rows)
        if last_error is not None:
            raise last_error
        return 0
