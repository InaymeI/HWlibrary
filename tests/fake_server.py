"""In-process fake of education.maximumtest.ru used by the test-suite.

It mimics the endpoints the application talks to, including pagination-free
lists, cookie checks and the answer endpoint, so the whole sync pipeline can be
tested without touching the network.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

#: Terminology follows the site URLs:
#:   courses/<id> = предмет, lesson/<id> = урок, subjects/<id> = домашка,
#:   kbs/<id> = раздел внутри домашки.
SUBJECT_ID = 777          # домашка первого урока
SUBJECT_TITLE = "Домашка №1"
SUBJECT2_ID = 888         # домашка второго урока
SUBJECT2_TITLE = "Домашка №2"
KBS_ONLINE = 501          # раздел домашки №1
KBS_EXTRA = 503           # второй раздел той же домашки
KBS_OFFLINE = 502         # единственный раздел домашки №2

#: Course used by the course-structure tests (v3 education-groups API).
COURSE_ID = 111137
COURSE_TITLE = "История России"
LESSON_ENTITY_ID = 4772366
LESSON_DB_ID = 41907128
LESSON2_ENTITY_ID = 4772367
LESSON2_DB_ID = 41907129
LESSONS_PAYLOAD = {
    # The list endpoint sometimes sends only the numeric id at the top level;
    # the real name lives in `courseTitleForModule` of every lesson item.
    "title": str(COURSE_ID),
    "data": [
        {
            "id": LESSON_DB_ID,
            "position": 1,
            "number": 1,
            "title": "Урок 1. Практика",
            "courseTitleForModule": COURSE_TITLE,
            "date": "2025-09-03T19:40:00",
            "visited": True,
            "progress": {"percent": 100},
            "timeStart": "19:40",
            "timeEnd": "21:10",
            "topics": ["VIII–XV вв. Судьбы Русских земель", "Дополнительные материалы"],
            "entityInfo": {"id": LESSON_ENTITY_ID, "title": "Практика"},
            "type": "lesson",
        },
        {
            "id": LESSON2_DB_ID,
            "position": 2,
            "number": 2,
            "title": "Урок 2. Феодальные войны",
            "courseTitleForModule": COURSE_TITLE,
            "date": "2025-09-10T19:40:00",
            "status": "viewed",
            "progress": 100,
            "timeStart": "19:40",
            "timeEnd": "21:10",
            "topics": ["XV–XVI вв. Феодальные войны и грозный царь"],
            "entityInfo": {"id": LESSON2_ENTITY_ID, "title": "Теория"},
            "type": "lesson",
        },
    ],
    "meta": {"current_page": 1, "last_page": 1},
}
LESSON_STRUCTURES = {
    LESSON_DB_ID: {
        "data": {
            "id": LESSON_DB_ID,
            "children": [
                {"type": "curriculum_theory_block", "entityInfo": {"id": 11, "title": "Теория"}},
                {
                    "type": "curriculum_practice_block",
                    "entityInfo": {"id": SUBJECT_ID, "title": SUBJECT_TITLE},
                },
                {"type": "curriculum_test_block", "entityInfo": {"id": 12, "title": "Квиз"}},
            ],
        }
    },
    LESSON2_DB_ID: {
        "data": {
            "id": LESSON2_DB_ID,
            # The practice block of this lesson is nested under a wrapper the
            # canonical schema does not mention — the parser must still find it.
            "children": [
                {"type": "curriculum_theory_block", "entityInfo": {"id": 13, "title": "Лекция"}},
                {
                    "type": "curriculum_module",
                    "content": [
                        {
                            "type": "lesson_practice",
                            "entityInfo": {"id": SUBJECT2_ID, "title": SUBJECT2_TITLE},
                        }
                    ],
                },
            ],
        }
    },
}


def _test(test_id: int, ttype: str, title: str, answers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": test_id,
        "type": ttype,
        "title": title,
        "content": f"<p>Условие задания <b>{test_id}</b></p>",
        "educationTestAnswers": answers,
    }


TESTS_ONLINE = [
    _test(1001, "type1", "Одиночный выбор", [
        {"id": 11, "variants": ["верно"], "isRightAnswer": True, "sort": 1},
        {"id": 12, "variants": ["неверно"], "isRightAnswer": False, "sort": 2},
    ]),
    _test(1002, "type2", "Множественный выбор", [
        {"id": 21, "variants": ["а"], "isRightAnswer": True},
        {"id": 22, "variants": ["б"], "isRightAnswer": False},
        {"id": 23, "variants": ["в"], "isRightAnswer": True},
    ]),
    _test(1003, "type3", "Поле ввода", [
        {"id": 31, "variants": ["42"], "isRightAnswer": True},
    ]),
    _test(1004, "type5", "Сопоставление", [
        {"id": 41, "variants": ["1-a", "1-b"], "isRightAnswer": True},
        {"id": 42, "variants": ["2-a", "2-b"], "isRightAnswer": True},
    ]),
    _test(1005, "type9", "Неподдерживаемый тип", [
        {"id": 51, "variants": ["x"], "isRightAnswer": True},
    ]),
]
TESTS_EXTRA = [
    _test(3001, "type1", "Доп. задание 1", [
        {"id": 71, "variants": ["верно"], "isRightAnswer": True, "sort": 1},
        {"id": 72, "variants": ["неверно"], "isRightAnswer": False, "sort": 2},
    ]),
]
TESTS_OFFLINE = [
    _test(2001, "type1", "Офлайн задание", [
        {"id": 61, "variants": ["да"], "isRightAnswer": True},
        {"id": 62, "variants": ["нет"], "isRightAnswer": False},
    ]),
]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FakeMaximumTest/1.0"

    def log_message(self, *args) -> None:  # silence the test output
        pass

    # ------------------------------------------------------------------ #
    def _send(self, status: int, payload: Any, content_type: str = "application/json") -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        cookie = self.headers.get("Cookie", "")
        if "sessionid=valid" not in cookie:
            self._send(401, {"error": "unauthorized"})
            return False
        return True

    # ------------------------------------------------------------------ #
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        state: FakeServer = self.server.state  # type: ignore[attr-defined]
        state.requests.append(self.path)
        if not self._authorised():
            return
        if state.get_errors:
            # simulate a temporary backend failure
            state.get_errors -= 1
            self._send(500, {"error": "boom"})
            return
        path = self.path.split("?")[0]
        if path == f"/api/v3/student/education-groups/{COURSE_ID}":
            self._send(200, LESSONS_PAYLOAD)
            return
        if path.startswith(f"/api/v3/student/education-groups/{COURSE_ID}/structures/"):
            lesson_id = path.rsplit("/", 1)[-1]
            structure = LESSON_STRUCTURES.get(int(lesson_id)) if lesson_id.isdigit() else None
            if structure is not None:
                self._send(200, structure)
            else:
                self._send(404, {"error": "unknown lesson"})
            return
        for subject_id, title, online in (
            (SUBJECT_ID, SUBJECT_TITLE, [
                {"id": KBS_ONLINE, "title": "Раздел 1"},
                {"id": KBS_EXTRA, "title": "Раздел 2"},
            ]),
            # 502 is listed under "online" on purpose: its tests only answer in
            # "offline" mode, so the mode-fallback path is exercised.
            (SUBJECT2_ID, SUBJECT2_TITLE, [{"id": KBS_OFFLINE, "title": "Раздел 3"}]),
        ):
            if path == f"/api/v1/content/practice/curriculumsubject/{subject_id}":
                self._send(200, {
                    "id": subject_id,
                    "title": title,
                    # no lessonId here: the link lesson → домашка comes from the
                    # lesson structure, exactly like the real backend
                    "tree": {"online": online, "offline": [], "aftertheory": []},
                })
                return
        for subject_id, kbs_id, mode, tests in (
            (SUBJECT_ID, KBS_ONLINE, "online", TESTS_ONLINE),
            (SUBJECT_ID, KBS_EXTRA, "online", TESTS_EXTRA),
            (SUBJECT2_ID, KBS_OFFLINE, "offline", TESTS_OFFLINE),
        ):
            if path == (
                f"/api/v1/content/practice/curriculumsubject/{subject_id}"
                f"/knowledgebase/{kbs_id}/{mode}/tests"
            ):
                self._send(200, [] if state.empty_tests else tests)
                return
        if "/tests" in path:
            # Wrong mode for this knowledge base: behave like the real server.
            self._send(404, {"error": "not found"})
            return
        self._send(404, {"error": "unknown endpoint"})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        state: FakeServer = self.server.state  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        state.posts.append({"path": self.path, "payload": payload})
        state.post_attempts += 1
        if not self._authorised():
            return
        if state.fail_posts:
            # transient failure: the client is expected to retry
            state.fail_posts -= 1
            self._send(500, {"error": "temporary"})
            return
        if state.post_status:
            self._send(state.post_status, {"error": "nope"})
            return
        # Documented response shape: {"result": "true" | false, "percent": 100}
        self._send(200, {"result": "true", "percent": 100})


class FakeServer:
    """Context manager exposing ``base_url``, ``requests`` and ``posts``."""

    def __init__(self, *, port: int = 0) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        self.httpd.state = self  # type: ignore[attr-defined]
        self.requests: list[str] = []
        self.posts: list[dict[str, Any]] = []
        #: switch the tests endpoints to empty arrays
        self.empty_tests = False
        #: number of GET requests that answer 500 before succeeding
        self.get_errors = 0
        #: number of POST requests that answer 500 before succeeding
        self.fail_posts = 0
        #: fixed status to answer every POST with (4xx => must not be retried)
        self.post_status = 0
        self.post_attempts = 0
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> FakeServer:
        self.thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
