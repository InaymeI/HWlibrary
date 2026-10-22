"""Smoke test of the whole UI: every page, dialog and theme is exercised.

Runs headless (``QT_QPA_PLATFORM=offscreen``); skipped when PyQt6 is missing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture()
def window(app, tmp_path, monkeypatch):
    from mt import config, db
    from mt.models import clear_cache as clear_parse_cache
    from mt.ui import MainWindow
    from tests.fake_server import SUBJECT_ID, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", tmp_path / "cookies.json")
    monkeypatch.setattr(config, "SYNC_DELAY_MS", 0)
    db.set_database_path(tmp_path / "ui.sqlite3")
    db.init()
    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        subscribe(app, server, SUBJECT_ID)
        clear_parse_cache()  # rows are re-inserted with the same ids
        main = MainWindow()
        main.resize(1280, 820)
        main.show()
        app.processEvents()
        yield main
        main.close()
    db.close()


def subscribe(app, server, subject_id: int) -> None:
    """Fill the cache directly so the UI tests do not depend on threads."""
    from mt import db
    from mt.api import Api
    from mt.models import extract_kbs_entries, extract_tests, subject_title
    from tests.fake_server import TESTS_OFFLINE, TESTS_ONLINE

    api = Api()
    api.set_cookies({"sessionid": "valid"})
    base = api.get_curriculum_subject(subject_id)
    db.upsert_subject(subject_id, subject_title(base, "Тест"), base)
    for entry in extract_kbs_entries(base):
        rows = TESTS_ONLINE if entry.id == 501 else TESTS_OFFLINE
        db.upsert_kbs(entry.id, subject_id, entry.mode, entry.title, entry.lesson_id)
        db.replace_kbs_tests(
            entry.id,
            subject_id,
            [(t["id"], t["type"], t["title"], t) for t in extract_tests(rows)],
        )


def test_pages_and_navigation(app, window):
    window.navigate("dash")
    window.navigate("all")
    window.navigate("subject:777")
    window.navigate("kbs:501")
    window.navigate("test:1002")
    app.processEvents()

    assert window.kbs_page.table.rowCount() == 5
    window.kbs_page.search.setText("сопост")
    app.processEvents()
    assert window.kbs_page.table.rowCount() == 1
    window.kbs_page.search.setText("")
    app.processEvents()

    window.test_page.go_next()
    window.go_back()
    window.go_forward()
    app.processEvents()


def test_search_and_filters(app, window):
    window.navigate("all")
    window.all_page.search.setText("выбор")
    app.processEvents()
    assert window.all_page.table.rowCount() >= 1
    window.all_page.search.setText("")
    window.all_page.type_combo.setCurrentIndex(1)  # "с готовым ответом"
    app.processEvents()
    assert window.all_page.table.rowCount() >= 1
    window.all_page.type_combo.setCurrentIndex(0)
    app.processEvents()


def test_themes_apply(app, window):
    for theme in ("dark", "light", "system"):
        window.apply_theme(theme)
        app.processEvents()
        assert window.theme_palette().name in ("light", "dark")


def test_dialogs_construct(app, window):
    from mt.ui import dialogs

    assert dialogs.about_text()
    dialogs.JsonDialog("payload", {"a": 1}, window)
    dialogs.HelpDialog(window)
    dialogs.CookieDialog(window.api, window)
    window.navigate("test:1001")
    app.processEvents()
    dialogs.SendDialog(window.test_page.details, ["11"], 9001, 30, window)
    dialogs.ResultDialog({"isRight": True, "score": 1}, window)


def test_table_sorting_survives_refresh(app, window):
    window.navigate("kbs:501")
    window.kbs_page.table.sortItems(1, Qt.SortOrder.DescendingOrder)
    app.processEvents()
    first = window.kbs_page.table.item(0, 1).text()
    window.kbs_page.refresh()
    app.processEvents()
    assert window.kbs_page.table.item(0, 1).text() == first


def _run_sync(app, window, subject_id: int = 777):
    """Start a sync from the UI and pump the event loop until it finishes."""
    import time

    window._start_sync([subject_id])
    thread = window._sync
    deadline = time.time() + 30
    while time.time() < deadline:
        app.processEvents()
        current = window._sync
        if current is None or not current.isRunning():
            break
        time.sleep(0.01)
    # let ``finished`` → ``deleteLater`` be delivered
    for _ in range(5):
        app.processEvents()
    return thread


def test_sync_can_be_started_again_after_finishing(app, window):
    """Regression: a finished SyncThread was deleted while still referenced.

    The second start (and moving the mouse afterwards) raised
    ``RuntimeError: wrapped C/C++ object of type SyncThread has been deleted``
    and Qt aborted the process.
    """
    first = _run_sync(app, window)
    assert window._sync_thread() is None  # dangling wrapper was cleared

    # the raw reference really is dead — this is what used to blow up:
    with pytest.raises(RuntimeError):
        first.isRunning()

    _run_sync(app, window)  # would crash before the fix
    assert window._sync_thread() is None

    window.stop_sync()      # must be a no-op, not a crash
    window.close()          # closeEvent used to touch the dangling thread
    app.processEvents()


def test_closing_after_cancelled_sync_is_safe(app, window):
    window._start_sync([777])
    app.processEvents()
    window.stop_sync()
    import time

    deadline = time.time() + 30
    while time.time() < deadline and window._sync_thread() is not None:
        app.processEvents()
        time.sleep(0.01)
    window.close()
    app.processEvents()
