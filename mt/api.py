"""Thin HTTP client for education.maximumtest.ru.

* Thread safe: a shared requests session is guarded by a re-entrant lock.
* Transparent retries for idempotent GETs (network hiccups, 5xx, 429).
* Friendly, translatable errors (:class:`AuthError`, :class:`NetworkError`).
* Cookie jar persisted with ``0600`` permissions instead of the default umask.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config


class ApiError(RuntimeError):
    """Base class for API failures with a user-facing message."""

    title = "Ошибка запроса"

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AuthError(ApiError):
    title = "Нужна авторизация"


class AccessDeniedError(ApiError):
    title = "Доступ запрещён"


class NetworkError(ApiError):
    title = "Нет соединения"


class RateLimitError(ApiError):
    title = "Слишком много запросов"


def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=config.REQUEST_RETRIES,
        connect=config.REQUEST_RETRIES,
        read=config.REQUEST_RETRIES,
        backoff_factor=0.6,
        status_forcelist=(500, 502, 503, 504, 429),
        allowed_methods=frozenset({"GET", "HEAD"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) MaximumTestDesktop/{config.APP_VERSION}"
        ),
    })
    return session


class Api:
    """Single shared session guarded by a re-entrant lock.

    Requests are never issued in parallel, so serialising them is free and
    guarantees the cookie jar is always consistent between threads.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._session = _build_session()
        self._cookies: dict[str, str] = {}
        self._load_cookies()

    # ------------------------------------------------------------------ #
    # cookies
    # ------------------------------------------------------------------ #
    @property
    def cookie_header(self) -> str:
        return "; ".join(f"{key}={value}" for key, value in self._cookies.items())

    def _apply_cookies(self) -> None:
        with self._lock:
            self._session.cookies.clear()

    def _load_cookies(self) -> None:
        if not config.COOKIES_PATH.exists():
            return
        try:
            # Support both our own JSON dict and a plain "a=b; c=d" string.
            raw = config.COOKIES_PATH.read_text(encoding="utf-8").strip()
            data = json.loads(raw) if raw.startswith("{") else raw
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self._cookies = {str(k): str(v) for k, v in data.items() if k and v is not None}
        elif isinstance(data, str):
            self._cookies = self.parse_cookie_string(data)
        if self._cookies:
            self._apply_cookies()

    def save_cookies(self) -> None:
        config.ensure_dirs()
        path = config.COOKIES_PATH
        payload = json.dumps(self._cookies, ensure_ascii=False)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, path)
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)

    @staticmethod
    def parse_cookie_string(raw: str) -> dict[str, str]:
        raw = (raw or "").strip()
        if raw.lower().startswith("cookie:"):
            raw = raw.split(":", 1)[1]
        cookies: dict[str, str] = {}
        for part in raw.replace("\n", ";").split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key, value = key.strip(), value.strip().strip('"')
            if key:
                cookies[key] = value
        return cookies

    def set_cookies_from_string(self, raw: str) -> int:
        cookies = self.parse_cookie_string(raw)
        if not cookies:
            raise ApiError("Не удалось разобрать cookies: ожидается «name=value; …»")
        self.set_cookies(cookies)
        return len(cookies)

    def set_cookies(self, cookies: dict[str, str]) -> None:
        with self._lock:
            self._cookies = {str(k): str(v) for k, v in cookies.items()}
            self._apply_cookies()
            self.save_cookies()

    def clear_cookies(self) -> None:
        self._cookies = {}
        with self._lock:
            self._session.cookies.clear()
        with contextlib.suppress(FileNotFoundError):
            config.COOKIES_PATH.unlink()

    def has_cookies(self) -> bool:
        return bool(self._cookies)

    def cookie_summary(self) -> str:
        if not self._cookies:
            return "cookies не заданы"
        names = list(self._cookies)
        return f"{len(names)} cookies: " + ", ".join(names[:3]) + ("…" if len(names) > 3 else "")

    # ------------------------------------------------------------------ #
    # HTTP
    # ------------------------------------------------------------------ #
    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        attempts: int = 1,
        retry_delay: float = 0.0,
    ) -> Any:
        """Perform a request, optionally retrying transient failures.

        ``attempts``/``retry_delay`` are used by POST /tests: the reference
        specifies up to 3 attempts with a 400 ms pause.  Authorization errors
        are never retried; 4xx client errors are retried only for 429.
        """
        last_error: ApiError | None = None
        for attempt in range(1, max(1, attempts) + 1):
            try:
                return self._request_once(method, path, payload, params)
            except (AuthError, ApiError) as exc:
                last_error = exc
                status = getattr(exc, "status", None) or 0
                transient = status == 429 or status >= 500 or isinstance(exc, NetworkError)
                if isinstance(exc, AuthError) or not transient or attempt >= attempts:
                    raise
                with contextlib.suppress(Exception):
                    time.sleep(retry_delay * attempt)
        raise last_error or ApiError("Запрос не выполнен")

    def _request_once(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = config.BASE_URL + path
        origin = urlsplit(config.BASE_URL)
        context = f"{method} {urlsplit(url).path}"
        try:
            with self._lock:
                for hop in range(6):
                    # No domainless cookie jar: redirects can otherwise combine
                    # an imported token with a rotated token of the same name.
                    self._session.cookies.clear()
                    response = self._session.request(
                        method, url, json=payload, params=params,
                        headers={"Cookie": self.cookie_header} if self._cookies else {},
                        timeout=config.REQUEST_TIMEOUT, allow_redirects=False,
                    )
                    is_redirect = response.status_code in (301, 302, 303, 307, 308)
                    is_json = "json" in response.headers.get("Content-Type", "").lower()
                    if response.status_code < 400 and (is_redirect or is_json):
                        self._absorb_cookies(response)
                    if response.status_code not in (301, 302, 303, 307, 308):
                        break
                    target = urljoin(url, response.headers.get("Location", ""))
                    parsed = urlsplit(target)
                    if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
                        raise AuthError(f"{context}: HTTP {response.status_code}, "
                                        "редирект на другой сайт; cookies не переданы")
                    if method != "GET" or not response.headers.get("Location"):
                        raise ApiError(f"{context}: HTTP {response.status_code}, "
                                       "неожиданный редирект; запрос не повторён")
                    if hop == 5:
                        raise ApiError(f"{context}: слишком много перенаправлений")
                    response.close()
                    url, params = target, None
                # Clear Set-Cookie duplicates; the canonical imported/rotated
                # header is constructed afresh for each request.
                self._session.cookies.clear()
        except requests.Timeout as exc:
            raise NetworkError(f"{context}: сервер не ответил вовремя") from exc
        except requests.RequestException as exc:
            raise NetworkError(f"{context}: {exc.__class__.__name__}") from exc

        status = response.status_code
        if status == 401:
            raise AuthError(
                f"{context}: HTTP 401 — сервер не принял cookies. "
                "Это ответ сервера, а не проверка срока сессии приложением.",
                status=status,
            )
        if status == 403:
            raise AccessDeniedError(
                f"{context}: HTTP 403 — сервер запретил доступ к этому ресурсу. "
                "Это не обязательно истёкшая сессия.", status=status,
            )
        if status == 429:
            raise RateLimitError(f"{context}: HTTP 429 — сервер ограничил частоту запросов.", status=status)
        if status >= 400:
            raise ApiError(f"{context}: HTTP {status}", status=status)

        content_type = response.headers.get("Content-Type", "")
        if "json" not in content_type.lower():
            # Usually an HTML login page served with 200 OK.
            if "text/html" in content_type.lower():
                raise ApiError(f"{context}: HTTP {status}, HTML вместо JSON. "
                               "Это может быть страница входа или защитный фильтр; "
                               "cookies не изменены.", status=status)
            raise ApiError(f"Неожиданный ответ сервера ({content_type or 'без типа'})")
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError(f"{context}: HTTP {status}, некорректный JSON") from exc

    def _absorb_cookies(self, response: requests.Response) -> None:
        """Persist cookies the server rotates during requests."""
        changed = False
        for cookie in getattr(response, "cookies", []) or []:
            if not cookie.name or not cookie.value:
                continue
            if self._cookies.get(cookie.name) != cookie.value:
                self._cookies[cookie.name] = cookie.value
                changed = True
        if changed:
            with contextlib.suppress(OSError):
                self.save_cookies()

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    # ------------------------------------------------------------------ #
    # endpoints
    # ------------------------------------------------------------------ #
    def get_curriculum_subject(self, subject_id: int) -> dict[str, Any]:
        data = self._request(
            "GET", config.ENDPOINTS["curriculum_subject"].format(subject_id=subject_id)
        )
        if not isinstance(data, dict):
            raise ApiError("Неожиданный формат ответа для предмета")
        return data

    def get_kbs_tests(self, subject_id: int, kbs_id: int, mode: str) -> Any:
        return self._request(
            "GET",
            config.ENDPOINTS["kbs_tests"].format(
                subject_id=subject_id, kbs_id=kbs_id, mode=mode
            ),
        )

    def post_answer(
        self,
        subject_id: int,
        lesson_id: int | str,
        test_id: int,
        answer: list,
        spent_time: int,
    ) -> Any:
        """Send an answer.

        Body and path follow the reference exactly; ``lessonId`` is a string.
        Transient failures are retried up to ``POST_ATTEMPTS`` times with a
        400 ms pause (never on 401/403/4xx).
        """
        return self._request(
            "POST",
            config.ENDPOINTS["post_answer"].format(subject_id=subject_id, test_id=test_id),
            payload={
                "lessonId": str(lesson_id),
                "attaches": [],
                "answer": answer,
                "spentTime": int(spent_time),
            },
            attempts=config.POST_ATTEMPTS,
            retry_delay=config.POST_RETRY_DELAY,
        )

    # ------------------------------------------------------------------ #
    # v3: courses → lessons → practice blocks
    # ------------------------------------------------------------------ #
    def get_education_group(
        self,
        course_id: int,
        last_known_id: int | None = None,
        per_page: int | None = None,
    ) -> Any:
        """One page of a course with its lessons.

        The endpoint paginates by the id of the last lesson, not by a page
        number: ``?perPage=50&lastKnownId=<id>`` returns the following slice.
        """
        params: dict[str, Any] = {"perPage": per_page or config.COURSE_PAGE_SIZE}
        if last_known_id is not None:
            params["lastKnownId"] = int(last_known_id)
        return self._request(
            "GET",
            config.ENDPOINTS["education_group"].format(course_id=course_id),
            params=params,
        )

    def get_lesson_structure(self, course_id: int, lesson_id: int) -> Any:
        """Structure of a lesson; practice blocks carry the subject ids."""
        return self._request(
            "GET",
            config.ENDPOINTS["lesson_structure"].format(
                course_id=course_id, lesson_id=lesson_id
            ),
        )

