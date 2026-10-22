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

SUBJECT_ID = 777
KBS_ONLINE = 501
KBS_OFFLINE = 502


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
        if path == f"/api/v1/content/practice/curriculumsubject/{SUBJECT_ID}":
            self._send(200, {
                "id": SUBJECT_ID,
                "title": "Тестовый предмет",
                "tree": {
                    "online": [
                        {"id": KBS_ONLINE, "title": "Раздел онлайн", "lessonId": 9001},
                        {"id": KBS_OFFLINE, "title": "Раздел офлайн", "lessonId": 9002},
                    ],
                    "offline": [],
                    "aftertheory": [],
                },
            })
            return
        for kbs_id, mode, tests in (
            (KBS_ONLINE, "online", TESTS_ONLINE),
            (KBS_OFFLINE, "offline", TESTS_OFFLINE),
        ):
            if path == (
                f"/api/v1/content/practice/curriculumsubject/{SUBJECT_ID}"
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
