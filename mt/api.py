import json
import threading
import requests

from .config import BASE_URL, ENDPOINTS, COOKIES_PATH

class Api:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) MaximumTestDesktop/0.1",
        })
        self._lock = threading.Lock()
        self._load_cookies()

    # ---------- cookies ----------
    def _load_cookies(self):
        if COOKIES_PATH.exists():
            try:
                jar = requests.utils.cookiejar_from_dict(json.loads(COOKIES_PATH.read_text()))
                self.session.cookies.update(jar)
            except Exception as e:
                print("cookies load failed:", e)

    def save_cookies(self):
        with self._lock:
            COOKIES_PATH.write_text(json.dumps(
                requests.utils.dict_from_cookiejar(self.session.cookies)
            ))

    def set_cookies_from_string(self, raw: str):
        raw = raw.strip()
        if raw.lower().startswith("cookie:"):
            raw = raw.split(":", 1)[1]
        for part in raw.split(";"):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            self.session.cookies.set(k.strip(), v.strip(), domain=".maximumtest.ru")
        self.save_cookies()

    def has_cookies(self) -> bool:
        return len(self.session.cookies) > 0

    # ---------- HTTP ----------
    def _get(self, path: str):
        r = self.session.get(BASE_URL + path, timeout=25)
        if r.status_code in (401, 403):
            raise PermissionError(f"auth required (HTTP {r.status_code})")
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict):
        with self._lock:
            r = self.session.post(BASE_URL + path, json=payload, timeout=25)
            r.raise_for_status()
            return r.json()

    # ---------- endpoints ----------
    def get_curriculum_subject(self, subject_id: int):
        return self._get(ENDPOINTS["curriculum_subject"].format(subject_id=subject_id))

    def get_kbs_tests(self, subject_id: int, kbs_id: int, mode: str):
        return self._get(ENDPOINTS["kbs_tests"].format(
            subject_id=subject_id, kbs_id=kbs_id, mode=mode,
        ))

    def post_answer(self, subject_id: int, lesson_id: int | str, test_id: int,
                    answer: list, spent_time: int):
        return self._post(ENDPOINTS["post_answer"].format(
            subject_id=subject_id, test_id=test_id,
        ), {
            "lessonId": str(lesson_id),
            "attaches": [],
            "answer": answer,
            "spentTime": spent_time,
        })

    def try_get_courses(self):
        try:
            return self._get(ENDPOINTS["courses"])
        except Exception:
            return None
