"""Contract tests with synthetic credentials only; never use account cookies."""
import json

import pytest
import requests
from PyQt6.QtCore import Qt

from mt import config, db
from mt.api import AccessDeniedError, Api, ApiError, AuthError, RateLimitError
from mt.batch import LessonSender, Submission, lesson_plan


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "COOKIES_PATH", tmp_path / "cookies.json")
    monkeypatch.setattr(config, "BASE_URL", "https://example.test")
    api = Api()
    api.set_cookies({"token": "synthetic-old", "session": "synthetic-session"})
    return api


def response(status=200, *, location=None, cookies=None, html=False):
    result = requests.Response()
    result.status_code = status
    result.headers["Content-Type"] = "text/html" if html else "application/json"
    result._content_consumed = True
    result._content = b"<html>blocked</html>" if html else b'{"result":"true","percent":100}'
    if location:
        result.headers["Location"] = location
    result.cookies = requests.utils.cookiejar_from_dict(cookies or {})
    return result


def scripted(monkeypatch, client, replies):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return replies.pop(0)

    monkeypatch.setattr(client._session, "request", request)
    return calls


def test_import_replaces_and_preserves_cookie_bytes(client):
    raw = "Cookie: iam_token=a.b.c==; token=x+y/z==; session=new; *yasc=abc="
    client.set_cookies_from_string(raw)
    assert client.cookie_header == raw.removeprefix("Cookie: ")
    assert Api().cookie_header == client.cookie_header
    assert "synthetic" not in client.cookie_header


def test_same_origin_redirect_uses_rotated_cookie(client, monkeypatch):
    calls = scripted(monkeypatch, client, [
        response(302, location="/api/next", cookies={"token": "synthetic-new"}), response(),
    ])
    client.get_json("/api/start")
    assert "token=synthetic-old" in calls[0][2]["headers"]["Cookie"]
    assert "token=synthetic-new" in calls[1][2]["headers"]["Cookie"]
    assert calls[1][2]["headers"]["Cookie"].count("token=") == 1
    assert all(call[2]["allow_redirects"] is False for call in calls)
    assert "synthetic-new" in Api().cookie_header


@pytest.mark.parametrize("status, error", [(401, AuthError), (403, AccessDeniedError)])
def test_failed_response_does_not_destroy_session(client, monkeypatch, status, error):
    before = client.cookie_header
    scripted(monkeypatch, client, [response(status, cookies={"token": ""})])
    with pytest.raises(error) as caught:
        client.get_json("/api/resource")
    assert caught.value.status == status
    assert f"GET /api/resource: HTTP {status}" in str(caught.value)
    assert "synthetic" not in str(caught.value)
    assert client.cookie_header == Api().cookie_header == before


def test_cross_origin_redirect_never_receives_cookies(client, monkeypatch):
    calls = scripted(monkeypatch, client, [response(302, location="https://elsewhere.test/login")])
    with pytest.raises(AuthError, match="cookies не переданы"):
        client.get_json("/api/resource")
    assert len(calls) == 1


def test_post_redirect_is_not_replayed(client, monkeypatch):
    calls = scripted(monkeypatch, client, [response(307, location="/other")])
    with pytest.raises(ApiError, match="не повторён"):
        client.post_answer(12, 34, 56, ["answer"], 60)
    assert len(calls) == 1


def test_html_is_not_mislabeled_expired_session(client, monkeypatch):
    before = client.cookie_header
    scripted(monkeypatch, client, [response(html=True, cookies={"token": "wrong"})])
    with pytest.raises(ApiError, match="HTML вместо JSON") as caught:
        client.get_json("/api/resource")
    assert not isinstance(caught.value, AuthError)
    assert client.cookie_header == before


def test_all_endpoint_paths_query_and_post_contract(client, monkeypatch):
    calls = scripted(monkeypatch, client, [response() for _ in range(5)])
    client.get_curriculum_subject(123)
    client.get_kbs_tests(123, 456, "offline")
    client.get_education_group(789, last_known_id=1011)
    client.get_lesson_structure(789, 1213)
    client.post_answer(123, 1415, 1617, ["answer"], 67)
    assert [(m, url.removeprefix(config.BASE_URL)) for m, url, _ in calls] == [
        ("GET", "/api/v1/content/practice/curriculumsubject/123"),
        ("GET", "/api/v1/content/practice/curriculumsubject/123/knowledgebase/456/offline/tests"),
        ("GET", "/api/v3/student/education-groups/789"),
        ("GET", "/api/v3/student/education-groups/789/structures/1213"),
        ("POST", "/api/v1/content/practice/curriculumsubject/123/tests/1617"),
    ]
    assert calls[2][2]["params"] == {"perPage": 50, "lastKnownId": 1011}
    assert calls[-1][2]["json"] == {
        "lessonId": "1415", "attaches": [], "answer": ["answer"], "spentTime": 67,
    }
    assert all(c[2]["headers"]["Cookie"] == client.cookie_header for c in calls)


@pytest.fixture
def cached_lesson(monkeypatch, tmp_path):
    from mt.sync import SyncThread
    from tests.fake_server import COURSE_ID, LESSON_DB_ID, FakeServer

    monkeypatch.setattr(config, "COOKIES_PATH", tmp_path / "batch-cookies.json")
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))
    db.set_database_path(tmp_path / "batch.sqlite3")
    db.init()
    with FakeServer() as server:
        monkeypatch.setattr(config, "BASE_URL", server.base_url)
        api = Api()
        api.set_cookies({"sessionid": "valid"})
        worker = SyncThread(api, course_ids=[COURSE_ID])
        worker.start()
        assert worker.wait(30_000)
        # A lesson with two homeworks: all supported tasks must be included.
        db.set_lesson_subjects(LESSON_DB_ID, [
            {"subject_id": 777, "title": "Первая"}, {"subject_id": 888, "title": "Вторая"},
        ])
        yield api, LESSON_DB_ID
    db.close()


def test_lesson_plan_uses_entity_id_and_all_homeworks(cached_lesson):
    from tests.fake_server import LESSON_ENTITY_ID

    _, lesson_id = cached_lesson
    plan, skipped = lesson_plan(lesson_id)
    assert {item.subject_id for item in plan} == {777, 888}
    assert all(item.lesson_id == LESSON_ENTITY_ID for item in plan)
    assert len(plan) == 6 and skipped == 1
    assert len({(item.subject_id, item.test_id) for item in plan}) == len(plan)
    assert all(45 <= item.spent <= 90 and item.answer for item in plan)
    with pytest.raises(ValueError, match="entity lessonId"):
        lesson_plan(999999)


@pytest.mark.parametrize("stop_reason", ["cancel", "auth", "forbidden", "rate", "none"])
def test_sender_stops_safely_and_reports_each_result(client, monkeypatch, stop_reason):
    monkeypatch.setattr(config, "SYNC_DELAY_RANGE", (0.0, 0.0))
    plan = [Submission(12, tid, 34, ["answer"], 60) for tid in (1, 2, 3)]
    calls, results = [], []

    def post(*args):
        calls.append(args)
        if stop_reason == "auth":
            raise AuthError("HTTP 401")
        if stop_reason == "forbidden":
            raise AccessDeniedError("HTTP 403")
        if stop_reason == "rate":
            raise RateLimitError("HTTP 429")
        return {"result": "true", "percent": 100}

    monkeypatch.setattr(client, "post_answer", post)
    worker = LessonSender(client, plan)

    def record(sid, tid, message):
        results.append((sid, tid, message))
        if stop_reason == "cancel":
            worker.stop()

    worker.item_done.connect(record, type=Qt.ConnectionType.DirectConnection)
    worker.start()
    assert worker.wait(30_000)
    expected = 3 if stop_reason == "none" else 1
    assert len(calls) == len(results) == expected
    assert all(call[1] == "34" for call in calls)
    assert json.dumps(calls)
