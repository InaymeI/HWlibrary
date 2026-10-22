"""Main window: sidebar navigation, toolbar, pages and sync orchestration."""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QSettings, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QIcon, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import config, db, icons
from ..api import Api, AuthError
from ..links import ParsedLink, parse_link
from ..models import TestDetails, clear_cache, lesson_meta
from ..sync import SyncResult, SyncThread
from ..theme import PALETTES, Palette, stylesheet
from ..theme import palette as get_palette
from ..widgets import ICONS, Badge, label
from .dialogs import (
    CookieDialog,
    HelpDialog,
    JsonDialog,
    ResultDialog,
    SendDialog,
    about_text,
)
from .pages import (
    AllTestsPage,
    CoursePage,
    CoursesPage,
    DashboardPage,
    HomeworkPage,
    KbsPage,
    LessonPage,
    SubjectPage,
    TestPage,
)

APP_ICON = "book"


class Sidebar(QWidget):
    """Left navigation: brand, quick links, subject tree, footer stats."""

    navigate = pyqtSignal(str)

    def __init__(self, main: MainWindow) -> None:
        super().__init__()
        self.main = main
        self.setMinimumWidth(240)
        self.setMaximumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("sidebarHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 16, 16, 10)
        header_layout.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(icons.pixmap(APP_ICON, "#4a63dd", 22))
        logo.setFixedSize(38, 38)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet("background: #eceffe; border-radius: 10px;")
        self.logo = logo
        header_layout.addWidget(logo)
        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        brand_box.addWidget(label(config.APP_TITLE, "h3"))
        brand_box.addWidget(label("локальный кэш заданий", "muted"))
        header_layout.addLayout(brand_box, 1)
        layout.addWidget(header)

        quick = QWidget()
        quick_layout = QVBoxLayout(quick)
        quick_layout.setContentsMargins(10, 6, 10, 4)
        quick_layout.setSpacing(2)
        self.dashboard_button = self._quick_button("book", "Обзор")
        self.dashboard_button.clicked.connect(lambda: self.navigate.emit("dash"))
        quick_layout.addWidget(self.dashboard_button)
        self.courses_button = self._quick_button("book", "Предметы")
        self.courses_button.clicked.connect(lambda: self.navigate.emit("courses"))
        quick_layout.addWidget(self.courses_button)
        self.all_button = self._quick_button("grid", "Все тесты")
        self.all_button.clicked.connect(lambda: self.navigate.emit("all"))
        quick_layout.addWidget(self.all_button)
        layout.addWidget(quick)

        section = QWidget()
        section_layout = QHBoxLayout(section)
        section_layout.setContentsMargins(20, 10, 20, 2)
        self.section_label = label("ПРЕДМЕТЫ", "muted")
        section_layout.addWidget(self.section_label)
        section_layout.addStretch(1)
        self.count_label = label("", "muted")
        section_layout.addWidget(self.count_label)
        layout.addWidget(section)

        self.tree = QTreeWidget()
        self.tree.setObjectName("sidebar")
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setColumnWidth(1, 54)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.setIndentation(12)
        self.tree.setAnimated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.itemClicked.connect(self._on_click)
        self.tree.itemActivated.connect(self._on_click)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.tree, 1)

        footer = QFrame()
        footer.setObjectName("sidebarFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(18, 10, 16, 12)
        footer_layout.setSpacing(2)
        self.footer_stats = label("Кэш пуст", "muted")
        footer_layout.addWidget(self.footer_stats)
        self.footer_hint = label("F5 — синхронизация", "muted")
        footer_layout.addWidget(self.footer_hint)
        layout.addWidget(footer)
        self._expanded_state: dict[str, bool] = _load_expanded_state()

        self.tree.itemExpanded.connect(lambda _: self._save_expanded())
        self.tree.itemCollapsed.connect(lambda _: self._save_expanded())

    # ------------------------------------------------------------------ #
    def _quick_button(self, icon_name: str, text: str) -> QPushButton:
        button = QPushButton(f"  {text}")
        button.setProperty("variant", "ghost")
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        ICONS.bind(button, icon_name, "text_secondary", 17)
        button.setStyleSheet("text-align: left; padding: 8px 12px;")
        return button

    def highlight(self, page: str) -> None:
        self.dashboard_button.setChecked(page == "dash")
        self.courses_button.setChecked(page in ("courses", "course"))
        self.all_button.setChecked(page == "all")

    # ------------------------------------------------------------------ #
    def rebuild(self, palette: Palette) -> None:
        expanded = {**self._expanded_state, **self._expanded_states()}
        self.tree.blockSignals(True)
        self.tree.clear()
        self.section_label.setText("ПРЕДМЕТЫ")

        def add_homework(parent: QTreeWidgetItem, homework: dict[str, Any]) -> None:
            total = int(homework.get("total") or 0)
            supported = int(homework.get("supported") or 0)
            item = QTreeWidgetItem([
                homework.get("title") or f"Домашка #{homework['id']}",
                f"{supported}/{total}" if total else "",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, ("homework", int(homework["id"])))
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setForeground(1, QColor(palette.muted))
            item.setToolTip(
                0,
                f"{homework.get('title') or ''}\nДомашка #{homework['id']} · "
                f"тестов {total} · с ответом {supported}",
            )
            parent.addChild(item)

        def add_lesson(parent: QTreeWidgetItem, lesson: dict[str, Any],
                       homeworks: list[dict[str, Any]]) -> None:
            meta = lesson_meta(lesson)
            title = meta.heading if meta.number is not None else (
                lesson.get("title") or f"Занятие #{lesson['id']}"
            )
            if meta.date_text:
                title = f"{title} · {meta.date_text}"
            item = QTreeWidgetItem([title, str(len(homeworks)) if homeworks else ""])
            item.setData(0, Qt.ItemDataRole.UserRole, ("lesson", int(lesson["id"])))
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setForeground(1, QColor(palette.muted))
            item.setToolTip(
                0,
                f"{title}\nlessonId {lesson.get('entity_lesson_id') or '—'} · "
                f"домашек {len(homeworks)}",
            )
            for homework in homeworks:
                add_homework(item, homework)
            parent.addChild(item)
            item.setExpanded(expanded.get(f"l{lesson['id']}", False))

        count = 0
        for course, _lessons in db.course_tree():
            stats = {
                "total": course.get("total") or 0,
                "supported": course.get("supported") or 0,
                "lessons": course.get("lesson_count") or 0,
            }
            groups = db.course_homeworks(course["id"])
            item = QTreeWidgetItem([
                course.get("title") or f"Предмет #{course['id']}",
                f"{stats['supported']}/{stats['total']}",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, ("course", course["id"]))
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setToolTip(
                0,
                f"{course.get('title') or ''}\nПредмет #{course['id']} · "
                f"занятий {stats['lessons']} · тестов {stats['total']} · "
                f"с ответом {stats['supported']}",
            )
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            item.setForeground(1, QColor(palette.muted))
            self.tree.addTopLevelItem(item)
            count += 1
            for lesson, homeworks in groups["lessons"]:
                add_lesson(item, lesson, homeworks)
            if groups["orphans"]:
                extra = QTreeWidgetItem(["Домашки без занятия", ""])
                extra.setForeground(0, QColor(palette.muted))
                extra.setFlags(Qt.ItemFlag.ItemIsEnabled)
                item.addChild(extra)
                for homework in groups["orphans"]:
                    add_homework(extra, homework)
            item.setExpanded(expanded.get(f"c{course['id']}", True))

        in_courses = db.course_subject_ids()
        for subject in db.list_subjects():
            if subject["id"] in in_courses:
                continue
            counts = db.subject_stats(subject["id"])
            groups = db.subject_homeworks(subject["id"])
            item = QTreeWidgetItem([
                subject["title"] or f"Предмет #{subject['id']}",
                f"{counts['supported']}/{counts['total']}",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, ("subject", subject["id"]))
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setToolTip(
                0,
                f"{subject['title']}\nID {subject['id']} · тестов {counts['total']} · "
                f"с ответом {counts['supported']}",
            )
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            item.setForeground(1, QColor(palette.muted))
            for lesson, homeworks in groups["lessons"]:
                add_lesson(item, lesson, homeworks)
            for homework in groups["orphans"]:
                add_homework(item, homework)
            self.tree.addTopLevelItem(item)
            count += 1
            item.setExpanded(expanded.get(str(subject["id"]), True))
        self.tree.blockSignals(False)

        self.count_label.setText(str(count) if count else "")
        stats = db.overall_stats()
        if stats["total"]:
            self.footer_stats.setText(
                f"{stats['total']:,} тестов · {stats['supported']:,} с ответом".replace(",", " ")
            )
        else:
            self.footer_stats.setText("Кэш пуст")
        if stats.get("courses"):
            lessons = sum(course.get("lesson_count") or 0 for course in db.list_courses())
            self.footer_hint.setText(
                f"{stats['courses']} предм. · {lessons} занятий · "
                f"{_short_time(stats['updated_at'])}"
            )
        else:
            self.footer_hint.setText(f"Обновлено: {_short_time(stats['updated_at'])}")

    def _expanded_states(self) -> dict[str, bool]:
        states: dict[str, bool] = {}

        def walk(item: QTreeWidgetItem) -> None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data:
                states[_state_key(data)] = item.isExpanded()
            for index in range(item.childCount()):
                walk(item.child(index))

        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            walk(root.child(index))
        return states

    def _save_expanded(self) -> None:
        self._expanded_state.update(self._expanded_states())
        QSettings().setValue("sidebar/expanded", json.dumps(self._expanded_state))

    def _items(self) -> list[QTreeWidgetItem]:
        items: list[QTreeWidgetItem] = []

        def walk(item: QTreeWidgetItem) -> None:
            items.append(item)
            for index in range(item.childCount()):
                walk(item.child(index))

        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            walk(root.child(index))
        return items

    def select(self, kind: str, item_id: int) -> None:
        self.tree.blockSignals(True)
        target = None
        for item in self._items():
            if item.data(0, Qt.ItemDataRole.UserRole) == (kind, item_id):
                target = item
                break
        if target is not None:
            parent = target.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            self.tree.setCurrentItem(target)
            self.tree.scrollToItem(target)
        self.tree.blockSignals(False)

    def clear_selection(self) -> None:
        self.tree.blockSignals(True)
        self.tree.setCurrentItem(None)
        self.tree.clearSelection()
        self.tree.blockSignals(False)

    # ------------------------------------------------------------------ #
    def _on_click(self, item: QTreeWidgetItem, _column: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind, item_id = data
        if kind == "course":
            self.navigate.emit(f"course:{item_id}")
        elif kind == "homework":
            self.navigate.emit(f"homework:{item_id}")
        elif kind == "lesson":
            self.navigate.emit(f"lesson:{item_id}")
        elif kind == "subject":
            self.navigate.emit(f"subject:{item_id}")
        elif kind == "kbs":
            self.navigate.emit(f"kbs:{item_id}")

    def _context_menu(self, point) -> None:
        item = self.tree.itemAt(point)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind, item_id = data
        menu = QMenu(self)
        if kind == "course":
            course = db.get_course(item_id) or {}
            menu.addAction(icons.icon("external", "#59616e", 16), "Открыть",
                           lambda: self.navigate.emit(f"course:{item_id}"))
            menu.addAction(icons.icon("refresh", "#59616e", 16), "Синхронизировать курс",
                           lambda: self.main.sync_course(item_id))
            menu.addAction(icons.icon("copy", "#59616e", 16), "Копировать ID",
                           lambda: QGuiApplication.clipboard().setText(str(item_id)))
            menu.addSeparator()
            menu.addAction(icons.icon("trash", "#c53b3b", 16), "Удалить курс из кэша",
                           lambda: self.main.delete_course(item_id))
            menu.setToolTip(course.get("title") or "")
        elif kind == "homework":
            homework = db.get_kbs(item_id) or {}
            menu.addAction(icons.icon("external", "#59616e", 16), "Открыть домашку",
                           lambda: self.navigate.emit(f"homework:{item_id}"))
            menu.addAction(icons.icon("grid", "#59616e", 16), "Списком заданий",
                           lambda: self.navigate.emit(f"kbs:{item_id}"))
            menu.addAction(icons.icon("refresh", "#59616e", 16), "Синхронизировать предмет",
                           lambda: self.main.sync_subject(homework.get("subject_id")))
            menu.addAction(icons.icon("copy", "#59616e", 16), "Копировать ID",
                           lambda: QGuiApplication.clipboard().setText(str(item_id)))
        elif kind == "lesson":
            menu.addAction(icons.icon("external", "#59616e", 16), "Открыть урок",
                           lambda: self.navigate.emit(f"lesson:{item_id}"))
            menu.addAction(icons.icon("refresh", "#59616e", 16), "Синхронизировать урок",
                           lambda: self.main.sync_lesson(item_id))
            menu.addAction(icons.icon("copy", "#59616e", 16), "Копировать ID",
                           lambda: QGuiApplication.clipboard().setText(str(item_id)))
        elif kind == "subject":
            subject = db.get_subject(item_id) or {}
            menu.addAction(icons.icon("external", "#59616e", 16), "Открыть",
                           lambda: self.navigate.emit(f"subject:{item_id}"))
            menu.addAction(icons.icon("refresh", "#59616e", 16), "Синхронизировать",
                           lambda: self.main.sync_subject(item_id))
            menu.addAction(icons.icon("copy", "#59616e", 16), "Копировать ID",
                           lambda: QGuiApplication.clipboard().setText(str(item_id)))
            menu.addSeparator()
            menu.addAction(icons.icon("trash", "#c53b3b", 16), "Удалить из кэша",
                           lambda: self.main.delete_subject(item_id))
            menu.setToolTip(subject.get("title") or "")
        elif kind == "kbs":
            kbs = db.get_kbs(item_id) or {}
            menu.addAction(icons.icon("external", "#59616e", 16), "Открыть",
                           lambda: self.navigate.emit(f"kbs:{item_id}"))
            menu.addAction(icons.icon("refresh", "#59616e", 16), "Синхронизировать предмет",
                           lambda: self.main.sync_subject(kbs.get("subject_id")))
            menu.addAction(icons.icon("copy", "#59616e", 16), "Копировать ID",
                           lambda: QGuiApplication.clipboard().setText(str(item_id)))
            menu.addSeparator()
            menu.addAction(icons.icon("trash", "#c53b3b", 16), "Удалить раздел из кэша",
                           lambda: self.main.delete_kbs(item_id))
        menu.exec(self.tree.viewport().mapToGlobal(point))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        config.ensure_dirs()
        self.settings = QSettings()
        self.api = Api()
        self._theme_name = self._initial_theme()
        self._pal = get_palette(self._theme_name)
        self._sync: SyncThread | None = None
        self._sync_queue_results: list[SyncResult] = []
        self._pending_test: int | None = None
        self._history: list[str] = []
        self._future: list[str] = []
        self._from_history = False

        self.setWindowTitle(config.APP_TITLE)
        self.setWindowIcon(_app_icon())
        self.setMinimumSize(QSize(1024, 640))
        self.resize(QSize(1360, 880))

        self._build_actions()
        self._build_menu()
        self._build_ui()
        self._build_statusbar()
        self._connect_shortcuts()
        self._restore_state()
        self.apply_theme(self._theme_name, persist=False)
        self.refresh()

    # ------------------------------------------------------------------ #
    # construction
    # ------------------------------------------------------------------ #
    def theme_palette(self) -> Palette:
        return self._pal

    def _initial_theme(self) -> str:
        stored = str(self.settings.value("ui/theme", "system"))
        if stored in PALETTES:
            return stored
        scheme = QGuiApplication.styleHints().colorScheme()
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"

    def _build_actions(self) -> None:
        def action(text: str, icon_name: str | None, slot, shortcut: str | None = None,
                   color_key: str = "text_secondary") -> QAction:
            act = QAction(text, self)
            if icon_name:
                ICONS.bind(act, icon_name, color_key, 16)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(slot)
            return act

        self.act_sync_selected = action("Синхронизировать", "refresh",
                                        self._sync_current_subject, "F5")
        self.act_sync_selected.setToolTip(
            "Синхронизировать текущий курс, урок или предмет (F5)"
        )
        self.act_sync_all = action("Синхронизировать всё", "refresh", self.sync_all)
        self.act_stop = action("Остановить", "close", self.stop_sync)
        self.act_add_subject = action(
            "Добавить предмет по ссылке…", "plus", self.add_link_dialog, "Ctrl+N"
        )
        self.act_cookies = action("Cookies сессии…", "cookie", self.import_cookies)
        self.act_refresh = action("Обновить", "refresh", self.refresh, "Ctrl+R")
        self.act_log = action("Журнал синхронизации", "terminal", self.toggle_log, "Ctrl+L")
        self.act_log.setCheckable(True)
        self.act_export = action("Экспорт выбранного предмета…", "download", self._export_current)
        self.act_courses = action("Предметы", "book", lambda: self.navigate("courses"))
        self.act_all_tests = action("Все тесты", "grid", lambda: self.navigate("all"))
        self.act_dashboard = action("Обзор", "book", lambda: self.navigate("dash"))
        self.act_back = action("Назад", "arrow-left", self.go_back, "Alt+Left")
        self.act_forward = action("Вперёд", "chevron-right", self.go_forward, "Alt+Right")
        self.act_find = action("Поиск по тестам", "search", self.focus_search, "Ctrl+F")
        self.act_help = action("Как получить cookies", "info", self.show_help, "F1")
        self.act_clear = action("Очистить кэш…", "trash", self.clear_cache_dialog)
        self.act_quit = action("Выход", None, self.close, "Ctrl+Q")

        self.act_stop.setEnabled(False)
        self.act_back.setEnabled(False)
        self.act_forward.setEnabled(False)

        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_actions: dict[str, QAction] = {}
        for name, title in (("light", "Светлая"), ("dark", "Тёмная"), ("system", "Как в системе")):
            act = QAction(title, self, checkable=True)
            act.setChecked(name == self._stored_theme_name())
            act.triggered.connect(lambda _checked, key=name: self.apply_theme(key))
            self.theme_group.addAction(act)
            self.theme_actions[name] = act

    def _stored_theme_name(self) -> str:
        return str(self.settings.value("ui/theme", "system"))

    def _build_menu(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("Файл")
        file_menu.addAction(self.act_cookies)
        file_menu.addAction(self.act_refresh)
        file_menu.addSeparator()
        file_menu.addAction(self.act_export)
        file_menu.addAction(self.act_clear)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        sync_menu = bar.addMenu("Синхронизация")
        sync_menu.addAction(self.act_add_subject)
        sync_menu.addAction(self.act_sync_selected)
        sync_menu.addAction(self.act_sync_all)
        sync_menu.addAction(self.act_stop)

        view_menu = bar.addMenu("Вид")
        view_menu.addAction(self.act_dashboard)
        view_menu.addAction(self.act_courses)
        view_menu.addAction(self.act_all_tests)
        view_menu.addSeparator()
        view_menu.addAction(self.act_back)
        view_menu.addAction(self.act_forward)
        view_menu.addAction(self.act_find)
        theme_menu = view_menu.addMenu("Тема")
        for act in self.theme_actions.values():
            theme_menu.addAction(act)
        view_menu.addSeparator()
        view_menu.addAction(self.act_log)
        view_menu.addSeparator()
        reset = QAction("Сбросить настройки окна", self)
        reset.triggered.connect(self.reset_layout)
        view_menu.addAction(reset)

        help_menu = bar.addMenu("Справка")
        help_menu.addAction(self.act_help)
        about = QAction("О программе", self)
        about.triggered.connect(self.show_about)
        help_menu.addAction(about)

    def _build_ui(self) -> None:
        toolbar = QToolBar("Основное")
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(17, 17))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        self.back_button = QToolButton()
        self.back_button.setDefaultAction(self.act_back)
        self.forward_button = QToolButton()
        self.forward_button.setDefaultAction(self.act_forward)
        toolbar.addWidget(self.back_button)
        toolbar.addWidget(self.forward_button)
        toolbar.addSeparator()

        self.sync_button = QToolButton()
        self.sync_button.setDefaultAction(self.act_sync_selected)
        self.sync_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.sync_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        sync_menu = QMenu(self.sync_button)
        sync_menu.addAction(self.act_sync_all)
        sync_menu.addAction(self.act_stop)
        self.sync_button.setMenu(sync_menu)
        toolbar.addWidget(self.sync_button)
        toolbar.addAction(self.act_add_subject)
        toolbar.addSeparator()
        toolbar.addAction(self.act_all_tests)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self.cookie_badge = Badge("cookies не заданы", "danger")
        self.cookie_badge.setToolTip("Cookies нужны для доступа к заданиям")
        toolbar.addWidget(self.cookie_badge)
        self.cookie_button = QToolButton()
        self.cookie_button.setDefaultAction(self.act_cookies)
        self.cookie_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.addWidget(self.cookie_button)

        self.theme_button = QToolButton()
        self.theme_button.setIcon(icons.icon("moon", "#59616e", 17))
        self.theme_button.setToolTip("Переключить тему")
        self.theme_button.setAutoRaise(True)
        self.theme_button.clicked.connect(self.toggle_theme)
        toolbar.addWidget(self.theme_button)

        self.log_button = QToolButton()
        self.log_button.setDefaultAction(self.act_log)
        self.log_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        toolbar.addWidget(self.log_button)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.splitter = splitter
        self.sidebar = Sidebar(self)
        self.sidebar.navigate.connect(self.navigate)
        splitter.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.dashboard_page = DashboardPage(self)
        self.courses_page = CoursesPage(self)
        self.course_page = CoursePage(self)
        self.lesson_page = LessonPage(self)
        self.homework_page = HomeworkPage(self)
        self.subject_page = SubjectPage(self)
        self.kbs_page = KbsPage(self)
        self.test_page = TestPage(self)
        self.all_page = AllTestsPage(self)
        for page in (self.dashboard_page, self.courses_page, self.course_page,
                     self.lesson_page, self.homework_page, self.subject_page,
                     self.kbs_page, self.test_page, self.all_page):
            page.navigate.connect(self.navigate)
            self.stack.addWidget(page)
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([288, 1072])
        self.setCentralWidget(splitter)

        self.log_dock = QDockWidget("Журнал", self)
        self.log_dock.setObjectName("logDock")
        self.log_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea
                                      | Qt.DockWidgetArea.RightDockWidgetArea)
        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.log_dock.setWidget(self.log)
        self.log_dock.setVisible(False)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        self.log_dock.visibilityChanged.connect(self.act_log.setChecked)

    def _build_statusbar(self) -> None:
        status = QStatusBar()
        self.setStatusBar(status)
        self.status_label = QLabel("Готово")
        status.addWidget(self.status_label, 1)
        self.db_label = QLabel()
        status.addPermanentWidget(self.db_label)
        self.progress = QProgressBar()
        self.progress.setFixedSize(220, 12)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        status.addPermanentWidget(self.progress)

    def _connect_shortcuts(self) -> None:
        self.act_find.triggered.connect(self.focus_search)

    def _restore_state(self) -> None:
        geometry = self.settings.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        state = self.settings.value("window/state")
        if state:
            self.restoreState(state)
        sizes = self.settings.value("window/splitter")
        if sizes:
            with contextlib.suppress(TypeError, ValueError):
                self.splitter.setSizes([int(value) for value in sizes])
        self.log_dock.setVisible(bool(self.settings.value("ui/log", False, type=bool)))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        self.settings.setValue("window/splitter", self.splitter.sizes())
        self.settings.setValue("ui/log", self.log_dock.isVisible())
        thread = self._sync_thread()
        if thread is not None and thread.isRunning():
            thread.stop()
            thread.wait(2500)
        self._sync = None
        db.close()
        super().closeEvent(event)

    # ------------------------------------------------------------------ #
    # theme
    # ------------------------------------------------------------------ #
    def toggle_theme(self) -> None:
        self.apply_theme("light" if self._pal.name == "dark" else "dark")

    def apply_theme(self, name: str, persist: bool = True) -> None:
        if name == "system":
            scheme = QGuiApplication.styleHints().colorScheme()
            resolved = "dark" if scheme == Qt.ColorScheme.Dark else "light"
        else:
            resolved = name if name in PALETTES else "light"
        self._theme_name = name
        self._pal = get_palette(resolved)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet(resolved))
        ICONS.recolor(self._pal)
        self.theme_button.setIcon(icons.icon(
            "sun" if resolved == "dark" else "moon", self._pal.text_secondary, 17
        ))
        self.sidebar.logo.setPixmap(icons.pixmap(APP_ICON, self._pal.accent, 22))
        self.sidebar.logo.setStyleSheet(
            f"background: {self._pal.accent_soft}; border-radius: 10px;"
        )
        self.dashboard_page.apply_theme(self._pal)
        self.courses_page.apply_theme(self._pal)
        self.course_page.apply_theme(self._pal)
        self.lesson_page.apply_theme(self._pal)
        self.homework_page.apply_theme(self._pal)
        self.kbs_page.apply_theme(self._pal)
        self.all_page.apply_theme(self._pal)
        self.test_page.apply_theme(self._pal)
        act = self.theme_actions.get(name)
        if act and not act.isChecked():
            act.setChecked(True)
        self.sidebar.rebuild(self._pal)
        if persist:
            self.settings.setValue("ui/theme", name)

    # ------------------------------------------------------------------ #
    # navigation
    # ------------------------------------------------------------------ #
    def navigate(self, target: str) -> None:
        if not target:
            return
        if not self._from_history and target not in ("back", "forward"):
            current = self._current_target()
            if current and current != target:
                self._history.append(current)
                self._future.clear()
                if len(self._history) > 60:
                    self._history = self._history[-60:]
        self._from_history = False
        self._update_history_buttons()
        self._open(target)

    def _current_target(self) -> str | None:
        widget = self.stack.currentWidget()
        if widget is self.subject_page and self.subject_page.subject_id:
            return f"subject:{self.subject_page.subject_id}"
        if widget is self.course_page and self.course_page.course_id:
            return f"course:{self.course_page.course_id}"
        if widget is self.homework_page and self.homework_page.kbs_id:
            return f"homework:{self.homework_page.kbs_id}"
        if widget is self.lesson_page and self.lesson_page.lesson_id:
            return f"lesson:{self.lesson_page.lesson_id}"
        if widget is self.kbs_page and self.kbs_page.kbs_id:
            return f"kbs:{self.kbs_page.kbs_id}"
        if widget is self.test_page and self.test_page.test_id:
            return f"test:{self.test_page.test_id}"
        if widget is self.all_page:
            return "all"
        if widget is self.courses_page:
            return "courses"
        if widget is self.dashboard_page:
            return "dash"
        return None

    def _open(self, target: str) -> None:
        if target in ("dash", "all"):
            self.sidebar.clear_selection()
            self.stack.setCurrentWidget(self.dashboard_page if target == "dash" else self.all_page)
            self.sidebar.highlight(target)
            if target == "all":
                self.all_page.refresh_subjects()
                self.all_page.reload()
        elif target == "courses":
            self.sidebar.clear_selection()
            self.courses_page.refresh()
            self.stack.setCurrentWidget(self.courses_page)
            self.sidebar.highlight("courses")
        elif target.startswith("course:"):
            course_id = _to_int(target.split(":", 1)[1])
            if course_id is None:
                return
            self.course_page.show_course(course_id)
            self.course_page.scroll_to_top()
            self.stack.setCurrentWidget(self.course_page)
            self.sidebar.highlight("course")
            self.sidebar.select("course", course_id)
        elif target.startswith("homework:"):
            kbs_id = _to_int(target.split(":", 1)[1])
            if kbs_id is None:
                return
            self.homework_page.show_homework(kbs_id)
            self.homework_page.scroll_to_top()
            self.stack.setCurrentWidget(self.homework_page)
            self.sidebar.highlight("")
            self.sidebar.select("homework", kbs_id)
            self.status_label.setText("Готово")
            self._update_status()
            return
        elif target.startswith("lesson:"):
            lesson_id = _to_int(target.split(":", 1)[1])
            if lesson_id is None:
                return
            self.lesson_page.show_lesson(lesson_id)
            self.lesson_page.scroll_to_top()
            self.stack.setCurrentWidget(self.lesson_page)
            self.sidebar.highlight("")
            self.sidebar.select("lesson", lesson_id)
        elif target.startswith("subject:"):
            subject_id = _to_int(target.split(":", 1)[1])
            if subject_id is None:
                return
            self.subject_page.show_subject(subject_id)
            self.subject_page.scroll_to_top()
            self.stack.setCurrentWidget(self.subject_page)
            self.sidebar.highlight("")
            self.sidebar.select("subject", subject_id)
        elif target.startswith("kbs:"):
            kbs_id = _to_int(target.split(":", 1)[1])
            if kbs_id is None:
                return
            self.kbs_page.show_kbs(kbs_id)
            self.stack.setCurrentWidget(self.kbs_page)
            self.sidebar.highlight("")
            self.sidebar.select("kbs", kbs_id)
        elif target.startswith("test:"):
            test_id = _to_int(target.split(":", 1)[1])
            if test_id is None:
                return
            self.test_page.show_test(test_id)
            self.test_page.scroll_to_top()
            self.stack.setCurrentWidget(self.test_page)
            self.sidebar.highlight("")
            row = db.get_test(test_id)
            if row:
                self.sidebar.select("kbs", row["kbs_id"])
            self._update_status_for_test(test_id)
            return
        self.status_label.setText("Готово")
        self._update_status()

    def go_back(self) -> None:
        if not self._history:
            return
        current = self._current_target()
        target = self._history.pop()
        if current:
            self._future.append(current)
        self._from_history = True
        self.navigate(target)
        self._update_history_buttons()

    def go_forward(self) -> None:
        if not self._future:
            return
        current = self._current_target()
        target = self._future.pop()
        if current:
            self._history.append(current)
        self._from_history = True
        self.navigate(target)
        self._update_history_buttons()

    def _update_history_buttons(self) -> None:
        self.act_back.setEnabled(bool(self._history))
        self.act_forward.setEnabled(bool(self._future))

    def _update_status(self) -> None:
        size = db.db_size_bytes()
        size_text = f"{size / 1024 / 1024:.1f} МБ" if size > 1024 * 1024 else f"{max(size // 1024, 0)} КБ"
        self.db_label.setText(
            f"{config.APP_NAME} {config.APP_VERSION} · база {size_text}"
        )
        self._update_cookie_badge()

    def _update_status_for_test(self, test_id: int) -> None:
        self._update_status()
        row = db.get_test(test_id)
        if row:
            self.status_label.setText(f"Задание #{test_id} · {row.get('title') or ''}")

    def focus_search(self) -> None:
        self.navigate("all")
        self.all_page.search.setFocus()
        self.all_page.search.selectAll()

    # ------------------------------------------------------------------ #
    # refresh
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        clear_cache()
        self.sidebar.rebuild(self._pal)
        self.dashboard_page.refresh()
        self.courses_page.refresh()
        self.all_page.refresh_subjects()
        self.all_page.reload()
        if self.course_page.course_id:
            self.course_page.refresh()
        if self.lesson_page.lesson_id:
            self.lesson_page.refresh()
        if self.homework_page.kbs_id:
            self.homework_page.refresh()
        if self.subject_page.subject_id:
            self.subject_page.refresh()
        if self.kbs_page.kbs_id:
            self.kbs_page.refresh()
        if self.test_page.test_id:
            self.test_page.show_test(self.test_page.test_id)
        self._update_status()

    def _update_cookie_badge(self) -> None:
        has_cookies = self.api.has_cookies()
        self.cookie_badge.setText("cookies активны" if has_cookies else "cookies не заданы")
        self.cookie_badge.set_variant("success" if has_cookies else "danger")
        self.cookie_badge.setToolTip(
            self.api.cookie_summary() if has_cookies else "Нажмите, чтобы импортировать cookies"
        )
        self.act_cookies.setText("Cookies сессии…" if not has_cookies else "Обновить cookies…")

    # ------------------------------------------------------------------ #
    # sync
    # ------------------------------------------------------------------ #
    def _sync_current_subject(self) -> None:
        widget = self.stack.currentWidget()
        if widget is self.course_page and self.course_page.course_id:
            self.sync_course(self.course_page.course_id)
            return
        if widget is self.lesson_page and self.lesson_page.lesson_id:
            self.sync_lesson(self.lesson_page.lesson_id)
            return
        if widget is self.homework_page and self.homework_page.kbs_id:
            kbs = db.get_kbs(self.homework_page.kbs_id)
            if kbs:
                self.sync_subject(kbs["subject_id"])
            return
        subject_id = self._current_subject_id()
        if subject_id is None:
            self.add_subject_dialog()
            return
        self.sync_subject(subject_id)

    def _current_subject_id(self) -> int | None:
        widget = self.stack.currentWidget()
        if widget is self.subject_page:
            return self.subject_page.subject_id
        if widget is self.kbs_page and self.kbs_page.kbs_id:
            kbs = db.get_kbs(self.kbs_page.kbs_id)
            return kbs["subject_id"] if kbs else None
        if widget is self.homework_page and self.homework_page.kbs_id:
            kbs = db.get_kbs(self.homework_page.kbs_id)
            return int(kbs["subject_id"]) if kbs else None
        if widget is self.test_page and self.test_page.row:
            return int(self.test_page.row["subject_id"])
        item = self.sidebar.tree.currentItem()
        while item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data[0] == "subject":
                return int(data[1])
            item = item.parent()
        subjects = db.list_subjects()
        return subjects[0]["id"] if len(subjects) == 1 else None

    def sync_subject(self, subject_id: int, *, silent: bool = False) -> None:
        if not self._ensure_cookies(silent=silent):
            return
        self._start_sync([subject_id])

    def sync_all(self) -> None:
        if not self._ensure_cookies():
            return
        courses = db.list_courses()
        subjects = db.list_subjects()
        if not subjects and not courses:
            self.add_link_dialog()
            return
        # Subjects that belong to a course are refreshed through its structure,
        # everything else directly.
        covered = db.course_subject_ids()
        loose = [subject["id"] for subject in subjects if subject["id"] not in covered]
        self._start_sync(loose, [course["id"] for course in courses])

    def sync_course(self, course_id: int, *, silent: bool = False) -> None:
        """Download the whole course: lessons, practice blocks, tests."""
        if not self._ensure_cookies(silent=silent):
            return
        self._start_sync([], [course_id])

    def sync_lesson(self, lesson_id: int) -> None:
        """Refresh the subjects of a single lesson."""
        subject_ids = db.lesson_subject_ids(lesson_id)
        if not subject_ids:
            self.toast(
                "В уроке нет загруженных практик — синхронизируйте курс", error=True
            )
            return
        if not self._ensure_cookies():
            return
        self._start_sync(subject_ids)

    def _ensure_cookies(self, silent: bool = False) -> bool:
        if self.api.has_cookies():
            return True
        if silent:
            return False
        answer = QMessageBox.question(
            self,
            "Нужны cookies",
            "Для доступа к заданиям нужны cookies вашей сессии.\nИмпортировать их сейчас?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self.import_cookies()
        return self.api.has_cookies()

    def _sync_thread(self) -> SyncThread | None:
        """Return the worker only if its C++ object still exists.

        ``thread.finished`` is connected to ``deleteLater``, so after a run the
        wrapper in ``self._sync`` dangles; touching it raises
        ``RuntimeError: wrapped C/C++ object ... has been deleted``.
        """
        thread = self._sync
        if thread is None or sip.isdeleted(thread):
            self._sync = None
            return None
        return thread

    def _on_sync_thread_finished(self) -> None:
        if self.sender() is self._sync:
            self._sync = None

    def _start_sync(self, subject_ids: list[int], course_ids: list[int] | None = None) -> None:
        current = self._sync_thread()
        if current is not None and current.isRunning():
            self.toast("Синхронизация уже идёт", error=True)
            return
        self._sync_queue_results = []
        thread = SyncThread(self.api, subject_ids, course_ids, parent=self)
        thread.progress.connect(self._on_sync_progress)
        thread.log.connect(self._append_log)
        thread.subject_done.connect(self._on_subject_done)
        thread.finished_all.connect(self._on_sync_finished)
        thread.failed.connect(self._on_sync_failed)
        thread.finished.connect(self._on_sync_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._sync = thread
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)  # busy until the first progress event
        self.act_stop.setEnabled(True)
        self.act_sync_selected.setEnabled(False)
        self.act_sync_all.setEnabled(False)
        self.status_label.setText("Синхронизация…")
        targets = [f"предмет {cid}" for cid in course_ids or []]
        targets += [f"предмет {sid}" for sid in subject_ids]
        self._append_log("— старт синхронизации: " + ", ".join(targets))
        thread.start()

    def stop_sync(self) -> None:
        thread = self._sync_thread()
        if thread is not None and thread.isRunning():
            thread.stop()
            self.act_stop.setEnabled(False)
            self.status_label.setText("Останавливаю синхронизацию…")
            self._append_log("— запрошена остановка")

    def _on_sync_progress(self, done: int, total: int, message: str) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        self.status_label.setText(f"Синхронизация: {message} ({done}/{total})")

    def _on_subject_done(self, result: SyncResult) -> None:
        self._sync_queue_results.append(result)
        if result.error:
            return
        self.sidebar.rebuild(self._pal)
        if self.subject_page.subject_id == result.subject_id:
            self.subject_page.refresh()
        if self.kbs_page.kbs_id:
            self.kbs_page.refresh()
        self.dashboard_page.refresh()
        self.all_page.refresh_subjects()
        self.all_page.reload()
        if self.course_page.course_id:
            self.course_page.refresh()
        if self.lesson_page.lesson_id:
            self.lesson_page.refresh()

    def _on_sync_finished(self, results: list[SyncResult]) -> None:
        self.progress.setVisible(False)
        self.act_stop.setEnabled(False)
        self.act_sync_selected.setEnabled(True)
        self.act_sync_all.setEnabled(True)
        total_tests = sum(result.tests for result in results)
        failed_subjects = sum(1 for result in results if result.error)
        errors = sum(result.errors for result in results) + failed_subjects
        seconds = sum(result.seconds for result in results)
        courses = [result for result in results if result.course_id]
        plain = [result for result in results if not result.course_id]
        if results:
            parts: list[str] = []
            if courses:
                lessons = sum(result.lessons for result in courses)
                parts.append(
                    f"{len(courses)} {_plural(len(courses), 'предмет', 'предмета', 'предметов')}"
                )
                parts.append(
                    f"{lessons} {_plural(lessons, 'занятие', 'занятия', 'занятий')}"
                )
            if plain:
                parts.append(
                    f"{len(plain)} {_plural(len(plain), 'раздел', 'раздела', 'разделов')}"
                )
            parts.append(f"{total_tests} {_plural(total_tests, 'тест', 'теста', 'тестов')}")
            if errors:
                parts.append(f"{errors} {_plural(errors, 'ошибка', 'ошибки', 'ошибок')}")
            message = "Готово: " + ", ".join(parts) + f" за {seconds:.1f} с"
        else:
            message = "Синхронизация завершена"
        self.status_label.setText(message)
        self._append_log("— " + message)
        self.toast(message, error=errors > 0)
        self.refresh()
        self._update_status()
        self._open_pending_test()

    def _open_pending_test(self) -> None:
        """After a sync started from a task link, open that task."""
        test_id, self._pending_test = self._pending_test, None
        if not test_id:
            return
        if db.get_test(test_id):
            self.navigate(f"test:{test_id}")
        else:
            self.toast(f"Задание #{test_id} не найдено в кэше", error=True)

    def _on_sync_failed(self, title: str, message: str) -> None:
        self._append_log(f"ОШИБКА: {title}: {message}")
        self.status_label.setText(f"{title}: {message}")
        if title == "Нужна авторизация":
            self._update_cookie_badge()

    # ------------------------------------------------------------------ #
    # actions
    # ------------------------------------------------------------------ #
    def add_link_dialog(self) -> None:
        """Accept any MaximumTest link: a course, a task, a subject."""
        text, ok = QInputDialog.getText(
            self,
            "Добавить из ссылки",
            "Вставьте ссылку на предмет, занятие или задание:\n"
            "  • предмет: …/student/courses/111137/lessons\n"
            "  • задание: …/lesson/4772366/subjects/51610/practice/kbs/8931/tests/289165\n"
            "  • раздел: …/subjects/51610/",
        )
        if not ok or not text.strip():
            return
        link = parse_link(text)
        if link is None:
            self.toast("Не удалось найти ID в строке", error=True)
            return
        self.open_link(link)

    #: Old name kept for callers and tests.
    def add_subject_dialog(self) -> None:
        self.add_link_dialog()

    def open_link(self, link: ParsedLink) -> None:
        """Do the right thing for a parsed link."""
        if link.subject_id is not None and link.lesson_id is not None:
            # /lesson/<entity_lesson_id>/… — the documented lessonId fallback.
            self.set_lesson_hint(link.subject_id, link.lesson_id)

        if link.kind == "course":
            if link.course_id is None:
                self.toast("В ссылке не найден ID курса", error=True)
                return
            if not self._ensure_cookies():
                return
            self.toast(f"Загружаю предмет #{link.course_id}…")
            self.sync_course(link.course_id)
            return

        if link.kind == "kbs":
            kbs = db.get_kbs(link.kbs_id) if link.kbs_id else None
            if kbs:
                self.navigate(f"kbs:{kbs['id']}")
            else:
                self.toast(
                    "Раздел ещё не в кэше — вставьте ссылку на предмет или курс", error=True
                )
            return

        if link.subject_id is None:
            self.toast("В ссылке не найден предмет", error=True)
            return

        # A task that is already cached opens instantly; the network is only
        # needed for a subject that has never been downloaded (or to refresh).
        if link.test_id is not None and db.get_test(link.test_id):
            self._pending_test = None
            self.navigate(f"test:{link.test_id}")
            return

        if link.test_id is not None:
            self._pending_test = link.test_id
        if not self._ensure_cookies():
            self._pending_test = None
            return
        self._start_sync([link.subject_id])

    def delete_course(self, course_id: int) -> None:
        course = db.get_course(course_id)
        if not course:
            return
        answer = QMessageBox.question(
            self,
            "Удалить предмет",
            f"Удалить структуру «{course['title']}» из кэша?\n"
            "Скачанные разделы и задания останутся доступны.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        db.delete_course(course_id)
        self.toast("Предмет удалён из кэша")
        self.refresh()
        on_deleted_course = self.course_page.course_id == course_id
        on_deleted_lesson = bool(
            self.lesson_page.lesson_id and not db.get_lesson(self.lesson_page.lesson_id)
        )
        if on_deleted_course or on_deleted_lesson:
            self.navigate("courses")

    # ------------------------------------------------------------------ #
    # lessonId hints (fallback when the tree has no lessonId)
    # ------------------------------------------------------------------ #
    def lesson_hint(self, subject_id: int | None) -> int | None:
        if subject_id is None:
            return None
        hints = _load_lesson_hints()
        value = hints.get(str(subject_id))
        return int(value) if value else None

    def set_lesson_hint(self, subject_id: int, lesson_id: int) -> None:
        hints = _load_lesson_hints()
        if hints.get(str(subject_id)) != lesson_id:
            hints[str(subject_id)] = int(lesson_id)
            QSettings().setValue("lesson/hints", json.dumps(hints))

    def import_cookies(self) -> None:
        dialog = CookieDialog(self.api, self)
        if dialog.exec():
            self._update_cookie_badge()
            self.toast("Cookies сохранены")
            self.refresh()

    def export_subject(self, subject_id: int) -> None:
        subject = db.get_subject(subject_id)
        if not subject:
            return
        safe_title = "".join(
            char if char.isalnum() or char in " -_" else "_" for char in subject["title"]
        ).strip() or f"subject_{subject_id}"
        path, _filter = QFileDialog.getSaveFileName(
            self, "Экспорт предмета", f"{safe_title}.json", "JSON (*.json)"
        )
        if not path:
            return
        rows = db.all_tests(subject_id)
        payload = {
            "subject": {"id": subject_id, "title": subject["title"]},
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tests": [
                {
                    "id": row["id"],
                    "kbs_id": row["kbs_id"],
                    "type": row["type"],
                    "title": row["title"],
                    "payload": json.loads(row["payload_json"]),
                }
                for row in rows
            ],
        }
        try:
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            self.toast(f"Не удалось сохранить файл: {exc}", error=True)
            return
        self.toast(f"Экспортировано {len(rows)} тестов")

    def _export_current(self) -> None:
        subject_id = self._current_subject_id()
        if subject_id is None:
            self.toast("Сначала выберите предмет", error=True)
            return
        self.export_subject(subject_id)

    def delete_subject(self, subject_id: int) -> None:
        subject = db.get_subject(subject_id)
        if not subject:
            return
        answer = QMessageBox.question(
            self,
            "Удалить из кэша",
            f"Удалить «{subject['title']}» и все его тесты из локального кэша?\n"
            "На сервере ничего не изменится.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        db.delete_subject(subject_id)
        self.toast("Предмет удалён из кэша")
        self.refresh()
        if self.subject_page.subject_id == subject_id:
            if db.list_subjects():
                self.navigate(f"subject:{db.list_subjects()[0]['id']}")
            else:
                self.navigate("dash")
        elif self.kbs_page.kbs_id and not db.get_kbs(self.kbs_page.kbs_id) or self.test_page.test_id and not db.get_test(self.test_page.test_id):
            self.navigate("dash")

    def delete_kbs(self, kbs_id: int) -> None:
        kbs = db.get_kbs(kbs_id)
        if not kbs:
            return
        answer = QMessageBox.question(
            self,
            "Удалить раздел",
            f"Удалить «{kbs.get('title') or kbs_id}» и его тесты из кэша?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        db.delete_kbs(kbs_id)
        self.toast("Раздел удалён из кэша")
        self.refresh()
        if self.kbs_page.kbs_id == kbs_id:
            self.navigate(f"subject:{kbs['subject_id']}")

    def clear_cache_dialog(self) -> None:
        stats = db.overall_stats()
        answer = QMessageBox.question(
            self,
            "Очистить кэш",
            f"Удалить все данные ({stats['subjects']} предметов, {stats['total']} тестов)?\n"
            "Cookies останутся.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        db.clear_cache()
        clear_cache()
        self.toast("Кэш очищен")
        self.refresh()
        self.navigate("dash")

    def reset_layout(self) -> None:
        self.settings.remove("window/geometry")
        self.settings.remove("window/state")
        self.settings.remove("window/splitter")
        self.settings.remove("sidebar/expanded")
        self.resize(QSize(1360, 880))
        self.splitter.setSizes([288, 1072])
        self.log_dock.setVisible(False)
        self.refresh()
        self.toast("Настройки окна сброшены")

    def toggle_log(self, checked: bool | None = None) -> None:
        if checked is None:
            self.log_dock.setVisible(not self.log_dock.isVisible())
        else:
            self.log_dock.setVisible(bool(checked))
        self.act_log.setChecked(self.log_dock.isVisible())

    def _append_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log.appendPlainText(f"[{stamp}] {message}")

    def show_help(self) -> None:
        HelpDialog(self).exec()

    def show_about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("О программе")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(about_text())
        box.setIconPixmap(icons.pixmap(APP_ICON, self._pal.accent, 42))
        box.exec()

    def show_payload(self, title: str, payload: Any) -> None:
        JsonDialog(title, payload, self).exec()

    def copy_text(self, text: str, message: str = "Скопировано") -> None:
        QGuiApplication.clipboard().setText(text)
        self.toast(message)

    def confirm_send(self, details: TestDetails, answer: list, lesson_id: int, spent: int) -> bool:
        dialog = SendDialog(details, answer, lesson_id, spent, self)
        return bool(dialog.exec())

    def show_api_error(self, exc: Exception) -> None:
        title = getattr(exc, "title", "Ошибка")
        message = str(exc)
        self.toast(f"{title}: {message}", error=True)
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setText(message)
        if isinstance(exc, AuthError):
            box.setInformativeText("Импортируйте свежие cookies и повторите попытку.")
            open_button = box.addButton("Импорт cookies", QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if box.clickedButton() is open_button:
                self.import_cookies()
            return
        box.exec()

    def show_answer_result(self, payload: Any) -> None:
        ResultDialog(payload, self).exec()

    def toast(self, message: str, *, error: bool = False) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            f"color: {self._pal.danger};" if error else ""
        )
        if not hasattr(self, "_toast_timer"):
            self._toast_timer = QTimer(self)
            self._toast_timer.setSingleShot(True)
            self._toast_timer.timeout.connect(lambda: self.status_label.setStyleSheet(""))
        self._toast_timer.start(6000)


# --------------------------------------------------------------------------- #
def _to_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return None


def _parse_subject_url(text: str) -> tuple[int | None, int | None]:
    """Extract ``(subject_id, lesson_id)`` from a pasted URL or a bare id.

    Understands ``/subjects/<id>/`` and the practice URL pattern from the API
    reference: ``/lesson/<lessonId>/subjects/<subjectId>/practice/kbs/...``
    """
    link = parse_link(text)
    if link is None:
        return None, None
    return link.subject_id, link.lesson_id


def _state_key(data: tuple[str, int]) -> str:
    """Stable key for the expanded-state map (subjects keep their old keys)."""
    kind, item_id = data
    if kind == "course":
        return f"c{item_id}"
    if kind == "lesson":
        return f"l{item_id}"
    return str(item_id)


def _extract_subject_id(text: str) -> int | None:
    """Backwards compatible helper (subject id only)."""
    return _parse_subject_url(text)[0]


def _load_lesson_hints() -> dict[str, int]:
    raw = QSettings().value("lesson/hints", "")
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    hints: dict[str, int] = {}
    for key, value in data.items():
        try:
            hints[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return hints


def _load_expanded_state() -> dict[str, bool]:
    raw = QSettings().value("sidebar/expanded", "")
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): bool(value) for key, value in data.items()}


def _short_time(timestamp: int) -> str:
    if not timestamp:
        return "никогда"
    return time.strftime("%d.%m %H:%M", time.localtime(timestamp))


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def _app_icon() -> QIcon:
    result = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        result.addPixmap(icons.pixmap(APP_ICON, "#4a63dd", size, ratio=1.0))
    return result
