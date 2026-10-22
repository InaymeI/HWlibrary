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


# --------------------------------------------------------------------------- #
# course structure
# --------------------------------------------------------------------------- #
def _subscribe_course(app, window) -> None:
    """Cache the course from the fake v3 API without running a thread."""
    from mt import db
    from mt.api import Api
    from mt.models import extract_course_title, extract_lessons, extract_practice_blocks
    from tests.fake_server import COURSE_ID

    api = Api()
    api.set_cookies({"sessionid": "valid"})
    payload = api.get_education_group(COURSE_ID)
    db.upsert_course(COURSE_ID, extract_course_title(payload, ""), payload)
    lessons = extract_lessons(payload)
    db.replace_course_lessons(COURSE_ID, [lesson.as_row() for lesson in lessons])
    for lesson in lessons:
        blocks = extract_practice_blocks(api.get_lesson_structure(COURSE_ID, lesson.id))
        db.set_lesson_subjects(lesson.id, [block.as_row() for block in blocks])
    window.refresh()
    app.processEvents()


def _wait_sync(app, window, timeout: float = 30.0) -> None:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        current = window._sync
        if current is None or not current.isRunning():
            break
        time.sleep(0.01)
    for _ in range(10):
        app.processEvents()


def test_course_structure_appears_in_sidebar_and_pages(app, window):
    """Course → lesson → subject → kbs, both in the tree and on the pages."""
    from PyQt6.QtCore import Qt

    from tests.fake_server import (
        COURSE_ID,
        LESSON_DB_ID,
        LESSON_ENTITY_ID,
        SUBJECT_ID,
    )

    _subscribe_course(app, window)

    tree = window.sidebar.tree
    assert tree.topLevelItemCount() == 1  # the subject is grouped under the course
    course_item = tree.topLevelItem(0)
    assert course_item.data(0, Qt.ItemDataRole.UserRole) == ("course", COURSE_ID)
    assert course_item.childCount() == 2
    lesson_item = course_item.child(0)
    assert lesson_item.data(0, Qt.ItemDataRole.UserRole) == ("lesson", LESSON_DB_ID)
    subject_item = lesson_item.child(0)
    assert subject_item.data(0, Qt.ItemDataRole.UserRole) == ("subject", SUBJECT_ID)
    assert subject_item.childCount() == 2  # two knowledge bases of the subject

    window.navigate("courses")
    assert window.stack.currentWidget() is window.courses_page
    window.navigate(f"course:{COURSE_ID}")
    assert window.stack.currentWidget() is window.course_page
    window.navigate(f"lesson:{LESSON_DB_ID}")
    assert window.stack.currentWidget() is window.lesson_page

    window.navigate("test:1001")
    app.processEvents()
    # the entity lesson id from the course wins over the tree's lessonId
    assert window.test_page.lesson_input.value() == LESSON_ENTITY_ID


def test_open_task_link_opens_cached_test(app, window):
    from mt.links import parse_link
    from tests.fake_server import COURSE_ID

    link = parse_link(
        "https://education.maximumtest.ru/lesson/4772366/subjects/777/practice/kbs/501"
        f"/tests/1002?backPage=/student/courses/{COURSE_ID}/lessons&type=online"
    )
    window.open_link(link)
    app.processEvents()
    assert window.stack.currentWidget() is window.test_page
    assert window.test_page.test_id == 1002


def test_course_link_starts_full_sync(app, window):
    from mt import db
    from mt.links import parse_link
    from tests.fake_server import COURSE_ID

    window.open_link(parse_link("https://education.maximumtest.ru/student/courses/111137/lessons"))
    assert window._sync is not None
    assert window._sync.course_ids == [COURSE_ID]
    _wait_sync(app, window)

    assert db.get_course(COURSE_ID) is not None
    assert db.course_stats(COURSE_ID)["subjects"] == 1
    window.navigate(f"course:{COURSE_ID}")
    assert window.stack.currentWidget() is window.course_page


def test_task_link_syncs_subject_and_then_opens_test(app, window):
    """Paste a task link while the cache is empty: download, then navigate."""
    from mt import db
    from mt.links import parse_link

    db.clear_cache()
    window.refresh()
    window.open_link(parse_link(
        "https://education.maximumtest.ru/lesson/4772366/subjects/777/practice/kbs/501"
        "/tests/1001?backPage=/student/courses/111137/lessons&type=online"
    ))
    assert window._pending_test == 1001
    _wait_sync(app, window)

    assert window.test_page.test_id == 1001
    assert window.stack.currentWidget() is window.test_page
    assert window._pending_test is None
