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
        KBS_OFFLINE,
        KBS_ONLINE,
        LESSON2_ENTITY_ID,
        LESSON_ENTITY_ID,
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
        assert result.kbs == 3
        assert result.tests == 7
        assert result.errors == 0

        subject = db.get_subject(SUBJECT_ID)
        assert subject["title"] == "Тестовый предмет"
        assert {row["id"] for row in db.list_kbs(SUBJECT_ID)} == {
            KBS_ONLINE, KBS_EXTRA, KBS_OFFLINE
        }
        assert db.kbs_stats(KBS_ONLINE)["total"] == 5
        assert db.kbs_stats(KBS_OFFLINE)["total"] == 1
        # the fallback mode probe must not leave duplicate tests behind
        assert len(db.list_tests(KBS_OFFLINE)) == 1
        assert db.get_kbs(KBS_OFFLINE)["lesson_id"] == LESSON2_ENTITY_ID
        assert db.get_kbs(KBS_ONLINE)["lesson_id"] == LESSON_ENTITY_ID

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
        assert payload["title"] == "Тестовый предмет"

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
    from tests.fake_server import KBS_OFFLINE, SUBJECT_ID, TESTS_OFFLINE, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-mode.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))
    monkeypatch.setattr(config, "MODES", ("online", "offline", "aftertheory"))

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})
        thread = SyncThread(api, [SUBJECT_ID])
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
        "meta": {"last_page": 1},
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


def test_course_sync_expands_lessons_and_subjects(temp_db, monkeypatch):
    """One course link fills lessons, practice blocks and their tests."""
    from mt.api import Api
    from mt.sync import SyncThread
    from tests.fake_server import (
        COURSE_ID,
        COURSE_TITLE,
        KBS_ONLINE,
        LESSON2_DB_ID,
        LESSON_DB_ID,
        LESSON_ENTITY_ID,
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
    assert lessons[1][1] == []  # a theory-only lesson has no practice block

    # the answer endpoint needs the entity lesson id, not the database one
    assert db.lesson_id_for_subject(SUBJECT_ID) == LESSON_ENTITY_ID
    assert db.get_lesson(LESSON_DB_ID)["entity_lesson_id"] == LESSON_ENTITY_ID

    assert db.kbs_stats(KBS_ONLINE)["total"] == len(TESTS_ONLINE)
    assert len(results) == 1
    result = results[0]
    assert result.course_id == COURSE_ID
    assert (result.lessons, result.subjects) == (2, 1)
    total_tests = len(TESTS_ONLINE) + len(TESTS_EXTRA) + len(TESTS_OFFLINE)
    assert result.tests == total_tests
    assert not result.error
    assert [item.subject_id for item in subjects] == [SUBJECT_ID]

    stats = db.course_stats(COURSE_ID)
    assert stats["subjects"] == 1 and stats["total"] == total_tests


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
        assert check.execute("PRAGMA user_version").fetchone()[0] == 3


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
    assert meta.heading == "Занятие 1"
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


def test_subject_homeworks_groups_by_entity_lesson(temp_db):
    """Домашки belong to the lesson whose entity id matches their lessonId."""
    from tests.fake_server import (
        KBS_EXTRA,
        KBS_OFFLINE,
        LESSON2_DB_ID,
        LESSON2_ENTITY_ID,
        LESSON_DB_ID,
        LESSON_ENTITY_ID,
        SUBJECT_ID,
    )

    db.upsert_subject(SUBJECT_ID, "Предмет", {})
    db.upsert_course(111137, "Демо-курс", {})
    db.replace_course_lessons(111137, [
        {"id": LESSON_DB_ID, "entity_id": LESSON_ENTITY_ID, "title": "Урок 1",
         "position": 0, "payload": {"number": 1, "date": "2025-09-03T19:40:00"}},
        {"id": LESSON2_DB_ID, "entity_id": LESSON2_ENTITY_ID, "title": "Урок 2",
         "position": 1, "payload": {"number": 2}},
    ])
    db.set_lesson_subjects(LESSON_DB_ID, [{"subject_id": SUBJECT_ID, "position": 0}])
    db.set_lesson_subjects(LESSON2_DB_ID, [{"subject_id": SUBJECT_ID, "position": 0}])
    for kid, lesson, title in (
        (KBS_EXTRA - 2, LESSON_ENTITY_ID, "Домашка 1"),
        (KBS_EXTRA, LESSON_ENTITY_ID, "Домашка 2"),
        (KBS_OFFLINE, LESSON2_ENTITY_ID, "Домашка 3"),
    ):
        db.upsert_kbs(kid, SUBJECT_ID, "online", title, lesson)

    grouped = db.subject_homeworks(SUBJECT_ID)
    assert [lesson["id"] for lesson, _ in grouped["lessons"]] == [
        LESSON_DB_ID, LESSON2_DB_ID
    ]
    first, second = grouped["lessons"]
    assert [homework["id"] for homework in first[1]] == [KBS_EXTRA - 2, KBS_EXTRA]
    assert [homework["id"] for homework in second[1]] == [KBS_OFFLINE]
    assert grouped["orphans"] == []

    # a homework without a lessonId is shown separately, never lost
    db.upsert_kbs(999, SUBJECT_ID, "online", "Без занятия", None)
    grouped = db.subject_homeworks(SUBJECT_ID)
    assert [homework["id"] for homework in grouped["orphans"]] == [999]

    # the course view groups the same way and keeps the lesson meta
    course_groups = db.course_homeworks(111137)
    assert [lesson["id"] for lesson, _ in course_groups["lessons"]] == [
        LESSON_DB_ID, LESSON2_DB_ID
    ]
    meta = models.lesson_meta(course_groups["lessons"][0][0])
    assert meta.heading == "Занятие 1" and meta.date_text == "3 сентября"

    row = db.homework_row(KBS_EXTRA)
    assert row["lesson"]["id"] == LESSON_DB_ID
    assert db.lesson_homeworks(LESSON_DB_ID)[0]["id"] == KBS_EXTRA - 2


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

    def spy() -> float:
        value = real_delay()
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

    assert len(drawn) == 3  # one pause per knowledge base of the fake subject
    assert all(0.6 <= value <= 1.4 for value in drawn)
    assert len(set(drawn)) > 1  # randomized, not fixed
    # the worker sleeps in 100 ms slices, so cancellation stays responsive
    assert all(value <= 0.1 + 1e-9 for value in slept)
    assert sum(slept) == pytest.approx(sum(drawn), abs=0.3)


def test_failed_section_does_not_delete_cached_homework(temp_db, monkeypatch):
    """A transient error must never prune a cached раздел."""
    from mt.api import Api, ApiError
    from mt.sync import SyncThread
    from tests.fake_server import KBS_OFFLINE, SUBJECT_ID, TESTS_OFFLINE, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", config.DATA_DIR / "cookies-prune.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))

    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})

        # first run caches both homeworks
        first = SyncThread(api, [SUBJECT_ID])
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
        second = SyncThread(api, [SUBJECT_ID])
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
