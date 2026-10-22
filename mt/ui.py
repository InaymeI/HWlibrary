import html
import json
import random
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QStackedWidget, QLabel, QPushButton,
    QTextBrowser, QPlainTextEdit, QMessageBox, QProgressBar, QStatusBar,
    QToolBar, QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QTabWidget, QLineEdit, QDockWidget, QHeaderView, QInputDialog,
    QComboBox,
)

from . import db
from .answers import build_answer, extract_right_answers
from .api import Api
from .config import SUPPORTED_TYPES, is_supported
from .cookie_dialog import CookieDialog
from .sync import SyncThread


class SubjectView(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        self.title = QLabel(); self.title.setStyleSheet("font-size:18px;font-weight:600;")
        lay.addWidget(self.title)
        self.stats = QLabel(); lay.addWidget(self.stats)
        lay.addWidget(QLabel("Knowledge bases:"))
        self.kbs_list = QListWidget()
        self.kbs_list.itemDoubleClicked.connect(self._open_kbs)
        lay.addWidget(self.kbs_list, 1)

    def show_subject(self, row):
        self.subject = row
        sid = row["id"]
        self.title.setText(row.get("title") or f"Subject #{sid}")
        st = db.stats_for_subject(sid)
        self.stats.setText(
            f"Тестов: {st['total']}  •  Поддерживаемых (type1-5): {st['supported']}"
        )
        self.kbs_list.clear()
        for k in db.list_kbs(sid):
            n = len(db.list_tests(k["id"]))
            item = QListWidgetItem(f"KBS #{k['id']}  ({k['mode']})  —  {n} tests")
            item.setData(Qt.ItemDataRole.UserRole, k["id"])
            self.kbs_list.addItem(item)

    def _open_kbs(self, item):
        self.main.select_kbs(item.data(Qt.ItemDataRole.UserRole))


class KbsView(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        self.title = QLabel(); self.title.setStyleSheet("font-size:16px;font-weight:600;")
        lay.addWidget(self.title)
        self.meta = QLabel(); lay.addWidget(self.meta)
        lay.addWidget(QLabel("Tests (double-click to open):"))
        self.tests = QListWidget()
        self.tests.itemDoubleClicked.connect(
            lambda it: self.main.select_test(it.data(Qt.ItemDataRole.UserRole))
        )
        lay.addWidget(self.tests, 1)

    def show_kbs(self, kbs_id: int):
        self.kbs_id = kbs_id
        rows = [k for k in db.list_kbs(self._subject_id_of(kbs_id)) if k["id"] == kbs_id] \
               if self._subject_id_of(kbs_id) else []
        title = f"KBS #{kbs_id}"
        if rows:
            title += f"  ({rows[0]['mode']})"
        self.title.setText(title)
        tests = db.list_tests(kbs_id)
        supported = sum(1 for t in tests if is_supported(t["type"]))
        self.meta.setText(f"Тестов: {len(tests)}  •  Поддерживаемых: {supported}")
        self.tests.clear()
        for t in tests:
            p = json.loads(t["payload_json"])
            tt = p.get("title") or p.get("name") or ""
            mark = "✓" if is_supported(t["type"]) else "·"
            item = QListWidgetItem(f"{mark} [{t['type']}]  #{t['id']}  {tt}")
            item.setData(Qt.ItemDataRole.UserRole, t["id"])
            if not is_supported(t["type"]):
                item.setForeground(QColor("#888"))
            self.tests.addItem(item)

    def _subject_id_of(self, kbs_id: int):
        for s in db.list_subjects():
            for k in db.list_kbs(s["id"]):
                if k["id"] == kbs_id:
                    return s["id"]
        return None


class TestView(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        self.title = QLabel(); self.title.setStyleSheet("font-size:16px;font-weight:600;")
        lay.addWidget(self.title)
        self.meta = QLabel(); lay.addWidget(self.meta)

        lay.addWidget(QLabel("Условие:"))
        self.question = QTextBrowser(); self.question.setMaximumHeight(180)
        lay.addWidget(self.question)

        lay.addWidget(QLabel("Варианты ответов:"))
        self.answers = QTextBrowser()
        lay.addWidget(self.answers, 1)

        h = QHBoxLayout()
        self.lesson_edit = QLineEdit()
        self.lesson_edit.setPlaceholderText("lessonId (нужен для отправки, берётся из URL)")
        h.addWidget(self.lesson_edit, 1)
        self.btn_send = QPushButton("Отправить ответ")
        self.btn_send.clicked.connect(self._send)
        h.addWidget(self.btn_send)
        lay.addLayout(h)

    def show_test(self, tid: int):
        t = db.get_test(tid)
        if not t:
            return
        self.test_row = t
        p = json.loads(t["payload_json"])
        title = p.get("title") or p.get("name") or f"Test #{tid}"
        self.title.setText(title)
        self.meta.setText(
            f"ID {tid}  •  type {t['type']}  •  subject {t['subject_id']}  •  kbs {t['kbs_id']}  •  "
            f"cached {datetime.fromtimestamp(t['fetched_at']):%Y-%m-%d %H:%M}"
        )
        q = p.get("content") or p.get("question") or p.get("text") or ""
        self.question.setHtml(f"<div>{q}</div>")

        # find a lessonId hint in payload
        for key in ("lessonId", "lesson_id", "lesson"):
            if p.get(key):
                self.lesson_edit.setText(str(p[key]))
                break

        right = extract_right_answers(p)
        right_ids = {str(a.get("id")) for a in right}
        rows = []
        for a in (p.get("educationTestAnswers") or []):
            aid = str(a.get("id"))
            is_right = a.get("isRightAnswer") is True
            variant_text = " / ".join(str(v) for v in (a.get("variants") or []))
            mark = "✓" if is_right else "○"
            color = "#36ad6a" if is_right else "#aaa"
            rows.append(
                f'<li style="color:{color}"><b>{mark}</b> '
                f'[{html.escape(aid)}] {html.escape(variant_text)}</li>'
            )
        self.answers.setHtml("<ul>" + "".join(rows) + "</ul>")

        correct = build_answer(p)
        self.btn_send.setToolTip(f"Автоответ: {correct}")
        self.btn_send.setEnabled(is_supported(t["type"]))

    def _send(self):
        if not getattr(self, "test_row", None):
            return
        lesson_raw = self.lesson_edit.text().strip()
        try:
            lesson_id = int(lesson_raw)
        except ValueError:
            QMessageBox.warning(self, "lessonId", "Введите числовой lessonId")
            return
        p = json.loads(self.test_row["payload_json"])
        answer = build_answer(p)
        if QMessageBox.question(
            self, "Confirm",
            f"Отправить ответ на тест #{self.test_row['id']}?\n\nОтвет: {answer}",
        ) != QMessageBox.StandardButton.Yes:
            return
        spent = 45 + random.randint(0, 45)
        try:
            res = self.main.api.post_answer(
                self.test_row["subject_id"], lesson_id,
                self.test_row["id"], answer, spent,
            )
            QMessageBox.information(self, "Result", json.dumps(res, ensure_ascii=False, indent=2)[:2000])
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))


class AllTestsView(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        h = QHBoxLayout()
        h.addWidget(QLabel("Filter:"))
        self.filter = QLineEdit(); self.filter.setPlaceholderText("id / title / type / subject")
        self.filter.textChanged.connect(self._reload)
        h.addWidget(self.filter, 1)
        h.addWidget(QLabel("Type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["all", "supported", "type1", "type2", "type3", "type4", "type5", "other"])
        self.type_combo.currentIndexChanged.connect(self._reload)
        h.addWidget(self.type_combo)
        lay.addLayout(h)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Subject", "KBS", "Type", "ID", "Title", ""]
        )
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(self._open_row)
        lay.addWidget(self.table, 1)

        self._rows = []

    def refresh(self):
        self._reload()

    def _reload(self):
        flt = self.filter.text().strip().lower()
        mode = self.type_combo.currentText()

        subj_titles = {s["id"]: s.get("title") or f"#{s['id']}" for s in db.list_subjects()}
        rows = []
        for t in db.all_tests():
            p = json.loads(t["payload_json"])
            title = p.get("title") or p.get("name") or ""
            if mode == "supported" and not is_supported(t["type"]):
                continue
            if mode == "other" and is_supported(t["type"]):
                continue
            if mode.startswith("type") and t["type"] != mode:
                continue
            hay = f"{t['id']} {t['type']} {title} {t['subject_id']} {t['kbs_id']}".lower()
            if flt and flt not in hay:
                continue
            rows.append((t, subj_titles.get(t["subject_id"], ""), title))

        self._rows = rows
        self.table.setRowCount(len(rows))
        for r, (t, s, title) in enumerate(rows):
            cells = [
                s, str(t["kbs_id"]), t["type"] or "", str(t["id"]),
                title[:120], "✓" if is_supported(t["type"]) else "",
            ]
            for c, v in enumerate(cells):
                item = QTableWidgetItem(v)
                if c == 5:
                    item.setForeground(QColor("#36ad6a") if is_supported(t["type"]) else QColor("#888"))
                self.table.setItem(r, c, item)

    def _open_row(self, r, _c):
        if 0 <= r < len(self._rows):
            self.main.select_test(self._rows[r][0]["id"])


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MaximumTest Desktop")
        self.resize(1280, 820)

        self.api = Api()
        self.thread: SyncThread | None = None
        self._build_menu()
        self._build_ui()
        self._refresh_all()
        self._update_cookie_badge()

    # ---------- menu / toolbar ----------
    def _build_menu(self):
        m = self.menuBar()

        f = m.addMenu("&File")
        a = QAction("Import cookies…", self); a.triggered.connect(self._open_cookie_dialog); f.addAction(a)
        a = QAction("Quit", self); a.triggered.connect(self.close); f.addAction(a)

        s = m.addMenu("&Sync")
        a = QAction("Add subject by ID…", self); a.triggered.connect(self._add_subject); s.addAction(a)
        a = QAction("Sync selected subject", self); a.triggered.connect(self._sync_selected); s.addAction(a)
        a = QAction("Sync all subjects", self); a.triggered.connect(self._sync_all); s.addAction(a)
        a = QAction("Stop sync", self); a.triggered.connect(self._stop_sync); s.addAction(a)

        h = m.addMenu("&Help")
        a = QAction("About", self); a.triggered.connect(self._about); h.addAction(a)

    def _build_ui(self):
        tb = QToolBar("Main")
        self.addToolBar(tb)
        self.lbl_cookies = QLabel("  cookies: —  ")
        tb.addWidget(self.lbl_cookies)
        tb.addSeparator()
        for text, slot in [
            ("Sync selected", self._sync_selected),
            ("Sync all", self._sync_all),
            ("Stop", self._stop_sync),
            ("Import cookies", self._open_cookie_dialog),
        ]:
            b = QPushButton(text); b.clicked.connect(slot); tb.addWidget(b)

        sp = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(sp)

        # left: tree
        self.tabs = QTabWidget()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Item", "Tests"])
        self.tree.setColumnWidth(0, 360)
        self.tree.itemClicked.connect(self._on_tree_click)
        self.tabs.addTab(self.tree, "Tree")

        self.all_view = AllTestsView(self)
        self.tabs.addTab(self.all_view, "All tests")

        sp.addWidget(self.tabs)

        # right: details
        self.detail = QStackedWidget()
        self.v_subject = SubjectView(self); self.detail.addWidget(self.v_subject)
        self.v_kbs     = KbsView(self);     self.detail.addWidget(self.v_kbs)
        self.v_test    = TestView(self);    self.detail.addWidget(self.v_test)
        sp.addWidget(self.detail)
        sp.setSizes([480, 800])

        # status + log
        self.status = QStatusBar(); self.setStatusBar(self.status)
        self.progress = QProgressBar(); self.progress.setMaximumWidth(280); self.progress.setVisible(False)
        self.status.addPermanentWidget(self.progress)

        dock = QDockWidget("Log", self)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        dock.setWidget(self.log)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)

    # ---------- helpers ----------
    def _update_cookie_badge(self):
        if self.api.has_cookies():
            self.lbl_cookies.setText("  cookies: ✓  ")
            self.lbl_cookies.setStyleSheet("color:#36ad6a;font-weight:600;")
        else:
            self.lbl_cookies.setText("  cookies: —  ")
            self.lbl_cookies.setStyleSheet("color:#e74c3c;font-weight:600;")

    def _refresh_all(self):
        self._refresh_tree()
        self.all_view.refresh()

    def _refresh_tree(self):
        self.tree.clear()
        for s in db.list_subjects():
            st = db.stats_for_subject(s["id"])
            si = QTreeWidgetItem([s.get("title") or f"Subject #{s['id']}",
                                  f"{st['supported']}/{st['total']}"])
            si.setData(0, Qt.ItemDataRole.UserRole, ("subject", s["id"]))
            self.tree.addTopLevelItem(si)
            for k in db.list_kbs(s["id"]):
                tests = db.list_tests(k["id"])
                sup = sum(1 for t in tests if is_supported(t["type"]))
                ki = QTreeWidgetItem([f"KBS #{k['id']} ({k['mode']})", f"{sup}/{len(tests)}"])
                ki.setData(0, Qt.ItemDataRole.UserRole, ("kbs", k["id"]))
                si.addChild(ki)
                for t in tests:
                    p = json.loads(t["payload_json"])
                    title = (p.get("title") or p.get("name") or "")[:70]
                    ti = QTreeWidgetItem([f"[{t['type']}] #{t['id']} {title}", ""])
                    ti.setData(0, Qt.ItemDataRole.UserRole, ("test", t["id"]))
                    if not is_supported(t["type"]):
                        ti.setForeground(0, QColor("#888"))
                    ki.addChild(ti)
            si.setExpanded(True)

    # ---------- selection ----------
    def _on_tree_click(self, item, _col):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind = data[0]
        if kind == "subject":
            self.select_subject(data[1])
        elif kind == "kbs":
            self.select_kbs(data[1])
        elif kind == "test":
            self.select_test(data[1])

    def select_subject(self, sid: int):
        row = db.get_subject(sid)
        if not row:
            return
        self.v_subject.show_subject(row)
        self.detail.setCurrentWidget(self.v_subject)

    def select_kbs(self, kid: int):
        self.v_kbs.show_kbs(kid)
        self.detail.setCurrentWidget(self.v_kbs)

    def select_test(self, tid: int):
        self.v_test.show_test(tid)
        self.detail.setCurrentWidget(self.v_test)

    # ---------- sync ----------
    def _add_subject(self):
        text, ok = QInputDialog.getText(self, "Add subject", "Subject ID (from URL /subjects/<id>/):")
        if not ok or not text.strip():
            return
        try:
            sid = int(text.strip())
        except ValueError:
            QMessageBox.warning(self, "Invalid", "Введите число")
            return
        self._sync_one(sid)

    def _sync_selected(self):
        data = None
        item = self.tree.currentItem()
        if item:
            data = item.data(0, Qt.ItemDataRole.UserRole)
        # walk up to subject
        while data and data[0] != "subject":
            item = item.parent()
            data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if data:
            self._sync_one(data[1])
        else:
            QMessageBox.information(self, "Sync", "Выберите предмет в дереве")

    def _sync_all(self):
        subjects = db.list_subjects()
        if not subjects:
            QMessageBox.information(self, "Sync", "Нет предметов. Добавьте через Sync → Add subject by ID")
            return
        ids = [s["id"] for s in subjects]
        self._sync_queue(ids)

    def _sync_queue(self, ids: list[int]):
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "Sync", "Синхронизация уже идёт")
            return
        if not ids:
            return
        sid, rest = ids[0], ids[1:]
        self._sync_one(sid, on_done=lambda: self._sync_queue(rest))

    def _sync_one(self, sid: int, on_done=None):
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "Sync", "Синхронизация уже идёт")
            return
        th = SyncThread(self.api, sid)
        th.progress.connect(self._on_progress)
        th.log.connect(self._on_log)
        th.err.connect(self._on_sync_err)
        th.done.connect(lambda _sid: self._on_sync_done(_sid, on_done))
        th.finished.connect(th.deleteLater)
        self.thread = th
        self.progress.setVisible(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        th.start()

    def _stop_sync(self):
        if self.thread and self.thread.isRunning():
            self.thread.stop()

    def _on_progress(self, done: int, total: int, msg: str):
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        self.status.showMessage(f"{msg}  ({done}/{total})")

    def _on_log(self, msg: str):
        self.log.appendPlainText(msg)

    def _on_sync_err(self, msg: str):
        self._on_log(f"ERROR: {msg}")
        self.status.showMessage(msg)
        self.progress.setVisible(False)

    def _on_sync_done(self, _sid: int, on_done):
        self._on_log(f"✓ subject {_sid} synced")
        self.progress.setVisible(False)
        self.status.showMessage("done")
        self._refresh_all()
        if on_done:
            on_done()

    # ---------- misc ----------
    def _open_cookie_dialog(self):
        d = CookieDialog(self.api, self)
        if d.exec():
            self._update_cookie_badge()

    def _about(self):
        QMessageBox.information(self, "About",
            "MaximumTest Desktop 0.1\n\n"
            "Local SQLite cache, PyQt6, native Wayland.\n"
            "Cookies stored at ~/.local/share/maximumtest-desktop/cookies.json"
        )

    def closeEvent(self, e):
        if self.thread and self.thread.isRunning():
            self.thread.stop()
            self.thread.wait(2000)
        e.accept()
