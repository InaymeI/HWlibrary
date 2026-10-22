"""Dialogs: cookie import, JSON viewer, send confirmation, result, help."""

from __future__ import annotations

import contextlib
import glob
import json
import os
import sqlite3
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import config, icons
from ..models import TestDetails
from ..widgets import label, primary_button, secondary_button

FIREFOX_GLOB = "~/.mozilla/firefox/*/cookies.sqlite"
CHROMIUM_PATHS = (
    "~/.config/google-chrome/*/Cookies",
    "~/.config/chromium/*/Cookies",
    "~/.config/BraveSoftware/Brave-Browser/*/Cookies",
    "~/.config/vivaldi/*/Cookies",
    "~/.config/microsoft-edge/*/Cookies",
    "~/.var/app/com.google.Chrome/config/google-chrome/*/Cookies",
    "~/.var/app/org.mozilla.firefox/.mozilla/firefox/*/cookies.sqlite",
)


# --------------------------------------------------------------------------- #
def read_firefox_cookies() -> tuple[list[tuple[str, str]], str | None]:
    """Return ``(cookies, error)`` from the most recently used Firefox profile."""
    candidates = glob.glob(os.path.expanduser(FIREFOX_GLOB))
    candidates += glob.glob(os.path.expanduser("~/.var/app/org.mozilla.firefox/.mozilla/firefox/*/cookies.sqlite"))
    if not candidates:
        return [], "Профиль Firefox не найден"
    path = max(candidates, key=os.path.getmtime)
    try:
        # copy first: Firefox may hold an exclusive lock on the database
        import shutil
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as handle:
            temp_path = handle.name
        shutil.copy2(path, temp_path)
        try:
            conn = sqlite3.connect(temp_path)
            rows = conn.execute(
                "SELECT name, value FROM moz_cookies"
                " WHERE host LIKE '%maximumtest%' OR host LIKE '%maximumtest.ru%'"
                " ORDER BY name"
            ).fetchall()
            conn.close()
        finally:
            with contextlib.suppress(OSError):
                os.unlink(temp_path)
    except Exception as exc:  # noqa: BLE001 - surface any sqlite error to the dialog
        return [], f"Не удалось прочитать cookies Firefox: {exc}"
    if not rows:
        return [], "В Firefox нет cookies maximumtest.ru — войдите на сайт в браузере"
    return [(name, value) for name, value in rows], None


def read_chromium_cookies() -> tuple[list[tuple[str, str]], str | None]:
    """Best effort import from Chromium based browsers (encrypted values are skipped)."""
    candidates: list[str] = []
    for pattern in CHROMIUM_PATHS:
        candidates += glob.glob(os.path.expanduser(pattern))
    if not candidates:
        return [], "Профили Chrome / Chromium / Edge не найдены"
    path = max(candidates, key=os.path.getmtime)
    temp_path = None
    try:
        import shutil
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as handle:
            temp_path = handle.name
        shutil.copy2(path, temp_path)
        conn = sqlite3.connect(temp_path)
        rows = conn.execute(
            "SELECT name, value, encrypted_value FROM cookies"
            " WHERE host_key LIKE '%maximumtest%' ORDER BY name"
        ).fetchall()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        return [], f"Не удалось прочитать cookies: {exc}"
    finally:
        if temp_path:
            with contextlib.suppress(OSError):
                os.unlink(temp_path)
    cookies = [(name, value) for name, value, _enc in rows if value]
    if not cookies:
        return [], (
            "Chrome хранит значения в зашифрованном виде.\n"
            "Скопируйте Cookie из DevTools → Network или используйте Firefox."
        )
    return cookies, None


# --------------------------------------------------------------------------- #
class CookieDialog(QDialog):
    def __init__(self, api, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self.setWindowTitle("Cookies сессии")
        self.setModal(True)
        self.resize(720, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        title = label("Доступ к заданиям", "h2")
        layout.addWidget(title)
        steps = QLabel(
            "1. Войдите на <b>education.maximumtest.ru</b> в браузере.<br>"
            "2. Откройте DevTools → <b>Network</b> → выберите любой запрос → <b>Headers</b>.<br>"
            "3. Скопируйте значение заголовка <b>Cookie</b> и вставьте его ниже."
        )
        steps.setTextFormat(Qt.TextFormat.RichText)
        steps.setWordWrap(True)
        steps.setProperty("role", "secondary")
        layout.addWidget(steps)

        row = QHBoxLayout()
        row.setSpacing(8)
        paste = secondary_button("Вставить из буфера", "copy")
        paste.clicked.connect(self._paste)
        row.addWidget(paste)
        firefox = secondary_button("Импорт из Firefox", "cookie")
        firefox.clicked.connect(self._import_firefox)
        row.addWidget(firefox)
        chrome = secondary_button("Chrome", "cookie")
        chrome.clicked.connect(self._import_chromium)
        row.addWidget(chrome)
        row.addStretch(1)
        layout.addLayout(row)

        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("sessionid=…; csrftoken=…; …")
        self.text.setMinimumHeight(140)
        layout.addWidget(self.text, 1)

        self.status = label("", "muted", wrap=True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton("Сохранить", QDialogButtonBox.ButtonRole.AcceptRole)
        self.save_button.setProperty("variant", "primary")
        clear = buttons.addButton("Удалить cookies", QDialogButtonBox.ButtonRole.DestructiveRole)
        cancel = buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        clear.clicked.connect(self._clear)
        layout.addWidget(buttons)
        self._cancel_button = cancel

        if api.has_cookies():
            self.status.setText(f"Сейчас сохранено: {api.cookie_summary()}")

    # ------------------------------------------------------------------ #
    def _paste(self) -> None:
        text = QGuiApplication.clipboard().text().strip()
        if text:
            self.text.setPlainText(text)
            self.status.setText("Вставлено из буфера обмена")
        else:
            self.status.setText("Буфер обмена пуст")

    def _import_firefox(self) -> None:
        cookies, error = read_firefox_cookies()
        if error:
            self.status.setText(error)
            return
        self.text.setPlainText("; ".join(f"{name}={value}" for name, value in cookies))
        self.status.setText(f"Импортировано {len(cookies)} cookies из Firefox")

    def _import_chromium(self) -> None:
        cookies, error = read_chromium_cookies()
        if error:
            self.status.setText(error)
            return
        self.text.setPlainText("; ".join(f"{name}={value}" for name, value in cookies))
        self.status.setText(f"Импортировано {len(cookies)} cookies")

    def _save(self) -> None:
        raw = self.text.toPlainText().strip()
        if not raw:
            self.status.setText("Вставьте cookies или закройте окно")
            return
        try:
            count = self.api.set_cookies_from_string(raw)
        except Exception as exc:  # noqa: BLE001
            self.status.setText(str(exc))
            return
        self.status.setText(f"Сохранено {count} cookies")
        self.accept()

    def _clear(self) -> None:
        self.api.clear_cookies()
        self.text.clear()
        self.status.setText("Cookies удалены")
        self.accept()


# --------------------------------------------------------------------------- #
class JsonDialog(QDialog):
    def __init__(self, title: str, payload: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"JSON — {title}")
        self.resize(820, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        layout.addWidget(label(title, "h2"))
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))
        layout.addWidget(text, 1)

        row = QHBoxLayout()
        copy = secondary_button("Копировать JSON", "copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(text.toPlainText()))
        row.addWidget(copy)
        row.addStretch(1)
        close = primary_button("Закрыть")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)


# --------------------------------------------------------------------------- #
class SendDialog(QDialog):
    """Explicit confirmation before a POST that changes the user's progress."""

    def __init__(
        self,
        details: TestDetails,
        answer: list,
        lesson_id: int,
        spent: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Отправить ответ")
        self.resize(560, 380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        layout.addWidget(label("Отправка ответа", "h2"))
        layout.addWidget(label(
            "Действие изменит результат задания в личном кабинете. Отменить его из приложения нельзя.",
            "muted", wrap=True,
        ))

        card = QFrame()
        card.setObjectName("cardFlat")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(6)
        card_layout.addWidget(label(details.title, "h3", wrap=True))
        card_layout.addWidget(label(
            f"Тест #{details.id} · lessonId {lesson_id} · время {spent} с", "muted"
        ))
        answer_text = ", ".join(str(item) for item in answer) or "—"
        card_layout.addWidget(label(f"Ответ: {answer_text}", wrap=True, selectable=True))
        layout.addWidget(card)

        self.checkbox = QCheckBox("Я понимаю последствия и хочу отправить ответ")
        layout.addWidget(self.checkbox)

        buttons = QDialogButtonBox()
        send = buttons.addButton("Отправить", QDialogButtonBox.ButtonRole.AcceptRole)
        send.setProperty("variant", "danger")
        send.setEnabled(False)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.checkbox.toggled.connect(send.setEnabled)
        layout.addWidget(buttons)


class ResultDialog(QDialog):
    def __init__(self, payload: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Результат отправки")
        self.resize(640, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        verdict = _verdict(payload)
        header = QHBoxLayout()
        header.setSpacing(12)
        icon_label = QLabel()
        icon_name, color, variant, text = verdict
        icon_label.setPixmap(icons.pixmap(icon_name, color, 28))
        header.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
        text_box = QVBoxLayout()
        text_box.setSpacing(4)
        text_box.addWidget(label(text, "h2", wrap=True))
        text_box.addWidget(_badge_for(variant))
        header.addLayout(text_box, 1)
        layout.addLayout(header)

        body = QPlainTextEdit()
        body.setReadOnly(True)
        body.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))
        layout.addWidget(body, 1)

        row = QHBoxLayout()
        copy = secondary_button("Копировать ответ сервера", "copy")
        copy.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(body.toPlainText())
        )
        row.addWidget(copy)
        row.addStretch(1)
        close = primary_button("Готово")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)


def _badge_for(variant: str) -> QLabel:
    from ..widgets import Badge

    return Badge(
        {"success": "ответ принят", "danger": "ответ отклонён", "muted": "ответ отправлен"}.get(
            variant, "ответ отправлен"
        ),
        variant if variant in ("success", "danger") else "muted",
    )


def _as_bool(value: Any) -> bool | None:
    """Interpret ``true`` / ``"true"`` / ``1`` / ``"1"`` the way the API mixes them."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "ok", "yes", "success"):
            return True
        if text in ("false", "0", "no", "fail", "error"):
            return False
    return None


def parse_result(payload: Any) -> tuple[bool | None, float | None]:
    """Return ``(ok, percent)`` from an answer response.

    Documented shape: ``{"result": "true", "percent": 100}``.  The old client
    also accepts a handful of boolean flags, which are handled as a fallback.
    """
    if not isinstance(payload, dict):
        return None, None
    ok = _as_bool(payload.get("result"))
    if ok is None:
        for key in ("isRight", "isCorrect", "right", "correct", "isRightAnswer", "success"):
            if key in payload:
                ok = _as_bool(payload[key])
                if ok is not None:
                    break
    percent = payload.get("percent")
    percent_value: float | None = None
    if isinstance(percent, (int, float, str)):
        try:
            percent_value = float(str(percent).replace("%", "").strip())
        except ValueError:
            percent_value = None
    if ok is None and percent_value is not None:
        ok = percent_value >= 100
    return ok, percent_value


def _verdict(payload: Any) -> tuple[str, str, str, str]:
    """Map a loose API response to (icon, colour, variant, headline)."""
    success_color, danger_color = "#12795a", "#c53b3b"
    if not isinstance(payload, dict):
        return "check", success_color, "success", "Ответ отправлен"

    ok, percent = parse_result(payload)
    suffix = ""
    if percent is not None:
        suffix = f" · {percent:g}%"

    if ok is True:
        return "check", success_color, "success", f"Ответ принят: верно{suffix}"
    if ok is False:
        return "close", danger_color, "danger", f"Ответ принят: неверно{suffix}"
    if payload.get("error") or payload.get("errors"):
        return "alert", danger_color, "danger", "Сервер сообщил об ошибке"
    return "check", success_color, "success", f"Ответ отправлен{suffix}"


# --------------------------------------------------------------------------- #
HELP_HTML = """
<h2>Как получить cookies</h2>
<ol>
  <li>Откройте <b>education.maximumtest.ru</b> и войдите в аккаунт.</li>
  <li>Нажмите <b>F12</b> → вкладка <b>Network</b> (Сеть).</li>
  <li>Обновите страницу и выберите любой запрос к <code>/api/</code>.</li>
  <li>В разделе <b>Request Headers</b> найдите <b>Cookie</b> и скопируйте всё значение.</li>
  <li>Вставьте его в окно «Cookies сессии» и сохраните.</li>
</ol>
<p>Данные хранятся только на этом компьютере в файле
<code>{cookies_path}</code> с правами доступа 600.</p>
<p>Cookies можно импортировать напрямую из Firefox. Chrome хранит значения
в зашифрованном виде, поэтому для него надёжнее скопировать заголовок вручную.</p>
<h3>Горячие клавиши</h3>
<ul>
  <li><b>F5</b> — синхронизировать выбранный предмет</li>
  <li><b>Ctrl+R</b> — обновить список</li>
  <li><b>Ctrl+N</b> — добавить предмет</li>
  <li><b>Ctrl+F</b> — поиск по всем тестам</li>
  <li><b>Ctrl+L</b> — журнал синхронизации</li>
  <li><b>Ctrl+Q</b> — выход</li>
</ul>
"""


class HelpDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Справка")
        self.resize(640, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        from PyQt6.QtWidgets import QTextBrowser

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setFrameShape(QFrame.Shape.NoFrame)
        browser.setHtml(HELP_HTML.format(cookies_path=config.COOKIES_PATH))
        layout.addWidget(browser, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = primary_button("Закрыть")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)


def about_text() -> str:
    return (
        f"<h3>{config.APP_TITLE} {config.APP_VERSION}</h3>"
        "<p>Просмотр заданий MaximumTest из локального кэша.</p>"
        "<p style='color:#8b93a1'>PyQt6 · SQLite · без телеметрии.<br>"
        f"Данные: {config.DATA_DIR}</p>"
    )


def message(parent: QWidget, title: str, text: str, *, error: bool = False) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Critical if error else QMessageBox.Icon.Information)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()
