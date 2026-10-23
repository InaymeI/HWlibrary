"""Unit tests for parsing, answers, database and the sync pipeline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt import answers, config, db, models  # noqa: E402


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def test_extract_kbs_entries_handles_tree_and_duplicates():
    payload = {
        "tree": {
            "online": [
                {"id": 1, "title": "A", "lessonId": 10},
                {"id": 1, "title": "A duplicate", "lessonId": 10},
                {"id": 2, "title": "B", "lesson": {"id": 20}},
            ],
            "offline": [{"id": 3, "name": "C"}],
        }
    }
    entries = models.extract_kbs_entries(payload)
    assert [entry.id for entry in entries] == [1, 2, 3]
    assert entries[0].lesson_id == 10
    assert entries[1].lesson_id == 20
    assert entries[2].mode == "offline"


def test_extract_kbs_entries_accepts_nested_and_flat_shapes():
    flat = {"tree": [{"id": 5, "knowledgeBases": [{"id": 6, "title": "nested"}]}]}
    entries = models.extract_kbs_entries(flat)
    assert {entry.id for entry in entries} == {5, 6}

    wrapped = {"data": {"tree": {"online": [{"id": 7}]}}}
    assert [entry.id for entry in models.extract_kbs_entries(wrapped)] == [7]

    assert models.extract_kbs_entries(None) == []
    assert models.extract_kbs_entries({"tree": {"online": "garbage"}}) == []


def test_extract_tests_accepts_list_and_wrapped_objects():
    assert [t["id"] for t in models.extract_tests([{"id": 1}, {"nope": 1}])] == [1]
    assert [t["id"] for t in models.extract_tests({"tests": [{"id": 2}]})] == [2]
    assert [t["id"] for t in models.extract_tests({"data": {"items": [{"id": 3}]}})] == [3]
    assert models.extract_tests(None) == []


def test_strip_html_and_normalize_html():
    assert models.strip_html("<p>Привет&nbsp;<b>мир</b></p>") == "Привет мир"
    assert models.normalize_html("просто текст") == "<p>просто текст</p>"
    assert models.normalize_html("<p>уже html</p>") == "<p>уже html</p>"


def test_parse_options_marks_right_answers_and_reads_variants():
    payload = {
        "type": "type2",
        "educationTestAnswers": [
            {"id": 1, "variants": ["a", "b"], "isRightAnswer": True, "sort": 2},
            {"id": 2, "variants": ["c"], "isRightAnswer": False, "sort": 1},
        ],
    }
    options = models.parse_options(payload)
    assert [option.id for option in options] == ["1", "2"]
    assert options[0].variants == ("a", "b")
    assert options[0].is_right is True


# --------------------------------------------------------------------------- #
# answers
# --------------------------------------------------------------------------- #
def _details(ttype: str, answers: list[dict]) -> models.TestDetails:
    return models.test_details({
        "id": 1,
        "subject_id": 1,
        "kbs_id": 1,
        "type": ttype,
        "fetched_at": 0,
        "payload_json": json.dumps({"type": ttype, "educationTestAnswers": answers}),
    })


def test_answer_for_choice_returns_ids():
    details = _details("type2", [
        {"id": 5, "variants": ["x"], "isRightAnswer": True},
        {"id": 6, "variants": ["y"], "isRightAnswer": False},
        {"id": 7, "variants": ["z"], "isRightAnswer": True},
    ])
    assert answers.build_answer(details) == ["5", "7"]


def test_answer_for_text_types_returns_values():
    details = _details("type3", [{"id": 1, "variants": ["42"], "isRightAnswer": True}])
    assert answers.build_answer(details) == ["42"]


def test_type5_picks_one_spelling_per_field():
    """type5 is a text input: one text per right answer, variants = spellings."""
    details = _details("type5", [
        {"id": 1, "variants": ["Москва", "москва"], "isRightAnswer": True, "sort": 0},
        {"id": 2, "variants": ["Россия"], "isRightAnswer": True, "sort": 1},
        {"id": 3, "variants": [], "isRightAnswer": True, "sort": 2},
    ])
    import random

    seen_first_field = set()
    for seed in range(40):
        result = answers.build_answer(details, rng=random.Random(seed))
        assert len(result) == 3
        assert result[0] in {"Москва", "москва"}
        assert result[1] == "Россия"
        assert result[2] == ""  # no variants -> empty string
        seen_first_field.add(result[0])
    # each field is chosen independently, not forced into one shared column
    assert seen_first_field == {"Москва", "москва"}


def test_type3_and_type4_take_first_variant_in_sort_order():
    details = _details("type4", [
        {"id": 1, "variants": ["Б"], "isRightAnswer": True, "sort": 1},
        {"id": 2, "variants": ["А"], "isRightAnswer": True, "sort": 0},
    ])
    assert answers.build_answer(details) == ["А", "Б"]


def test_answer_without_right_answers_is_empty():
    details = _details("type1", [{"id": 1, "variants": ["x"], "isRightAnswer": False}])
    assert answers.build_answer(details) == []


# --------------------------------------------------------------------------- #
# database
# --------------------------------------------------------------------------- #
@pytest.fixture()
def temp_db(tmp_path):
    db.set_database_path(tmp_path / "test.sqlite3")
    db.init()
    yield
    db.close()
    db.set_database_path(None)


def test_db_upsert_and_stats(temp_db):
    db.upsert_subject(1, "Математика", {"title": "Математика"})
    db.upsert_kbs(10, 1, "online", "Раздел", 55)
    db.replace_kbs_tests(10, 1, [
        (100, "type1", "Первый", {"id": 100}),
        (101, "type9", "Второй", {"id": 101}),
    ])
    stats = db.subject_stats(1)
    assert stats == {"total": 2, "supported": 1, "kbs": 1}

    # Re-sync must remove tests that disappeared on the server.
    db.replace_kbs_tests(10, 1, [(100, "type1", "Первый", {"id": 100})])
    assert [row["id"] for row in db.list_tests(10)] == [100]


def test_db_query_filters_and_search(temp_db):
    db.upsert_subject(1, "Математика", {})
    db.upsert_subject(2, "Русский язык", {})
    db.upsert_kbs(10, 1, "online", "Алгебра", 5)
    db.upsert_kbs(20, 2, "online", "Орфография", 6)
    db.upsert_tests([
        (1, 10, 1, "type1", "Квадратное уравнение", {}),
        (2, 10, 1, "type9", "Сложное задание", {}),
        (3, 20, 2, "type2", "Правописание НЕ", {}),
    ])
    assert len(db.query_tests()) == 3
    assert [row["id"] for row in db.query_tests(search="уравнение")] == [1]
    assert [row["id"] for row in db.query_tests(search="правоп")] == [3]
    assert [row["id"] for row in db.query_tests(type_filter="supported")] == [1, 3]
    assert [row["id"] for row in db.query_tests(type_filter="other")] == [2]
    assert [row["id"] for row in db.query_tests(subject_id=2)] == [3]
    assert [row["id"] for row in db.query_tests(kbs_id=10)] == [1, 2]
    # "не" is a substring of "уравнение" — matching is deliberately literal
    assert db.query_tests_count(search="правописание не") == 1
    assert db.query_tests_count(search="ПРАВОПИСАНИЕ") == 1
    # LIKE wildcards must be escaped, not treated as patterns
    assert db.query_tests_count(search="%") == 0
    # the names the user sees in the tree are searchable too
    assert db.query_tests_count(search="русский") == 1
    assert db.query_tests_count(search="математика сложное") == 1


def test_search_finds_homework_section_course_and_lesson_ids(temp_db):
    """A number typed into the search must find its homework, not nothing."""
    db.upsert_course(111137, "История России", {})
    db.replace_course_lessons(111137, [
        {"id": 41907128, "entity_id": 4772366, "title": "Занятие 9", "position": 1},
    ])
    db.set_lesson_subjects(41907128, [
        {"subject_id": 777, "title": "Домашка №1", "position": 1},
    ])
    db.upsert_subject(777, "Домашка №1", {})
    db.upsert_kbs(501, 777, "online", "Раздел 1", 41907128)
    db.upsert_tests([
        (1001, 501, 777, "type1", "Одиночный выбор", {}),
        (1002, 501, 777, "type2", "Множественный выбор", {}),
    ])

    # homework id, section id, course id, entity lesson id, lesson title
    assert db.query_tests_count(search="777") == 2
    assert db.query_tests_count(search="501") == 2
    assert db.query_tests_count(search="111137") == 2
    assert db.query_tests_count(search="4772366") == 2
    assert db.query_tests_count(search="занятие 9") == 2
    # test id and homework name keep working
    assert db.query_tests_count(search="1002") == 1
    assert db.query_tests_count(search="домашка №1") == 2
    # a number that matches nothing stays empty
    assert db.query_tests_count(search="999999") == 0


def test_manual_titles_survive_sync_and_repair(temp_db):
    """A name typed by hand must not be replaced by the automatic one."""
    db.upsert_course(111102, "Предмет #111102", {})
    db.upsert_subject(51610, "Домашка #51610", {})
    db.rename_course(111102, "Мой предмет")
    db.rename_subject(51610, "Моя домашка")

    # a sync arrives with the server's own names
    db.upsert_course(111102, "История России", {"courseTitleForModule": "История России"})
    db.upsert_subject(51610, "Домашняя работа", {"title": "Домашняя работа"})
    assert db.get_course(111102)["title"] == "Мой предмет"
    assert db.get_subject(51610)["title"] == "Моя домашка"

    # and the one-off repair of id-like names leaves manual rows alone
    db.rename_course(111102, "111102")
    db.rename_subject(51610, "51610")
    db.repair_course_titles()
    assert db.get_course(111102)["title"] == "111102"
    assert db.get_subject(51610)["title"] == "51610"


def test_set_course_title_only_fills_a_placeholder(temp_db):
    db.upsert_course(1, "Предмет #1", {})
    db.set_course_title(1, "История России")
    assert db.get_course(1)["title"] == "История России"
    db.set_course_title(1, "Другое имя")
    assert db.get_course(1)["title"] == "История России"


def test_db_migration_from_v1_schema(tmp_path):
    """An old database without the title/lesson columns must keep working."""
    import sqlite3

    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE subjects (id INTEGER PRIMARY KEY, payload_json TEXT, fetched_at INTEGER NOT NULL);
        CREATE TABLE knowledge_bases (id INTEGER PRIMARY KEY, subject_id INTEGER NOT NULL,
                                      mode TEXT NOT NULL, fetched_at INTEGER NOT NULL);
        CREATE TABLE tests (id INTEGER PRIMARY KEY, kbs_id INTEGER NOT NULL,
                            subject_id INTEGER NOT NULL, type TEXT,
                            payload_json TEXT NOT NULL, fetched_at INTEGER NOT NULL);
        INSERT INTO subjects VALUES (1, '{"title": "Старый"}', 1);
        INSERT INTO tests VALUES (9, 1, 1, 'type1',
                                  '{"title": "Восстановленный заголовок"}', 1);
        """
    )
    conn.commit()
    conn.close()

    db.set_database_path(path)
    try:
        db.init()
        assert db.list_subjects()[0]["title"] == "Старый"
        assert db.list_tests(1)[0]["title"] == "Восстановленный заголовок"
        # v5: the manual-title flag is added and defaults to «auto»
        columns = {
            row["name"]: row
            for row in db.get_connection().execute("PRAGMA table_info(subjects)")
        }
        assert "manual_title" in columns
        assert db.get_subject(1)["manual_title"] == 0
        course_columns = {
            row["name"]: row
            for row in db.get_connection().execute("PRAGMA table_info(courses)")
        }
        assert "manual_title" in course_columns
    finally:
        db.close()
        db.set_database_path(None)


def test_db_delete_and_clear(temp_db):
    db.upsert_subject(1, "A", {})
    db.upsert_kbs(10, 1, "online", "K", None)
    db.upsert_test(1, 10, 1, "type1", {"title": "t"})
    db.delete_kbs(10)
    assert db.list_kbs(1) == []
    assert db.all_tests() == []
    db.delete_subject(1)
    assert db.list_subjects() == []

    db.upsert_subject(2, "B", {})
    db.clear_cache()
    assert db.list_subjects() == []


def test_tree_data_groups_counts(temp_db):
    db.upsert_subject(1, "A", {})
    db.upsert_kbs(10, 1, "online", "K1", None)
    db.upsert_kbs(11, 1, "offline", "K2", None)
    db.upsert_tests([
        (1, 10, 1, "type1", "one", {}),
        (2, 10, 1, "type9", "two", {}),
        (3, 11, 1, "type2", "three", {}),
    ])
    tree = db.tree_data()
    assert len(tree) == 1
    subject, entries = tree[0]
    assert subject["title"] == "A"
    assert {kbs["id"]: counts for kbs, counts in entries} == {
        10: {"total": 2, "supported": 1},
        11: {"total": 1, "supported": 1},
    }


# --------------------------------------------------------------------------- #
# sync against the fake server
# --------------------------------------------------------------------------- #
def test_sync_pipeline_end_to_end(temp_db, monkeypatch):
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import (
        KBS_EXTRA,
        KBS_ONLINE,
        SUBJECT_ID,
        FakeServer,
    )

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-test.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})

        thread = SyncThread(api, [SUBJECT_ID])
        results: list = []
        # Direct connection: signals are delivered without a Qt event loop.
        thread.finished_all.connect(results.extend, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)

        assert results, "sync thread did not finish"
        result = results[0]
        assert result.error is None
        assert result.kbs == 2
        assert result.tests == 6
        assert result.errors == 0

        subject = db.get_subject(SUBJECT_ID)
        assert subject["title"] == "Домашка №1"
        assert {row["id"] for row in db.list_kbs(SUBJECT_ID)} == {KBS_ONLINE, KBS_EXTRA}
        assert db.kbs_stats(KBS_ONLINE)["total"] == 5
        assert db.kbs_stats(KBS_EXTRA)["total"] == 1
        # the fallback mode probe must not leave duplicate tests behind
        assert len(db.list_tests(KBS_ONLINE)) == 5
        # a homework (subject) collects the tests of all its sections
        assert len(db.homework_tests(SUBJECT_ID)) == 6
        assert db.homework_row(SUBJECT_ID)["sections"] == 2
        assert db.homework_row(SUBJECT_ID)["total"] == 6

        # answering uses the endpoint from the API docs
        response = api.post_answer(SUBJECT_ID, 9001, 1001, ["11"], 42)
        assert response == {"result": "true", "percent": 100}
        post = server.posts[-1]
        assert post["path"].endswith(f"/curriculumsubject/{SUBJECT_ID}/tests/1001")
        assert post["payload"] == {
            "lessonId": "9001",
            "attaches": [],
            "answer": ["11"],
            "spentTime": 42,
        }


def test_sync_without_cookies_reports_auth_error(temp_db, monkeypatch):
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import SUBJECT_ID, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-test2.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.clear_cookies()
        thread = SyncThread(api, [SUBJECT_ID])
        results: list = []
        thread.finished_all.connect(results.extend, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)
        assert results[0].error and "auth" in results[0].error


def test_cookie_string_parsing(monkeypatch, tmp_path):
    from mt.api import Api

    monkeypatch.setattr(config, "COOKIES_PATH", tmp_path / "cookies.json")
    api = Api()
    count = api.set_cookies_from_string("Cookie: sessionid=abc; csrftoken=xyz")
    assert count == 2
    assert "sessionid=abc" in api.cookie_header
    assert config.COOKIES_PATH.exists()
    # stored file must not be world readable
    assert (config.COOKIES_PATH.stat().st_mode & 0o077) == 0

    reloaded = Api()
    assert reloaded.has_cookies()
    assert reloaded.cookie_header == api.cookie_header

    api.clear_cookies()
    assert not api.has_cookies()


# --------------------------------------------------------------------------- #
# api layer
# --------------------------------------------------------------------------- #
def test_api_reports_auth_and_network_errors(monkeypatch):
    from mt import api as api_module
    from mt.api import Api, AuthError, NetworkError

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-nope.json")
    api = Api()
    api.clear_cookies()
    from tests.fake_server import SUBJECT_ID, FakeServer

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        with pytest.raises(AuthError):
            api.get_curriculum_subject(SUBJECT_ID)

        api.set_cookies({"sessionid": "valid"})
        payload = api.get_curriculum_subject(SUBJECT_ID)
        assert payload["title"] == "Домашка №1"

    # an unreachable host surfaces as a friendly NetworkError
    monkeypatch.setattr(config, "BASE_URL", "http://127.0.0.1:9")
    with pytest.raises(NetworkError):
        api.get_curriculum_subject(SUBJECT_ID)
    assert api_module.NetworkError.title == "Нет соединения"


def test_api_rejects_non_json_login_page(monkeypatch):
    from mt.api import Api, AuthError

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-nope2.json")
    api = Api()

    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}
        text = "<html>login</html>"
        reason = "OK"

    class FakeSession:
        def request(self, *args, **kwargs):
            return FakeResponse()

    api._session = FakeSession()  # type: ignore[assignment]
    with pytest.raises(AuthError):
        api.get_json("/api/whatever")


def test_sync_result_reports_failed_subject(temp_db, monkeypatch):
    """A broken endpoint must not wipe previously cached tests."""
    from mt.api import Api
    from mt.sync import SyncThread

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-test3.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    db.upsert_subject(777, "Старое имя", {"title": "Старое имя"})
    db.upsert_kbs(501, 777, "online", "Раздел", 1)
    db.replace_kbs_tests(501, 777, [(1, "type1", "Старый тест", {"id": 1})])

    from tests.fake_server import FakeServer

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.clear_cookies()
        thread = SyncThread(api, [777])
        results: list = []
        thread.finished_all.connect(results.extend, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)

    # auth failure: cache untouched
    assert [row["title"] for row in db.list_tests(501)] == ["Старый тест"]
    assert results[0].error is not None


# --------------------------------------------------------------------------- #
# API reference conformance
# --------------------------------------------------------------------------- #
def test_empty_tests_response_does_not_wipe_cache(temp_db, monkeypatch):
    """«Ошибки и пустые ответы НЕ кэшировать» — keep the previous cache."""
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import KBS_ONLINE, SUBJECT_ID, TESTS_ONLINE, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-empty.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})

        # first run fills the cache
        thread = SyncThread(api, [SUBJECT_ID])
        thread.finished_all.connect(lambda _r: None, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)

        before = db.kbs_stats(KBS_ONLINE)["total"]
        assert before == len(TESTS_ONLINE)

        # the backend now answers with empty arrays
        server.empty_tests = True
        thread = SyncThread(api, [SUBJECT_ID])
        results: list = []
        thread.finished_all.connect(results.extend, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)

        assert db.kbs_stats(KBS_ONLINE)["total"] == before
        assert results[0].error is None


def test_non_array_tests_payload_falls_back_to_another_mode(temp_db, monkeypatch):
    """A dict answer is not a tests array → the next mode is tried."""
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import KBS_OFFLINE, SUBJECT2_ID, TESTS_OFFLINE, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-mode.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))
    monkeypatch.setattr(config, "MODES", ("online", "offline", "aftertheory"))

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})
        thread = SyncThread(api, [SUBJECT2_ID])
        thread.finished_all.connect(lambda _r: None, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)

    # the KBS found in the "online" list only answers in "offline" mode
    assert db.get_kbs(KBS_OFFLINE)["mode"] == "offline"
    assert db.kbs_stats(KBS_OFFLINE)["total"] == len(TESTS_OFFLINE)


def test_post_answer_retries_transient_failures(monkeypatch):
    from mt.api import Api
    from tests.fake_server import SUBJECT_ID, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-post.json")
    monkeypatch.setattr(config, "POST_RETRY_DELAY", 0.01)

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})
        server.fail_posts = 2  # two 500s, then success

        response = api.post_answer(SUBJECT_ID, 111213, 1001, ["11"], 67)
        assert response == {"result": "true", "percent": 100}
        assert server.post_attempts == 3  # 3 attempts as documented
        assert server.posts[-1]["payload"] == {
            "lessonId": "111213",
            "attaches": [],
            "answer": ["11"],
            "spentTime": 67,
        }


def test_post_answer_does_not_retry_client_errors(monkeypatch):
    from mt.api import Api, ApiError
    from tests.fake_server import SUBJECT_ID, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-post2.json")
    monkeypatch.setattr(config, "POST_RETRY_DELAY", 0.01)

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})
        server.post_status = 400

        with pytest.raises(ApiError):
            api.post_answer(SUBJECT_ID, 111213, 1001, ["11"], 60)
        assert server.post_attempts == 1


def test_answer_response_verdict_matches_documented_shape():
    from mt.ui.dialogs import parse_result

    assert parse_result({"result": "true", "percent": 100})[0] is True
    assert parse_result({"result": "false", "percent": 0})[0] is False
    assert parse_result({"result": True})[0] is True
    assert parse_result({"percent": 100})[0] is True
    assert parse_result({"percent": 40})[0] is False
    assert parse_result({}) == (None, None)
    assert parse_result("garbage") == (None, None)


def test_subject_url_parsing_supports_practice_lesson_url():
    from mt.ui.main_window import _parse_subject_url

    assert _parse_subject_url("12345") == (12345, None)
    assert _parse_subject_url("https://education.maximumtest.ru/subjects/777/") == (777, None)
    assert _parse_subject_url(
        "https://education.maximumtest.ru/lesson/111213/subjects/999/practice/kbs/67890/"
    ) == (999, 111213)


def test_nested_right_answers_from_single_test_endpoint(temp_db):
    """GET /tests/{id} may wrap answers in data.studentTestResult.educationTest."""
    payload = {
        "data": {
            "studentTestResult": {
                "educationTest": {
                    "id": 42,
                    "type": "type2",
                    "title": "Обёрнутый тест",
                    "content": "<p>Условие</p>",
                    "lessonId": 111213,
                    "educationTestRightAnswers": [
                        {"id": 1, "isRightAnswer": True, "variants": ["a"], "sort": 0},
                        {"id": 2, "isRightAnswer": False, "variants": ["b"], "sort": 1},
                        {"id": 3, "isRightAnswer": True, "variants": ["c"], "sort": 2},
                    ],
                }
            }
        }
    }
    row = {
        "id": 42,
        "subject_id": 1,
        "kbs_id": 1,
        "type": "",
        "fetched_at": 0,
        "payload_json": json.dumps(payload),
    }
    details = models.test_details(row)
    assert details.type == "type2"
    assert details.title == "Обёрнутый тест"
    assert details.question_text == "Условие"
    assert details.lesson_id == 111213
    assert answers.build_answer(details) == ["1", "3"]


def test_empty_subject_payload_does_not_overwrite_cache(temp_db, monkeypatch):
    """A 200 with an empty body must not clobber a good cached subject."""
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import SUBJECT_ID, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-empty-subj.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    db.upsert_subject(SUBJECT_ID, "Нормальное имя", {"title": "Нормальное имя"})

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})

        # the backend answers with an object that has no title and no tree
        monkeypatch.setattr(api, "get_curriculum_subject", lambda _sid: {})

        thread = SyncThread(api, [SUBJECT_ID])
        results: list = []
        thread.finished_all.connect(results.extend, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)

    assert db.get_subject(SUBJECT_ID)["title"] == "Нормальное имя"
    assert results[0].error


# --------------------------------------------------------------------------- #
# course structure (v3 API)
# --------------------------------------------------------------------------- #
REFERENCE_LINK = (
    "https://education.maximumtest.ru/lesson/4772366/subjects/51610/practice"
    "/kbs/8931/tests/289165?backPage=/student/courses/111137/lessons&type=online"
)


def test_parse_reference_task_link():
    from mt import links

    parsed = links.parse_link(REFERENCE_LINK)
    assert parsed is not None
    assert parsed.kind == "test"
    assert parsed.course_id == 111137
    assert parsed.lesson_id == 4772366
    assert parsed.subject_id == 51610
    assert parsed.kbs_id == 8931
    assert parsed.test_id == 289165
    assert parsed.mode == "online"


def test_parse_course_and_subject_links():
    from mt import links

    course = links.parse_link("https://education.maximumtest.ru/student/courses/111137/lessons")
    assert course is not None
    assert (course.kind, course.course_id) == ("course", 111137)

    quoted = links.parse_link(
        "https://education.maximumtest.ru/lesson/4772366/subjects/51610"
        "?backPage=%2Fstudent%2Fcourses%2F111137%2Flessons"
    )
    assert quoted is not None
    assert (quoted.kind, quoted.subject_id, quoted.lesson_id, quoted.course_id) == (
        "subject", 51610, 4772366, 111137,
    )

    kbs = links.parse_link("https://education.maximumtest.ru/kbs/8931")
    assert kbs is not None and kbs.kind == "kbs" and kbs.kbs_id == 8931
    assert links.parse_link("") is None


def test_extract_lessons_and_practice_blocks_from_v3_shapes():
    lessons = models.extract_lessons({
        "data": [
            {"id": 41907128, "position": 3, "title": "Урок 1",
             "entityInfo": {"id": 4772366, "title": "Практика"}},
            {"id": 41907128, "entityInfo": {"id": 4772366}},  # duplicate
            {"entityInfo": {"id": 4772367, "title": "Без id"}},
            {"title": "мусор"},
        ],
    })
    assert [lesson.id for lesson in lessons] == [41907128, 4772367]
    assert lessons[0].entity_id == 4772366
    assert lessons[0].position == 3
    assert lessons[1].title == "Без id"

    blocks = models.extract_practice_blocks({
        "data": {
            "children": [
                {"type": "curriculum_theory_block", "entityInfo": {"id": 1}},
                {"type": "curriculum_practice_block",
                 "entityInfo": {"id": 51610, "title": "Практика"}},
                {"type": "curriculum_practice_block", "entityInfo": {"id": 51610}},
            ]
        }
    })
    assert [block.subject_id for block in blocks] == [51610]
    assert blocks[0].title == "Практика"
    assert models.extract_practice_blocks(None) == []


def test_titles_are_names_never_ids():
    """The backend sends numeric titles next to real names — pick the name."""
    payload = {
        "id": 111137,
        "title": "111137",
        "data": [
            {
                "id": 41907128,
                "title": "4772366",
                "entityInfo": {"id": 4772366, "title": "Урок 1. Введение"},
            }
        ],
    }
    assert models.looks_like_id("111137")
    assert models.looks_like_id(" # 42 ")
    assert models.looks_like_id("")
    assert not models.looks_like_id("История России")

    lessons = models.extract_lessons(payload)
    assert [lesson.title for lesson in lessons] == ["Урок 1. Введение"]

    # a lesson name must not become the name of the whole предмет
    assert models.extract_course_title(payload, "Предмет #111137") == "Предмет #111137"
    assert models.extract_course_title(
        {"title": "История России", "data": payload["data"]}, ""
    ) == "История России"

    # practice blocks (домaшки) also come with real names
    structure = {"data": {"children": [
        {"type": "curriculum_practice_block",
         "entityInfo": {"id": 777, "title": "888"}},
    ]}}
    blocks = models.extract_practice_blocks(structure)
    assert [block.title for block in blocks] == ["Домашка #777"]


def test_practice_blocks_survive_any_nesting():
    """Every homework of a lesson is found, whatever wrapper the backend uses."""
    structure = {
        "data": {
            "id": 1,
            "children": [
                {"type": "curriculum_theory_block", "entityInfo": {"id": 11, "title": "Теория"}},
                {"type": "curriculum_practice_block",
                 "entityInfo": {"id": 777, "title": "Домашка №1"}},
                # same block repeated deeper: must not be duplicated
                {"type": "curriculum_group", "children": [
                    {"type": "curriculum_practice_block",
                     "entityInfo": {"id": 777, "title": "Домашка №1"}},
                ]},
                # a wrapper with a key nobody expects
                {"type": "curriculum_module", "content": [
                    {"type": "lesson_practice",
                     "entityInfo": {"id": 888, "title": "Домашка №2"}},
                ]},
                # a block that only names its subject
                {"type": "curriculum_task_block", "subjectId": 999, "title": "Домашка №3"},
                # an entity typed as subject, no block type at all
                {"entityInfo": {"id": 1000, "type": "subject", "title": "Домашка №4"}},
                {"type": "curriculum_test_block", "entityInfo": {"id": 12, "title": "Квиз"}},
            ],
        }
    }
    blocks = models.extract_practice_blocks(structure)
    assert [block.subject_id for block in blocks] == [777, 888, 999, 1000]
    assert [block.title for block in blocks] == [
        "Домашка №1", "Домашка №2", "Домашка №3", "Домашка №4"
    ]


def test_course_title_comes_from_course_title_for_module():
    """`courseTitleForModule` wins over a numeric top-level title."""
    payload = {
        "id": 111137,
        "title": "111137",
        "data": [
            {"id": 41907128, "title": "Урок 1", "courseTitleForModule": "История России"},
            {"id": 41907129, "title": "Урок 2", "courseTitleForModule": "История России"},
        ],
    }
    assert models.find_course_title(payload) == "История России"
    assert models.extract_course_title(payload, "Предмет #111137") == "История России"

    # ...including inside the subject's own curriculum tree
    subject_payload = {"data": {"courseTitleForModule": "История России", "tree": {}}}
    assert models.find_course_title(subject_payload) == "История России"
    # and a numeric value is never accepted
    assert models.find_course_title({"courseTitleForModule": "111137"}) == ""


def test_course_title_is_found_in_deep_wrappers():
    """The name may hide behind wrappers and dictionaries several levels down."""
    assert models.find_course_title({"data": [{"entityInfo": {
        "course_title": {"value": "  История   России "}}}]}) == "История России"
    assert models.find_course_title({"a": {"b": {"c": {"d": {
        "e": {"courseTitleForModule": "Обществознание"}}}}}}) == "Обществознание"
    assert models.find_course_title({"moduleTitle": "111130"}) == ""
    assert models.find_course_title({"courseName": "#111130"}) == ""


def test_placeholders_are_not_names():
    """«Предмет #111137» / «#111130» are placeholders, «Домашка №1» is a name."""
    for placeholder in ("111137", "#111130", "Предмет #111137", "предмет №42",
                        "Домашка #777", "Урок #3", "ID: 12", "№ 5", "  "):
        assert models.looks_like_id(placeholder), placeholder
    for real in ("История России", "Домашка №1", "Домашка №12", "Раздел 2"):
        assert not models.looks_like_id(real), real


def test_cached_numeric_titles_are_repaired_without_network(temp_db):
    """An old cache shows real names: they are read from the stored payloads."""
    import json as _json

    db.upsert_course(111130, "Предмет #111130", {})
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO lessons(id, course_id, entity_lesson_id, title, position,"
        " payload_json, fetched_at) VALUES(?,?,?,?,?,?,?)",
        (41907180, 111130, 4772400, "Урок 1", 1,
         _json.dumps({"id": 41907180, "courseTitleForModule": "Обществознание"}), 1),
    )
    conn.execute(
        "INSERT INTO lesson_subjects(lesson_id, subject_id, title, position)"
        " VALUES(?,?,?,?)",
        (41907180, 900001, "", 1),
    )
    conn.commit()
    db.upsert_subject(900001, "#900001", {"title": "Домашка №7"})

    assert db.list_courses()[0]["title"] == "Обществознание"
    assert db.get_course(111130)["title"] == "Обществознание"
    homework = db.lesson_homeworks(41907180)[0]
    assert homework["title"] == "Домашка №7"
    assert db.homework_row(900001)["title"] == "Домашка №7"

    # ...and the repair is written back once, so it survives a restart
    assert db.repair_course_titles() == 2
    rows = db.get_connection().execute(
        "SELECT title FROM courses UNION ALL SELECT title FROM subjects"
    ).fetchall()
    assert {row["title"] for row in rows} == {"Обществознание", "Домашка №7"}


def test_sync_replaces_a_generic_course_title(temp_db, monkeypatch):
    """A cached «Предмет #111137» is repaired by the next sync."""
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import COURSE_ID, COURSE_TITLE, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-title.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    db.upsert_course(COURSE_ID, f"Предмет #{COURSE_ID}", {})
    assert db.course_title_is_generic(COURSE_ID)

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})
        thread = SyncThread(api, course_ids=[COURSE_ID])
        thread.finished_all.connect(lambda _r: None, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)

    assert db.get_course(COURSE_ID)["title"] == COURSE_TITLE
    assert not db.course_title_is_generic(COURSE_ID)


def test_sync_keeps_a_real_title_when_payload_has_none(temp_db, monkeypatch):
    """A nameless payload must not overwrite a good cached name."""
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import COURSE_ID, COURSE_TITLE, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-title2.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    db.upsert_course(COURSE_ID, COURSE_TITLE, {})

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})
        original = api.get_education_group
        nameless = {
            "data": [
                {"id": 41907128, "title": "Урок 1. Практика",
                 "entityInfo": {"id": 4772366, "title": "Практика"}},
            ],
        }
        monkeypatch.setattr(api, "get_education_group", lambda *a, **k: nameless)
        thread = SyncThread(api, course_ids=[COURSE_ID])
        thread.finished_all.connect(lambda _r: None, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)
        monkeypatch.setattr(api, "get_education_group", original)

    assert db.get_course(COURSE_ID)["title"] == COURSE_TITLE


def test_course_sync_expands_lessons_and_subjects(temp_db, monkeypatch):
    """One course link fills lessons, practice blocks and their tests."""
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import (
        COURSE_ID,
        COURSE_TITLE,
        KBS_ONLINE,
        LESSON2_DB_ID,
        LESSON2_ENTITY_ID,
        LESSON_DB_ID,
        LESSON_ENTITY_ID,
        SUBJECT2_ID,
        SUBJECT_ID,
        TESTS_EXTRA,
        TESTS_OFFLINE,
        TESTS_ONLINE,
        FakeServer,
    )

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-course.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})

        thread = SyncThread(api, course_ids=[COURSE_ID])
        results: list = []
        subjects: list = []
        thread.finished_all.connect(results.extend, type=Qt.ConnectionType.DirectConnection)
        thread.subject_done.connect(subjects.append, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)

        # the documented endpoints were used
        paths = [path.split("?")[0] for path in server.requests]
        assert f"/api/v3/student/education-groups/{COURSE_ID}" in paths
        assert (
            f"/api/v3/student/education-groups/{COURSE_ID}/structures/{LESSON_DB_ID}" in paths
        )
        assert "/api/v1/content/practice/curriculumsubject/777" in paths

    assert db.get_course(COURSE_ID)["title"] == COURSE_TITLE
    lessons = db.course_lessons(COURSE_ID)
    assert [lesson["id"] for lesson, _subjects in lessons] == [LESSON_DB_ID, LESSON2_DB_ID]
    assert [subject["id"] for subject in lessons[0][1]] == [SUBJECT_ID]
    # every lesson lists its own домашку — the bug the user reported
    assert [subject["id"] for subject in lessons[1][1]] == [SUBJECT2_ID]
    assert db.homework_row(SUBJECT_ID)["lesson"]["id"] == LESSON_DB_ID
    assert db.homework_row(SUBJECT2_ID)["lesson"]["id"] == LESSON2_DB_ID

    # the answer endpoint needs the entity lesson id, not the database one
    assert db.lesson_id_for_subject(SUBJECT_ID) == LESSON_ENTITY_ID
    assert db.lesson_id_for_subject(SUBJECT2_ID) == LESSON2_ENTITY_ID
    assert db.get_lesson(LESSON_DB_ID)["entity_lesson_id"] == LESSON_ENTITY_ID

    assert db.kbs_stats(KBS_ONLINE)["total"] == len(TESTS_ONLINE)
    assert len(results) == 1
    result = results[0]
    assert result.course_id == COURSE_ID
    assert (result.lessons, result.subjects) == (2, 2)
    total_tests = len(TESTS_ONLINE) + len(TESTS_EXTRA) + len(TESTS_OFFLINE)
    assert result.tests == total_tests
    assert not result.error
    assert [item.subject_id for item in subjects] == [SUBJECT_ID, SUBJECT2_ID]

    stats = db.course_stats(COURSE_ID)
    assert stats["homeworks"] == 2 and stats["total"] == total_tests
    assert stats["sections"] == 3 and stats["lessons"] == 2


def test_course_pagination_uses_last_known_id(temp_db, monkeypatch):
    """`page=N` does not work: the next slice is asked by `lastKnownId`."""
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import COURSE_ID, LESSON2_DB_ID, LESSON_DB_ID, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-cursor.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))
    monkeypatch.setattr(config, "COURSE_PAGE_SIZE", 1)

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})
        thread = SyncThread(api, course_ids=[COURSE_ID])
        thread.start()
        assert thread.wait(30_000)

        lesson_requests = [
            path for path in server.requests
            if path.split("?")[0].endswith(f"education-groups/{COURSE_ID}")
        ]
        assert "perPage=1" in lesson_requests[0]
        assert "lastKnownId" not in lesson_requests[0]
        assert any(f"lastKnownId={LESSON_DB_ID}" in path for path in lesson_requests)
        assert any(f"lastKnownId={LESSON2_DB_ID}" in path for path in lesson_requests)
        # the deprecated page parameter would be rejected by the backend
        assert all("page=" not in path for path in server.requests)

    lessons = db.course_homeworks(COURSE_ID)["lessons"]
    assert [lesson["id"] for lesson, _ in lessons] == [LESSON_DB_ID, LESSON2_DB_ID]


def test_course_sync_skips_structure_errors(temp_db, monkeypatch):
    """A broken lesson structure must not abort the rest of the course."""
    from mt.api import Api, ApiError
    from mt.sync import SyncThread
    from tests.fake_server import COURSE_ID, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-course2.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})
        original = api.get_lesson_structure
        calls = {"n": 0}

        def flaky(course_id: int, lesson_id: int):
            calls["n"] += 1
            if calls["n"] == 2:
                raise ApiError("HTTP 500: boom", status=500)
            return original(course_id, lesson_id)

        monkeypatch.setattr(api, "get_lesson_structure", flaky)
        thread = SyncThread(api, course_ids=[COURSE_ID])
        results: list = []
        thread.finished_all.connect(results.extend, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)

    assert results[0].errors == 1
    assert results[0].subjects == 1  # lesson 1 still synced its practice block


def test_database_upgrades_from_v2_to_course_schema(temp_db):
    """An existing cache must gain course tables without losing data."""
    import sqlite3

    from mt import db as database

    path = database.database_path()
    database.set_database_path(path)
    database.init()
    database.upsert_subject(555, "Старый предмет", {"title": "Старый предмет"})
    database.close()

    conn = sqlite3.connect(path)
    conn.executescript(
        "DROP TABLE lesson_subjects; DROP TABLE lessons; DROP TABLE courses;"
        "PRAGMA user_version = 2;"
    )
    conn.commit()
    conn.close()

    database.init()
    assert database.get_subject(555)["title"] == "Старый предмет"
    assert database.list_courses() == []
    database.upsert_course(1, "Новый курс", {})
    assert database.list_courses()[0]["title"] == "Новый курс"
    with sqlite3.connect(path) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# subject → занятие → домашка grouping
# --------------------------------------------------------------------------- #
def test_lesson_meta_parses_date_status_progress_topics():
    import json as _json

    row = {
        "title": "Урок 1",
        "payload_json": _json.dumps({
            "number": 1,
            "title": "Урок 1",
            "date": "2025-09-03T19:40:00",
            "status": "visited",
            "progress": {"percent": 100},
            "timeStart": "19:40",
            "timeEnd": "21:10",
            "topics": ["VIII–XV вв. Судьбы Русских земель", "Дополнительные материалы"],
        }),
    }
    meta = models.lesson_meta(row)
    assert meta.heading == "Урок 1"
    assert meta.date_text == "3 сентября"
    assert meta.status == "Посещено"
    assert meta.status_kind == "success"
    assert meta.progress == 100
    assert meta.time_text == "19:40-21:10"
    assert meta.topics == (
        "VIII–XV вв. Судьбы Русских земель", "Дополнительные материалы",
    )

    viewed = models.lesson_meta(
        {"title": "Урок", "payload_json": _json.dumps({"status": "viewed", "date": "10.09.2025"})}
    )
    assert viewed.status == "Просмотрено"
    assert viewed.status_kind == "outline"
    assert viewed.date_text == "10 сентября"

    empty = models.lesson_meta({"title": "Без данных", "payload_json": "{}"})
    assert empty.status == "" and empty.date_text == "" and empty.topics == ()
    assert models.format_lesson_date("") == ""
    assert models.format_lesson_date("не дата") == ""


def test_subject_sync_keeps_a_real_name(temp_db, monkeypatch):
    """A numeric домашка title from the API must not replace the real name."""
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import (
        COURSE_ID,
        COURSE_TITLE,
        SUBJECT_ID,
        SUBJECT_TITLE,
        FakeServer,
    )

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-subject-title.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})
        original = api.get_curriculum_subject

        def numeric_title(subject_id: int):
            payload = original(subject_id)
            payload["title"] = str(subject_id)  # the backend sometimes does this
            return payload

        monkeypatch.setattr(api, "get_curriculum_subject", numeric_title)
        thread = SyncThread(api, course_ids=[COURSE_ID])
        thread.finished_all.connect(lambda _r: None, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)

    # a wildcard search would return «Раздел 1» — the домашка must keep its name
    assert models.subject_title({"title": "777", "tree": {"online": [
        {"id": 501, "title": "Раздел 1"},
    ]}}, "") == ""
    assert db.title_is_generic(f"Домашка #{SUBJECT_ID}")
    assert db.title_is_generic(str(SUBJECT_ID))       # a bare id is no name
    assert db.title_is_generic(f"Предмет #{COURSE_ID}")
    assert not db.title_is_generic(SUBJECT_TITLE)
    assert not db.title_is_generic(COURSE_TITLE)
    assert db.get_subject(SUBJECT_ID)["title"] == SUBJECT_TITLE
    assert db.homework_row(SUBJECT_ID)["title"] == SUBJECT_TITLE


def test_homeworks_follow_lesson_structure_not_kbs_lesson_id(temp_db):
    """A домашка belongs to the lesson whose structure contains it.

    The real curriculum payload carries no ``lessonId``, so grouping by
    ``knowledge_bases.lesson_id`` left every lesson page empty. The only
    reliable link is the lesson structure (``lesson_subjects``).
    """
    from tests.fake_server import (
        COURSE_ID,
        KBS_EXTRA,
        KBS_OFFLINE,
        KBS_ONLINE,
        LESSON2_DB_ID,
        LESSON2_ENTITY_ID,
        LESSON_DB_ID,
        LESSON_ENTITY_ID,
        SUBJECT2_ID,
        SUBJECT_ID,
    )

    db.upsert_subject(SUBJECT_ID, "Домашка №1", {})
    db.upsert_subject(SUBJECT2_ID, "Домашка №2", {})
    db.upsert_course(COURSE_ID, "История России", {})
    db.replace_course_lessons(COURSE_ID, [
        {"id": LESSON_DB_ID, "entity_id": LESSON_ENTITY_ID, "title": "Урок 1",
         "position": 0, "payload": {"number": 1, "date": "2025-09-03T19:40:00"}},
        {"id": LESSON2_DB_ID, "entity_id": LESSON2_ENTITY_ID, "title": "Урок 2",
         "position": 1, "payload": {"number": 2}},
    ])
    db.set_lesson_subjects(LESSON_DB_ID, [{"subject_id": SUBJECT_ID, "position": 0}])
    db.set_lesson_subjects(LESSON2_DB_ID, [{"subject_id": SUBJECT2_ID, "position": 0}])
    # no lesson_id on the knowledge bases — exactly like the real backend
    db.upsert_kbs(KBS_ONLINE, SUBJECT_ID, "online", "Раздел 1", None)
    db.upsert_kbs(KBS_EXTRA, SUBJECT_ID, "online", "Раздел 2", None)
    db.upsert_kbs(KBS_OFFLINE, SUBJECT2_ID, "offline", "Раздел 3", None)

    first = db.lesson_homeworks(LESSON_DB_ID)
    assert [homework["id"] for homework in first] == [SUBJECT_ID]
    assert first[0]["sections"] == 2 and first[0]["title"] == "Домашка №1"
    second = db.lesson_homeworks(LESSON2_DB_ID)
    assert [homework["id"] for homework in second] == [SUBJECT2_ID]
    assert second[0]["title"] == "Домашка №2"

    course_groups = db.course_homeworks(COURSE_ID)
    assert [lesson["id"] for lesson, _ in course_groups["lessons"]] == [
        LESSON_DB_ID, LESSON2_DB_ID
    ]
    assert [homework["id"] for homework in course_groups["lessons"][0][1]] == [SUBJECT_ID]
    assert [homework["id"] for homework in course_groups["lessons"][1][1]] == [SUBJECT2_ID]
    meta = models.lesson_meta(course_groups["lessons"][0][0])
    assert meta.heading == "Урок 1" and meta.date_text == "3 сентября"

    row = db.homework_row(SUBJECT_ID)
    assert row["title"] == "Домашка №1"
    assert row["lesson"]["id"] == LESSON_DB_ID
    assert row["course"]["id"] == COURSE_ID
    assert [section["id"] for section in db.homework_sections(SUBJECT_ID)] == [
        KBS_ONLINE, KBS_EXTRA
    ]

    # a homework that no lesson points at stays visible instead of vanishing
    db.upsert_subject(999, "Домашка без урока", {})
    assert [homework["id"] for homework in db.standalone_homeworks()] == [999]


# --------------------------------------------------------------------------- #
# pacing and cache durability
# --------------------------------------------------------------------------- #
def test_sync_delay_is_about_a_second_and_random(monkeypatch):
    from mt import config as cfg

    monkeypatch.setattr(cfg, "SYNC_DELAY_RANGE", (0.6, 1.4))
    samples = [cfg.sync_delay() for _ in range(200)]
    assert all(0.6 <= value <= 1.4 for value in samples)
    assert len(set(samples)) > 100          # not a fixed value
    assert 0.8 < sum(samples) / len(samples) < 1.2  # around one second

    monkeypatch.setattr(cfg, "SYNC_DELAY_RANGE", (0.0, 0.0))
    assert cfg.sync_delay() == 0.0
    monkeypatch.setattr(cfg, "SYNC_DELAY_RANGE", (0.7, 0.7))
    assert cfg.sync_delay() == 0.7


def test_sync_delay_env_override(monkeypatch):
    from mt import config as cfg

    monkeypatch.setenv("MT_SYNC_DELAY", "0.2, 0.4")
    assert cfg._delay_range_from_env() == (0.2, 0.4)
    monkeypatch.setenv("MT_SYNC_DELAY", "0")
    assert cfg._delay_range_from_env() == (0.0, 0.0)
    monkeypatch.setenv("MT_SYNC_DELAY", "garbage")
    assert cfg._delay_range_from_env() is None


def test_sync_pauses_between_requests(temp_db, monkeypatch):
    """Every request of a subject sync is followed by a random ~1 s pause."""
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import SUBJECT_ID, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-pause.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.6, 1.4))

    drawn: list[float] = []
    slept: list[float] = []
    real_delay = config.sync_delay

    def spy(*, deep: bool = False) -> float:
        value = real_delay(deep=deep)
        drawn.append(value)
        return value

    monkeypatch.setattr(config, "sync_delay", spy)
    monkeypatch.setattr(SyncThread, "msleep", lambda _self, ms: slept.append(ms / 1000))

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})
        thread = SyncThread(api, [SUBJECT_ID])
        thread.finished_all.connect(lambda _r: None, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)

    assert len(drawn) == 2  # one pause per knowledge base of the fake homework
    assert all(0.6 <= value <= 1.4 for value in drawn)
    assert len(set(drawn)) > 1  # randomized, not fixed
    # the worker sleeps in 100 ms slices, so cancellation stays responsive
    assert all(value <= 0.1 + 1e-9 for value in slept)
    assert sum(slept) == pytest.approx(sum(drawn), abs=0.3)


def test_failed_section_does_not_delete_cached_homework(temp_db, monkeypatch):
    """A transient error must never prune a cached раздел."""
    from mt.api import Api, ApiError
    from mt.sync import SyncThread
    from tests.fake_server import KBS_OFFLINE, SUBJECT2_ID, TESTS_OFFLINE, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-prune.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})

        first = SyncThread(api, [SUBJECT2_ID])
        first.finished_all.connect(lambda _r: None, type=Qt.ConnectionType.DirectConnection)
        first.start()
        assert first.wait(30_000)
        assert db.get_kbs(KBS_OFFLINE) is not None

        # second run cannot reach the offline homework
        original = api.get_kbs_tests

        def flaky(subject_id: int, kbs_id: int, mode: str):
            if kbs_id == KBS_OFFLINE:
                raise ApiError("HTTP 500: boom", status=500)
            return original(subject_id, kbs_id, mode)

        monkeypatch.setattr(api, "get_kbs_tests", flaky)
        second = SyncThread(api, [SUBJECT2_ID])
        results: list = []
        second.finished_all.connect(results.extend, type=Qt.ConnectionType.DirectConnection)
        second.start()
        assert second.wait(30_000)

    assert results[0].errors >= 1
    # nothing was pruned: the cached homework and its tests are intact
    assert db.get_kbs(KBS_OFFLINE) is not None
    assert len(db.list_tests(KBS_OFFLINE)) == len(TESTS_OFFLINE)


def test_shorter_response_keeps_previously_cached_tests(temp_db, monkeypatch):
    """A shorter (or partially empty) response must not delete cached tests."""
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import KBS_ONLINE, SUBJECT_ID, TESTS_ONLINE, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-merge.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})

        first = SyncThread(api, [SUBJECT_ID])
        first.finished_all.connect(lambda _r: None, type=Qt.ConnectionType.DirectConnection)
        first.start()
        assert first.wait(30_000)
        assert db.kbs_stats(KBS_ONLINE)["total"] == len(TESTS_ONLINE)

        # the server suddenly answers with a single test of that homework
        original = api.get_kbs_tests

        def shortened(subject_id: int, kbs_id: int, mode: str):
            payload = original(subject_id, kbs_id, mode)
            if kbs_id == KBS_ONLINE and isinstance(payload, list):
                return payload[:1]
            return payload

        monkeypatch.setattr(api, "get_kbs_tests", shortened)
        second = SyncThread(api, [SUBJECT_ID])
        results: list = []
        second.finished_all.connect(results.extend, type=Qt.ConnectionType.DirectConnection)
        second.start()
        assert second.wait(30_000)

    # nothing was lost: the five original tests are still there
    assert db.kbs_stats(KBS_ONLINE)["total"] == len(TESTS_ONLINE)
    assert db.list_tests(KBS_ONLINE)[0]["id"] == TESTS_ONLINE[0]["id"]
    assert results[0].errors == 0


# --------------------------------------------------------------------------- #
# lessons_block courses (the shape some real courses answer with)
# --------------------------------------------------------------------------- #
def test_models_tell_a_lesson_block_from_a_lesson():
    """A ``lessons_block`` has no entityInfo; its lessons live in /structures."""
    payload = {
        "data": [
            {"id": 41892738, "title": "III семестр", "type": "lessons_block",
             "sort": 1, "entityInfo": None, "children": None},
            {"id": 41894018, "title": "Каникулы: Зима", "type": "lesson_entity",
             "sort": 4, "entityInfo": {"id": 4771170, "title": "Каникулы", "type": "vacation"},
             "children": None},
        ],
    }
    nodes = models.course_nodes(payload)
    assert [models.node_id(node) for node in nodes] == [41892738, 41894018]
    assert not models.is_lesson_node(nodes[0])
    assert models.is_lesson_node(nodes[1])

    structure = {
        "data": {
            "id": 41892738, "title": "III семестр", "type": "lessons_block",
            "entityInfo": None,
            "children": [
                {"id": 41892739, "title": "Занятие 1", "type": "lesson_entity",
                 "entityInfo": {"id": 4771263, "title": "Занятие 1"}, "children": None},
            ],
        }
    }
    children = models.structure_children(structure)
    assert [child["id"] for child in children] == [41892739]
    assert models.is_lesson_node(children[0])
    assert models.structure_node(structure)["title"] == "III семестр"
    assert models.structure_children({"data": {"id": 1, "children": []}}) == []
    assert models.structure_children(None) == []

    # unknown shapes keep the old «treat it as a lesson» behaviour
    assert models.is_lesson_node({"id": 5, "title": "Урок без entityInfo"})
    assert models.is_rest_node(nodes[1])          # «Каникулы: Зима»
    assert not models.is_rest_node(nodes[0])
    assert not models.is_lesson_node({"id": 6, "type": "curriculum_module"})
    assert models.is_lesson_node(None) is False


def test_course_with_lessons_blocks_is_expanded(temp_db, monkeypatch):
    """Lessons of a ``lessons_block`` are fetched from /structures, not lost."""
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import (
        BLOCK_COURSE_ID,
        BLOCK_DB_ID,
        BLOCK_LESSON_DB_ID,
        BLOCK_LESSON_ENTITY_ID,
        BLOCK_SUBJECT_ID,
        BLOCK_SUBJECT_TITLE,
        VACATION_ID,
        FakeServer,
    )

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-block.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})
        thread = SyncThread(api, course_ids=[BLOCK_COURSE_ID])
        results: list = []
        thread.finished_all.connect(results.extend, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)

        paths = [path.split("?")[0] for path in server.requests]

    assert results and results[0].error is None
    assert results[0].errors == 0
    assert db.get_course(BLOCK_COURSE_ID) is not None

    lessons = db.course_homeworks(BLOCK_COURSE_ID)["lessons"]
    ids = [int(lesson["id"]) for lesson, _homeworks in lessons]
    assert ids == [BLOCK_LESSON_DB_ID]        # the lesson inside the block
    assert BLOCK_DB_ID not in ids             # the block itself is not a lesson
    assert VACATION_ID not in ids             # «Каникулы» is not a lesson
    row = db.get_lesson(BLOCK_LESSON_DB_ID)
    assert row["entity_lesson_id"] == BLOCK_LESSON_ENTITY_ID
    assert row["title"] == "Занятие 1"

    # the homework of that lesson was synced all the way down to its tests
    assert db.get_subject(BLOCK_SUBJECT_ID)["title"] == BLOCK_SUBJECT_TITLE
    assert db.homework_row(BLOCK_SUBJECT_ID)["total"] == 1

    assert f"/api/v3/student/education-groups/{BLOCK_COURSE_ID}/structures/{BLOCK_DB_ID}" in paths
    assert f"/api/v3/student/education-groups/{BLOCK_COURSE_ID}/structures/{BLOCK_LESSON_DB_ID}" in paths
    # a vacation is skipped without even asking for its structure
    assert f"/api/v3/student/education-groups/{BLOCK_COURSE_ID}/structures/{VACATION_ID}" not in paths


def test_flat_course_is_parsed_without_block_requests(temp_db, monkeypatch):
    """The block logic must not add requests to courses that list lessons."""
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import COURSE_ID, LESSON2_DB_ID, LESSON_DB_ID, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-flat.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})
        thread = SyncThread(api, course_ids=[COURSE_ID])
        thread.finished_all.connect(lambda _r: None, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)

        structures = [
            path.split("?")[0]
            for path in server.requests
            if "/structures/" in path
        ]

    assert structures == [
        f"/api/v3/student/education-groups/{COURSE_ID}/structures/{LESSON_DB_ID}",
        f"/api/v3/student/education-groups/{COURSE_ID}/structures/{LESSON2_DB_ID}",
    ]


def test_block_pagination_uses_the_node_id_as_cursor(temp_db, monkeypatch):
    """``lastKnownId`` is the id of the *node*, never of a lesson inside it."""
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import BLOCK_COURSE_ID, BLOCK_DB_ID, BLOCK_LESSON_DB_ID, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-cursor.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))
    monkeypatch.setattr(config, "COURSE_PAGE_SIZE", 1)

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})
        thread = SyncThread(api, course_ids=[BLOCK_COURSE_ID])
        thread.finished_all.connect(lambda _r: None, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)

        page_urls = [
            path for path in server.requests
            if path.startswith(f"/api/v3/student/education-groups/{BLOCK_COURSE_ID}?")
        ]

    assert len(page_urls) >= 2, page_urls
    assert f"lastKnownId={BLOCK_DB_ID}" in page_urls[1]
    assert f"lastKnownId={BLOCK_LESSON_DB_ID}" not in " ".join(page_urls)
    assert db.get_lesson(BLOCK_LESSON_DB_ID) is not None


def test_failed_block_keeps_cached_lessons(temp_db, monkeypatch):
    """A block that cannot be expanded must not delete the cached lessons."""
    from mt.api import Api, ApiError
    from mt.sync import SyncThread
    from tests.fake_server import BLOCK_COURSE_ID, BLOCK_DB_ID, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-broken.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    db.upsert_course(BLOCK_COURSE_ID, "Английский язык", {})
    db.replace_course_lessons(BLOCK_COURSE_ID, [
        {"id": 555001, "entity_id": 666001, "title": "Старый урок", "position": 1, "payload": {}},
    ])

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})
        original = api.get_lesson_structure

        def broken(course_id: int, node_id: int):
            if node_id == BLOCK_DB_ID:
                raise ApiError("HTTP 500: boom", status=500)
            return original(course_id, node_id)

        monkeypatch.setattr(api, "get_lesson_structure", broken)
        thread = SyncThread(api, course_ids=[BLOCK_COURSE_ID])
        results: list = []
        thread.finished_all.connect(results.extend, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(30_000)

    assert results[0].errors >= 1
    assert db.get_lesson(555001) is not None   # nothing was pruned
    assert db.get_course(BLOCK_COURSE_ID)["title"] == "Английский язык"


def test_deep_sync_walks_every_course_last_first(temp_db, monkeypatch):
    """A recursive run takes the whole library, the last course first."""
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import (
        BLOCK_COURSE_ID,
        BLOCK_LESSON_DB_ID,
        COURSE_ID,
        LESSON2_DB_ID,
        LESSON_DB_ID,
        FakeServer,
    )

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-deep.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    db.upsert_course(COURSE_ID, "История России", {})
    db.upsert_course(BLOCK_COURSE_ID, "Английский язык", {})

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})
        thread = SyncThread(api, deep=True)
        results: list = []
        log: list[str] = []
        thread.finished_all.connect(results.extend, type=Qt.ConnectionType.DirectConnection)
        thread.log.connect(log.append, type=Qt.ConnectionType.DirectConnection)
        thread.start()
        assert thread.wait(60_000)

        course_pages = [
            path for path in server.requests
            if path.startswith("/api/v3/student/education-groups/")
            and "/structures/" not in path
        ]

    assert results and results[0].error is None
    assert any("глубокая синхронизация" in line for line in log)
    # the biggest id is the course added last — it goes first
    assert course_pages[0].startswith(f"/api/v3/student/education-groups/{COURSE_ID}")
    assert any(str(BLOCK_COURSE_ID) in path for path in course_pages)

    assert {int(row["id"]) for row in db.list_courses()} == {COURSE_ID, BLOCK_COURSE_ID}
    for lesson_id in (LESSON_DB_ID, LESSON2_DB_ID, BLOCK_LESSON_DB_ID):
        assert db.get_lesson(lesson_id) is not None
    assert db.get_subject(777) is not None
    assert db.get_subject(900) is not None


def test_deep_sync_pace_is_rarer_than_usual(monkeypatch):
    """The recursive walk sleeps longer; disabling pacing disables both."""
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.6, 1.4))
    monkeypatch.setattr(config, "DEEP_SYNC_DELAY_RANGE", (0.9, 2.1))

    usual = [config.sync_delay() for _ in range(100)]
    deep = [config.sync_delay(deep=True) for _ in range(100)]
    assert all(0.6 <= value <= 1.4 for value in usual)
    assert all(0.9 <= value <= 2.1 for value in deep)
    assert config.delay_range(deep=True) == (0.9, 2.1)
    assert config.delay_range() == (0.6, 1.4)

    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))
    assert config.sync_delay(deep=True) == 0.0


def _block_api(monkeypatch, payload, structures):
    """An Api whose course endpoints answer from dictionaries."""
    from mt.api import Api

    api = Api()
    api.set_cookies({"sessionid": "valid"})
    monkeypatch.setattr(api, "get_education_group", lambda *a, **k: payload)
    monkeypatch.setattr(api, "get_lesson_structure", lambda _cid, nid: structures[nid])
    monkeypatch.setattr(
        api,
        "get_curriculum_subject",
        lambda sid: {
            "id": sid,
            "title": f"Домашка #{sid}",
            "tree": {"online": [{"id": 7000 + sid, "title": "Раздел"}], "offline": [], "aftertheory": []},
        },
    )
    monkeypatch.setattr(api, "get_kbs_tests", lambda *a, **k: [])
    return api


def test_nested_blocks_are_expanded_recursively(temp_db, monkeypatch):
    """A block may contain another block — the walk keeps going down."""
    from mt.sync import SyncThread

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-nested.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    payload = {
        "title": "Курс",
        "data": [
            {"id": 1, "type": "lessons_block", "title": "Уровень 1",
             "entityInfo": None, "children": None},
        ],
    }
    structures = {
        1: {"data": {"id": 1, "type": "lessons_block", "children": [
            {"id": 2, "type": "lessons_block", "title": "Уровень 2",
             "entityInfo": None, "children": None},
            {"id": 3, "type": "lesson_entity", "title": "Первый урок",
             "entityInfo": {"id": 30, "title": "Первый урок"}, "children": None},
        ]}},
        2: {"data": {"id": 2, "type": "lessons_block", "children": [
            {"id": 4, "type": "lesson_entity", "title": "Вложенный урок",
             "entityInfo": {"id": 40, "title": "Вложенный урок"}, "children": None},
        ]}},
        3: {"data": {"id": 3, "children": [
            {"type": "curriculum_practice_block", "entityInfo": {"id": 700, "title": "Домашка A"}},
        ]}},
        4: {"data": {"id": 4, "children": [
            {"type": "curriculum_practice_block", "entityInfo": {"id": 800, "title": "Домашка B"}},
        ]}},
    }
    api = _block_api(monkeypatch, payload, structures)

    thread = SyncThread(api, course_ids=[555])
    results: list = []
    thread.finished_all.connect(results.extend, type=Qt.ConnectionType.DirectConnection)
    thread.start()
    assert thread.wait(30_000)

    assert results[0].errors == 0
    lessons = db.course_homeworks(555)["lessons"]
    # the order of the children is kept: the nested block comes first
    assert [int(lesson["id"]) for lesson, _ in lessons] == [4, 3]
    assert [lesson["position"] for lesson, _ in lessons] == [0, 1]
    assert db.homework_row(700) is not None
    assert db.homework_row(800) is not None


def test_block_nesting_stops_at_the_depth_limit(temp_db, monkeypatch):
    """A malformed chain of blocks stops safely and reports an error."""
    from mt.sync import SyncThread

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-depth.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))
    monkeypatch.setattr(config, "COURSE_STRUCTURE_DEPTH", 1)

    payload = {"title": "Курс", "data": [
        {"id": 1, "type": "lessons_block", "title": "Блок 1", "entityInfo": None, "children": None},
    ]}
    structures = {
        1: {"data": {"id": 1, "children": [
            {"id": 2, "type": "lessons_block", "title": "Блок 2", "entityInfo": None, "children": None},
        ]}},
        2: {"data": {"id": 2, "children": []}},
    }
    api = _block_api(monkeypatch, payload, structures)

    db.upsert_course(555, "Курс", {})
    db.replace_course_lessons(555, [
        {"id": 9001, "entity_id": 9002, "title": "Старый урок", "position": 1, "payload": {}},
    ])

    thread = SyncThread(api, course_ids=[555])
    results: list = []
    thread.finished_all.connect(results.extend, type=Qt.ConnectionType.DirectConnection)
    thread.start()
    assert thread.wait(30_000)

    assert results[0].errors == 1
    # nothing was pruned by the incomplete walk
    assert db.get_lesson(9001) is not None


def test_lesson_items_filter_out_practice_blocks():
    """Expanding a block must never treat theory/practice as a lesson."""
    assert models.is_lesson_item({"type": "lesson_entity", "entityInfo": {"id": 1}})
    assert models.is_lesson_item({"type": "lessons_block"})
    assert not models.is_lesson_item(
        {"type": "curriculum_practice_block", "entityInfo": {"id": 777}}
    )
    assert not models.is_lesson_item({"type": "curriculum_theory_block"})
    assert models.is_lesson_item({"id": 5})  # unknown shape: let the caller decide
