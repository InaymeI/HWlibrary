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
            title = subject_title(base, f"Предмет #{subject_id}")
            db.upsert_subject(subject_id, title, base)
            self.log.emit(f"  «{title}»")

            entries = [entry.as_row() for entry in extract_kbs_entries(base)]
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
        """Fetch one knowledge base, trying other modes if the first one fails."""
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
            tests = extract_tests(payload)
            db.upsert_kbs(kid, subject_id, mode, kbs.get("title"), kbs.get("lesson_id"))
            rows = []
            for test in tests:
                try:
                    test_id = int(test["id"])
                except (TypeError, ValueError):
                    continue
                title = test.get("title") or test.get("name") or ""
                rows.append((test_id, str(test.get("type") or ""), str(title), test))
            db.replace_kbs_tests(kid, subject_id, rows)
            if mode != preferred:
                self.log.emit(f"    режим {preferred} недоступен, использован {mode}")
            return len(rows)
        if last_error is not None:
            raise last_error
        return 0
