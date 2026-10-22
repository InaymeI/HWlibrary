from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QDialogButtonBox, QMessageBox, QApplication,
)

class CookieDialog(QDialog):
    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api
        self.setWindowTitle("Import cookies")
        self.resize(640, 320)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "1) Log in on https://education.maximumtest.ru in your browser.\n"
            "2) DevTools → Network → click any request → Headers → copy the Cookie value.\n"
            "3) Paste it below, or use the Firefox import button."
        ))
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("sessionid=...; csrftoken=...; ...")
        lay.addWidget(self.text)

        h = QHBoxLayout()
        b_paste = QPushButton("Paste from clipboard")
        b_paste.clicked.connect(lambda: self.text.setPlainText(QApplication.clipboard().text()))
        h.addWidget(b_paste)
        b_ff = QPushButton("Import from Firefox")
        b_ff.clicked.connect(self._import_firefox)
        h.addWidget(b_ff)
        lay.addLayout(h)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _save(self):
        raw = self.text.toPlainText().strip()
        if not raw:
            self.reject()
            return
        self.api.set_cookies_from_string(raw)
        self.accept()

    def _import_firefox(self):
        import glob, os, sqlite3
        candidates = glob.glob(os.path.expanduser("~/.mozilla/firefox/*/cookies.sqlite"))
        if not candidates:
            QMessageBox.warning(self, "Firefox", "No Firefox profile found")
            return
        path = max(candidates, key=os.path.getmtime)
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
            rows = conn.execute(
                "SELECT name, value FROM moz_cookies WHERE host LIKE '%maximumtest.ru%'"
            ).fetchall()
            conn.close()
        except Exception as e:
            QMessageBox.critical(self, "Firefox", str(e))
            return
        if not rows:
            QMessageBox.warning(self, "Firefox", "No maximumtest.ru cookies found")
            return
        self.text.setPlainText("; ".join(f"{n}={v}" for n, v in rows))
