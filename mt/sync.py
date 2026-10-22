from PyQt6.QtCore import QThread, pyqtSignal

from . import db
from .api import Api
from .config import MODES


def extract_kbs_entries(base: dict) -> list[dict]:
    out, seen = [], set()
    tree = (base or {}).get("tree") or {}
    for mode in MODES:
        arr = tree.get(mode)
        if not isinstance(arr, list):
            continue
        for it in arr:
            if not it or it.get("id") is None:
                continue
            try:
                kid = int(it["id"])
            except (TypeError, ValueError):
                continue
            key = (kid, mode)
            if key in seen:
                continue
            seen.add(key)
            lesson = it.get("lessonId") or it.get("lesson_id") or it.get("lesson")
            try:
                lesson_id = int(lesson) if lesson else None
            except (TypeError, ValueError):
                lesson_id = None
            out.append({
                "id": kid,
                "mode": mode,
                "title": it.get("title") or it.get("name"),
                "lesson_id": lesson_id,
            })
    return out


class SyncThread(QThread):
    progress = pyqtSignal(int, int, str)  # done, total, message
    log      = pyqtSignal(str)
    done     = pyqtSignal(int)            # subject_id
    err      = pyqtSignal(str)

    def __init__(self, api: Api, subject_id: int, force: bool = False):
        super().__init__()
        self.api = api
        self.subject_id = subject_id
        self.force = force
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            self._run(self.subject_id)
            self.done.emit(self.subject_id)
        except Exception as e:
            self.err.emit(f"sync {self.subject_id}: {e}")

    def _run(self, subject_id: int):
        if self._stop:
            return
        self.log.emit(f"→ fetch subject tree #{subject_id}")
        self.progress.emit(0, 1, "subject tree")
        base = self.api.get_curriculum_subject(subject_id)
        if not base:
            raise RuntimeError("empty response")
        db.upsert_subject(subject_id, base.get("title") or f"Subject #{subject_id}", base)

        entries = extract_kbs_entries(base)
        self.log.emit(f"  kbs entries: {len(entries)}")
        total = max(len(entries), 1)

        for i, kbs in enumerate(entries):
            if self._stop:
                self.log.emit("  stopped by user")
                return
            kid = kbs["id"]
            modes = [kbs["mode"]] + [m for m in MODES if m != kbs["mode"]]
            for mode in modes:
                try:
                    tests = self.api.get_kbs_tests(subject_id, kid, mode)
                    if not isinstance(tests, list):
                        continue
                    db.upsert_kbs(kid, subject_id, mode, kbs.get("title"), kbs.get("lesson_id"))
                    for t in tests:
                        if t and t.get("id") is not None:
                            db.upsert_test(int(t["id"]), kid, subject_id, t.get("type"), t)
                    self.log.emit(f"  kbs {kid} ({mode}): {len(tests)} tests")
                    break
                except Exception as e:
                    self.log.emit(f"  kbs {kid} ({mode}) fail: {e}")
            self.progress.emit(i + 1, total, f"kbs {kid}")
            self.msleep(150)
        self.progress.emit(total, total, "done")
