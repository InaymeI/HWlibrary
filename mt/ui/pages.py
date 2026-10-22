"""Application pages: dashboard, subject, knowledge base, test and search."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import config, db, icons
from ..answers import answer_preview, build_answer
from ..models import TestDetails, relative_time, test_details, test_title
from ..theme import Palette
from ..widgets import (
    AutoHeightTextBrowser,
    Badge,
    EmptyState,
    FlowLayout,
    ListRow,
    SearchField,
    SectionTitle,
    StatCard,
    ghost_button,
    label,
    primary_button,
    secondary_button,
)

TYPE_NAMES = {
    "type1": "один ответ",
    "type2": "несколько ответов",
    "type3": "ввод текста",
    "type4": "ввод текста",
    "type5": "сопоставление",
}


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            clear_layout(child)
            child.deleteLater()


class NumericItem(QTableWidgetItem):
    """Table cell that sorts numerically (Qt compares text by default)."""

    def __init__(self, value: int) -> None:
        super().__init__()
        self.setData(Qt.ItemDataRole.DisplayRole, int(value))
        self.setData(Qt.ItemDataRole.UserRole, int(value))
        self.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def __lt__(self, other: QTableWidgetItem) -> bool:  # noqa: D105
        left = self.data(Qt.ItemDataRole.UserRole)
        right = other.data(Qt.ItemDataRole.UserRole)
        if isinstance(left, int) and isinstance(right, int):
            return left < right
        return str(self.text()) < str(other.text())


def fmt_int(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


class Breadcrumb(QLabel):
    navigate = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setProperty("role", "muted")
        self.linkActivated.connect(self.navigate.emit)

    def set_path(self, items: Iterable[tuple[str, str | None]]) -> None:
        parts = []
        for text, href in items:
            if href:
                parts.append(f'<a href="{href}" style="color:#8b93a1;text-decoration:none;">{text}</a>')
            else:
                parts.append(f'<span>{text}</span>')
        self.setText("  ›  ".join(parts))


class PageHeader(QFrame):
    navigate = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pageHeader")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        outer.setSpacing(6)

        self.breadcrumb = Breadcrumb()
        self.breadcrumb.navigate.connect(self.navigate)
        outer.addWidget(self.breadcrumb)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.title = label("", "h1")
        self.title.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        row.addWidget(self.title, 0, Qt.AlignmentFlag.AlignVCenter)

        self.badge_box = QHBoxLayout()
        self.badge_box.setSpacing(6)
        row.addLayout(self.badge_box)
        row.addStretch(1)

        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        row.addLayout(self.actions)
        outer.addLayout(row)

        self.subtitle = label("", "muted", wrap=True)
        outer.addWidget(self.subtitle)

        self._badges: list[Badge] = []

    def set_content(
        self,
        title: str,
        subtitle: str = "",
        breadcrumb: Iterable[tuple[str, str | None]] = (),
        badges: Iterable[tuple[str, str]] = (),
        actions: Iterable[QWidget] = (),
    ) -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.subtitle.setVisible(bool(subtitle))
        crumb_items = list(breadcrumb)
        self.breadcrumb.set_path(crumb_items)
        self.breadcrumb.setVisible(bool(crumb_items))

        for badge in self._badges:
            badge.setParent(None)
            badge.deleteLater()
        self._badges.clear()
        for text, variant in badges:
            badge = Badge(text, variant)
            self.badge_box.addWidget(badge)
            self._badges.append(badge)

        clear_layout(self.actions)
        for widget in actions:
            self.actions.addWidget(widget)


class ScrollPage(QWidget):
    """Page with a fixed header and a scrollable body."""

    navigate = pyqtSignal(str)

    def __init__(self, main, actions_title: str = "") -> None:
        super().__init__()
        self.main = main
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = PageHeader()
        self.header.navigate.connect(self.navigate)
        layout.addWidget(self.header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)

        self.content = QWidget()
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(24, 20, 24, 28)
        self.body.setSpacing(16)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

    def scroll_to_top(self) -> None:
        self.scroll.verticalScrollBar().setValue(0)

    def apply_theme(self, palette: Palette) -> None:
        """Hook for subclasses that paint theme dependent HTML."""


class DashboardPage(ScrollPage):
    def __init__(self, main) -> None:
        super().__init__(main)
        self.navigate.connect(main.navigate)
        self.refresh()

    def apply_theme(self, palette: Palette) -> None:
        self.refresh()

    def refresh(self) -> None:
        clear_layout(self.body)
        stats = db.overall_stats()
        self.header.set_content(
            "Библиотека заданий",
            "Локальный кэш заданий MaximumTest: поиск, просмотр и автопроверка ответов.",
            breadcrumb=[("Библиотека", None)],
        )

        cards = QHBoxLayout()
        cards.setSpacing(12)
        has_data = stats["subjects"] > 0
        palette = self.main.theme_palette()
        for caption, value, icon_name in (
            ("предметов в кэше", fmt_int(stats["subjects"]), "book"),
            ("тестов", fmt_int(stats["total"]), "layers"),
            ("с готовым ответом", fmt_int(stats["supported"]), "check"),
            ("последнее обновление", relative_time(stats["updated_at"]), "clock"),
        ):
            card = StatCard(caption, value, icon_name)
            card.set_theme(palette)
            cards.addWidget(card)
        self.body.addLayout(cards)

        if not has_data:
            self._build_getting_started()
            return

        self._build_quick_actions()
        self._build_recent(stats)

    # ------------------------------------------------------------------ #
    def _build_getting_started(self) -> None:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(14)

        layout.addWidget(label("Начните с трёх шагов", "h2"))
        layout.addWidget(label(
            "Приложение не хранит пароль: доступ к заданиям выполняется через cookies вашей "
            "сессии на education.maximumtest.ru.",
            "muted",
            wrap=True,
        ))

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(14)
        steps = [
            ("1", "Импортируйте cookies", "Войдите на сайт, скопируйте Cookie из DevTools и вставьте его сюда.",
             primary_button("Импорт cookies", "cookie"), self.main.import_cookies),
            ("2", "Добавьте предмет", "Вставьте ссылку вида /subjects/1234/ или просто ID предмета.",
             secondary_button("Добавить предмет", "plus"), self.main.add_subject_dialog),
            ("3", "Синхронизируйте", "Задания загрузятся в локальный кэш и станут доступны офлайн.",
             secondary_button("Синхронизировать всё", "refresh"), self.main.sync_all),
        ]
        for row, (number, title, description, _button, _slot) in enumerate(steps):
            chip = QLabel(number)
            chip.setFixedSize(28, 28)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setProperty("role", "badge")
            chip.setProperty("variant", "accent")
            chip.setStyleSheet("font-size: 13px; font-weight: 700;")
            grid.addWidget(chip, row, 0, Qt.AlignmentFlag.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(label(title, "h3"))
            text.addWidget(label(description, "muted", wrap=True))
            grid.addLayout(text, row, 1)
            button = _button
            button.clicked.connect(_slot)
            grid.addWidget(button, row, 2, Qt.AlignmentFlag.AlignVCenter)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.body.addWidget(card)

        self.body.addWidget(EmptyState(
            "search",
            "Кэш пока пуст",
            "После синхронизации здесь появится список предметов, разделов и заданий "
            "с мгновенным поиском по всей базе.",
        ))
        self.body.addStretch(1)

    def _build_quick_actions(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        sync_button = primary_button("Синхронизировать всё", "refresh")
        sync_button.clicked.connect(self.main.sync_all)
        row.addWidget(sync_button)
        add_button = secondary_button("Добавить предмет", "plus")
        add_button.clicked.connect(self.main.add_subject_dialog)
        row.addWidget(add_button)
        all_button = secondary_button("Все тесты", "grid")
        all_button.clicked.connect(lambda: self.navigate.emit("all"))
        row.addWidget(all_button)
        row.addStretch(1)
        self.body.addLayout(row)

    def _build_recent(self, stats: dict[str, int]) -> None:
        self.body.addWidget(SectionTitle("Предметы", stats["subjects"]))
        subjects = sorted(
            db.list_subjects(),
            key=lambda row: row.get("fetched_at") or 0,
            reverse=True,
        )
        for subject in subjects[:6]:
            counts = db.subject_stats(subject["id"])
            row = ListRow(
                subject["title"] or f"Предмет #{subject['id']}",
                f"ID {subject['id']} · обновлено {relative_time(subject.get('fetched_at'))}",
                badges=[
                    (f"{fmt_int(counts['total'])} {plural(counts['total'], 'тест', 'теста', 'тестов')}", "muted"),
                    (f"{fmt_int(counts['supported'])} с ответом", "success" if counts["supported"] else "muted"),
                ],
                icon_name="book",
            )
            row.clicked.connect(lambda sid=subject["id"]: self.navigate.emit(f"subject:{sid}"))
            self.body.addWidget(row)
        if len(subjects) > 6:
            more = ghost_button(f"Ещё {len(subjects) - 6} — открыть все тесты", "grid")
            more.clicked.connect(lambda: self.navigate.emit("all"))
            self.body.addWidget(more, 0, Qt.AlignmentFlag.AlignLeft)
        self.body.addStretch(1)


class SubjectPage(ScrollPage):
    def __init__(self, main) -> None:
        super().__init__(main)
        self.navigate.connect(main.navigate)
        self.subject_id: int | None = None

    def show_subject(self, subject_id: int) -> None:
        self.subject_id = subject_id
        self.refresh()

    def refresh(self) -> None:
        if self.subject_id is None:
            return
        subject = db.get_subject(self.subject_id)
        clear_layout(self.body)
        if not subject:
            self.header.set_content("Предмет не найден", "", [("Библиотека", "dash")])
            self.body.addWidget(EmptyState(
                "alert", "Предмет удалён из кэша",
                "Вернитесь на главную и добавьте его снова.",
                [secondary_button("На главную", "arrow-left")],
            ))
            return
        counts = db.subject_stats(self.subject_id)
        title = subject["title"] or f"Предмет #{self.subject_id}"

        sync_button = primary_button("Синхронизировать", "refresh")
        sync_button.clicked.connect(lambda: self.main.sync_subject(self.subject_id))
        export_button = secondary_button("Экспорт JSON", "download")
        export_button.clicked.connect(lambda: self.main.export_subject(self.subject_id))
        delete_button = ghost_button("Удалить из кэша", "trash", color_key="danger")
        delete_button.clicked.connect(lambda: self.main.delete_subject(self.subject_id))

        self.header.set_content(
            title,
            f"ID {self.subject_id} · "
            f"{fmt_int(counts['kbs'])} {plural(counts['kbs'], 'раздел', 'раздела', 'разделов')} · "
            f"{fmt_int(counts['total'])} {plural(counts['total'], 'тест', 'теста', 'тестов')} · "
            f"обновлено {relative_time(subject.get('fetched_at'))}",
            breadcrumb=[("Библиотека", "dash"), (title, None)],
            badges=[
                (f"{fmt_int(counts['supported'])} с ответом", "success" if counts["supported"] else "muted"),
            ],
            actions=[sync_button, export_button, delete_button],
        )

        kbs_rows = db.tree_data()
        entries = next((items for subject, items in kbs_rows if subject["id"] == self.subject_id), [])
        if not entries:
            button = primary_button("Загрузить разделы", "refresh")
            button.clicked.connect(lambda: self.main.sync_subject(self.subject_id))
            self.body.addWidget(EmptyState(
                "layers",
                "Разделы ещё не загружены",
                "Синхронизируйте предмет, чтобы получить список разделов и заданий.",
                [button],
            ))
            return

        self.body.addWidget(SectionTitle("Разделы", len(entries)))
        for kbs, kbs_counts in entries:
            mode = config.mode_label(kbs.get("mode"))
            subtitle_bits = [f"Раздел #{kbs['id']}", f"режим: {mode}"]
            if kbs.get("lesson_id"):
                subtitle_bits.append(f"lesson {kbs['lesson_id']}")
            row = ListRow(
                kbs.get("title") or f"Раздел #{kbs['id']}",
                " · ".join(subtitle_bits),
                badges=[
                    (f"{fmt_int(kbs_counts['total'])} {plural(kbs_counts['total'], 'тест', 'теста', 'тестов')}", "muted"),
                    (f"{fmt_int(kbs_counts['supported'])} с ответом", "success" if kbs_counts["supported"] else "muted"),
                ],
                trailing=f"{kbs_counts['supported']}/{kbs_counts['total']}",
                icon_name="layers",
            )
            row.clicked.connect(lambda kid=kbs["id"]: self.navigate.emit(f"kbs:{kid}"))
            self.body.addWidget(row)
        self.body.addStretch(1)


class KbsPage(QWidget):
    """Knowledge base: a searchable table of its tests."""

    navigate = pyqtSignal(str)

    def __init__(self, main) -> None:
        super().__init__()
        self.main = main
        self.kbs_id: int | None = None
        self._tests: list[dict[str, Any]] = []
        self._filtered: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = PageHeader()
        self.header.navigate.connect(self.navigate)
        layout.addWidget(self.header)

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(24, 16, 24, 20)
        body.setSpacing(12)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.search = SearchField("Фильтр по названию или ID…")
        self.search.textChanged.connect(self._filter)
        controls.addWidget(self.search, 1)
        body.addLayout(controls)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Тип", "ID", "Задание", "Ответ"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(3, 190)
        self.table.cellDoubleClicked.connect(self._open_selected)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        body.addWidget(self.table, 1)

        footer = QHBoxLayout()
        self.count_label = label("", "muted")
        footer.addWidget(self.count_label)
        footer.addStretch(1)
        footer.addWidget(label("Двойной клик открывает задание · правый клик — быстрые действия", "muted"))
        body.addLayout(footer)

        layout.addWidget(content, 1)

    # ------------------------------------------------------------------ #
    def show_kbs(self, kbs_id: int) -> None:
        self.kbs_id = kbs_id
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.refresh()

    def apply_theme(self, palette: Palette) -> None:
        self.search.set_icon_color(palette.muted)
        self.refresh()

    def refresh(self) -> None:
        if self.kbs_id is None:
            return
        kbs = db.get_kbs(self.kbs_id)
        if not kbs:
            self.header.set_content("Раздел не найден", "", [("Библиотека", "dash")])
            self._tests, self._filtered = [], []
            self.table.setRowCount(0)
            return
        subject = db.get_subject(kbs["subject_id"]) or {}
        counts = db.kbs_stats(self.kbs_id)
        title = kbs.get("title") or f"Раздел #{self.kbs_id}"
        self._tests = db.list_tests(self.kbs_id)
        self._filtered = list(self._tests)

        sync_button = secondary_button("Обновить предмет", "refresh")
        sync_button.clicked.connect(lambda: self.main.sync_subject(kbs["subject_id"]))
        self.header.set_content(
            title,
            f"Предмет: {subject.get('title') or kbs['subject_id']}"
            f" · режим: {config.mode_label(kbs.get('mode'))}"
            + (f" · lesson {kbs['lesson_id']}" if kbs.get("lesson_id") else ""),
            breadcrumb=[
                ("Библиотека", "dash"),
                (subject.get("title") or "Предмет", f"subject:{kbs['subject_id']}"),
                (title, None),
            ],
            badges=[
                (f"{fmt_int(counts['total'])} {plural(counts['total'], 'тест', 'теста', 'тестов')}", "muted"),
                (f"{fmt_int(counts['supported'])} с ответом", "success" if counts["supported"] else "muted"),
            ],
            actions=[sync_button],
        )
        self._fill()
        self.search.setVisible(bool(self._tests))

    # ------------------------------------------------------------------ #
    def _sort_state(self) -> tuple[int, Qt.SortOrder]:
        header = self.table.horizontalHeader()
        return header.sortIndicatorSection(), header.sortIndicatorOrder()

    def _fill(self) -> None:
        rows = self._filtered
        sort_column, sort_order = self._sort_state()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            supported = config.is_supported(row.get("type"))
            type_item = QTableWidgetItem(row.get("type") or "—")
            type_item.setForeground(QColor("#4a63dd" if supported else "#8b93a1"))
            font = type_item.font()
            font.setBold(True)
            type_item.setFont(font)
            self.table.setItem(index, 0, type_item)

            self.table.setItem(index, 1, NumericItem(int(row["id"])))

            title_item = QTableWidgetItem(test_title(row))
            title_item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            if not supported:
                title_item.setForeground(QColor("#8b93a1"))
            self.table.setItem(index, 2, title_item)

            answer = "—"
            if supported:
                try:
                    answer = answer_preview(test_details(row), limit=2)
                except Exception:  # noqa: BLE001 - preview must never break the table
                    answer = "—"
            answer_item = QTableWidgetItem(answer)
            answer_item.setForeground(QColor("#59616e"))
            self.table.setItem(index, 3, answer_item)
        self.table.setSortingEnabled(True)
        if self.table.rowCount():
            self.table.sortItems(sort_column, sort_order)

        total = len(self._tests)
        shown = len(rows)
        if shown != total:
            self.count_label.setText(f"Показано {shown} из {total}")
        else:
            self.count_label.setText(f"{fmt_int(total)} {plural(total, 'задание', 'задания', 'заданий')}")

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        if not needle:
            self._filtered = list(self._tests)
        else:
            self._filtered = [
                row for row in self._tests
                if needle in str(row["id"])
                or needle in (row.get("title") or "").lower()
                or needle in (row.get("type") or "")
            ]
        self._fill()

    def _selected_test_id(self) -> int | None:
        item = self.table.currentItem()
        if item is None:
            return None
        id_item = self.table.item(item.row(), 1)
        return int(id_item.data(Qt.ItemDataRole.UserRole)) if id_item else None

    def _open_selected(self, row: int, _column: int = 0) -> None:
        id_item = self.table.item(row, 1)
        if id_item:
            self.navigate.emit(f"test:{int(id_item.data(Qt.ItemDataRole.UserRole))}")

    def _context_menu(self, point) -> None:
        test_id = self._selected_test_id()
        if test_id is None:
            return
        menu = QMenu(self)
        menu.addAction(icons.icon("external", "#59616e", 16), "Открыть задание",
                       lambda: self.navigate.emit(f"test:{test_id}"))
        menu.addAction(icons.icon("copy", "#59616e", 16), "Копировать ID",
                       lambda: QGuiApplication.clipboard().setText(str(test_id)))
        row = db.get_test(test_id)
        if row:
            action = menu.addAction(
                icons.icon("check", "#59616e", 16),
                "Копировать ответ",
                lambda: QGuiApplication.clipboard().setText(
                    json.dumps(build_answer(test_details(row)), ensure_ascii=False)
                ),
            )
            action.setEnabled(config.is_supported(row.get("type")))
        menu.exec(self.table.viewport().mapToGlobal(point))


class TestPage(ScrollPage):
    def __init__(self, main) -> None:
        super().__init__(main)
        self.navigate.connect(main.navigate)
        self.test_id: int | None = None
        self.details: TestDetails | None = None
        self.row: dict[str, Any] | None = None
        self.kbs: dict[str, Any] | None = None
        self._siblings: list[int] = []
        self._opened_at = time.monotonic()
        self._palette = main.theme_palette()
        self._answer: list | None = None

    # ------------------------------------------------------------------ #
    def show_test(self, test_id: int) -> None:
        row = db.get_test(test_id)
        self.test_id = test_id
        self.row = row
        self.details = test_details(row) if row else None
        self._opened_at = time.monotonic()
        self.kbs = db.get_kbs(row["kbs_id"]) if row else None
        if row:
            self._siblings = [int(item["id"]) for item in db.list_tests(row["kbs_id"])]
        else:
            self._siblings = []
        self.refresh()

    def apply_theme(self, palette: Palette) -> None:
        self._palette = palette
        self.refresh()

    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        if self.test_id is None:
            return
        clear_layout(self.body)
        details, row = self.details, self.row
        if not row or not details:
            self.header.set_content("Задание не найдено", "", [("Библиотека", "dash")])
            self.body.addWidget(EmptyState("alert", "Задание отсутствует в кэше", ""))
            return

        subject = db.get_subject(row["subject_id"]) or {}
        kbs = db.get_kbs(row["kbs_id"]) or {}
        index = self._siblings.index(self.test_id) if self.test_id in self._siblings else -1
        position = f"{index + 1} из {len(self._siblings)}" if index >= 0 else ""

        prev_button = secondary_button("Назад", "chevron-left")
        prev_button.clicked.connect(self.go_previous)
        prev_button.setEnabled(index > 0)
        next_button = secondary_button("Далее", "chevron-right")
        next_button.clicked.connect(self.go_next)
        next_button.setEnabled(0 <= index < len(self._siblings) - 1)
        json_button = secondary_button("JSON", "external")
        json_button.clicked.connect(self.show_json)

        badges = [
            (TYPE_NAMES.get(details.type, details.type or "тип неизвестен"), "accent"),
            (f"ID {details.id}", "muted"),
            ("поддерживается" if details.supported else "только просмотр",
             "success" if details.supported else "warning"),
        ]
        self.header.set_content(
            details.title,
            f"{subject.get('title') or 'Предмет'} · {kbs.get('title') or 'Раздел'} · {position}".strip(" ·"),
            breadcrumb=[
                ("Библиотека", "dash"),
                (subject.get("title") or "Предмет", f"subject:{row['subject_id']}"),
                (kbs.get("title") or f"Раздел #{row['kbs_id']}", f"kbs:{row['kbs_id']}"),
                (f"#{details.id}", None),
            ],
            badges=badges,
            actions=[prev_button, next_button, json_button],
        )

        self._build_question(details)
        self._build_answers(details)
        self._build_answer_card(details, row)
        self._build_meta(details, row)
        self.body.addStretch(1)

    # ------------------------------------------------------------------ #
    def _card(self, title: str, subtitle: str = "") -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(label(title, "h2"))
        if subtitle:
            head.addWidget(label(subtitle, "muted"))
        head.addStretch(1)
        layout.addLayout(head)
        return card, layout

    def _build_question(self, details: TestDetails) -> None:
        card, layout = self._card("Условие")
        # "Копировать текст" lives in the card header: keeps the card compact.
        copy_question = ghost_button("Копировать текст", "copy", color_key="muted")
        copy_question.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(
                details.question_text or details.title
            )
        )
        header = layout.itemAt(0).layout()
        if header is not None:
            header.addWidget(copy_question, 0, Qt.AlignmentFlag.AlignRight)

        browser = AutoHeightTextBrowser()
        browser.setOpenExternalLinks(True)
        document = browser.document()
        document.setDefaultStyleSheet(
            f"body {{ color: {self._palette.text}; font-size: 14px; line-height: 150%; }}"
            f"p {{ margin: 6px 0; }} a {{ color: {self._palette.accent}; }}"
            "code, pre { font-family: monospace; }"
            "img { max-width: 100%; }"
        )
        browser.setHtml(details.question_html or "<p><i>Условие не сохранено в кэше.</i></p>")
        layout.addWidget(browser)
        # The browser is created before it is laid out; measure it once the
        # geometry is final so short conditions do not leave a tall empty box.
        QTimer.singleShot(0, browser.fit_to_content)
        self.body.addWidget(card)

    def _build_answers(self, details: TestDetails) -> None:
        if not details.options:
            return
        card, layout = self._card("Варианты ответов",
                                  "зелёным отмечены правильные" if details.has_right_answers else "")
        right_ids = {option.id for option in details.right_options}
        letters = "АБВГДЕЖЗИКЛМНОПРСТУФ"
        for position, option in enumerate(details.options):
            is_right = option.id in right_ids
            row = QFrame()
            row.setObjectName("cardFlat")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 10, 12, 10)
            row_layout.setSpacing(12)

            chip = QLabel(letters[position] if position < len(letters) else str(position + 1))
            chip.setFixedSize(26, 26)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            palette = self._palette
            chip.setStyleSheet(
                f"border-radius: 13px; font-weight: 700;"
                f"background: {palette.success_soft if is_right else palette.surface_alt};"
                f"color: {palette.success if is_right else palette.text_secondary};"
            )
            row_layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop)

            text_box = QVBoxLayout()
            text_box.setSpacing(3)
            option_text = option.text or "—"
            if details.type == "type5" and option.variants:
                option_text = option.variants[0]
            text_label = label(option_text, wrap=True)
            text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if is_right:
                text_label.setStyleSheet(f"color: {palette.success}; font-weight: 600;")
            text_box.addWidget(text_label)
            if len(option.variants) > 1:
                text_box.addWidget(label(
                    "варианты: " + " · ".join(option.variants), "muted", wrap=True
                ))
            row_layout.addLayout(text_box, 1)

            if is_right:
                check = QLabel()
                check.setPixmap(icons.pixmap("check", palette.success, 16))
                row_layout.addWidget(check, 0, Qt.AlignmentFlag.AlignTop)
            row_layout.addWidget(label(f"#{option.id}", "muted"))
            layout.addWidget(row)
        self.body.addWidget(card)

    def _build_answer_card(self, details: TestDetails, row: dict[str, Any]) -> None:
        card, layout = self._card("Автоответ")
        if not details.supported:
            layout.addWidget(label(
                f"Тип {details.type or '—'} не поддерживается: ответ можно посмотреть, но не отправить.",
                "muted", wrap=True,
            ))
            self.body.addWidget(card)
            return
        if not details.has_right_answers:
            layout.addWidget(label(
                "В сохранённом ответе сервера нет правильных вариантов — возможно, задание "
                "не загрузилось полностью. Обновите предмет.",
                "muted", wrap=True,
            ))
            self.body.addWidget(card)
            return

        try:
            # Build once: the shown chips must be exactly what will be sent
            # (type5 has several acceptable spellings to choose from).
            answer = self._answer = build_answer(details)
        except Exception as exc:  # noqa: BLE001
            self._answer = None
            layout.addWidget(label(f"Не удалось собрать ответ: {exc}", "muted", wrap=True))
            self.body.addWidget(card)
            return

        chips = QWidget()
        flow = FlowLayout(chips, spacing=6)
        palette = self._palette
        for value in answer:
            chip = QLabel(str(value))
            chip.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            chip.setStyleSheet(
                f"background: {palette.accent_soft}; color: {palette.accent};"
                "border-radius: 8px; padding: 5px 10px; font-weight: 600;"
            )
            flow.addWidget(chip)
        layout.addWidget(chips)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        copy_button = secondary_button("Копировать ответ", "copy")
        copy_button.clicked.connect(
            lambda: self.main.copy_text(json.dumps(answer, ensure_ascii=False), "Ответ скопирован")
        )
        buttons.addWidget(copy_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        warning = label(
            "Отправка ответа изменит результат в личном кабинете. Lesson ID берётся из данных "
            "задания; проверьте его перед отправкой.",
            "muted", wrap=True,
        )
        layout.addWidget(warning)

        send_row = QHBoxLayout()
        send_row.setSpacing(8)
        lesson_caption = label("lessonId", "muted")
        send_row.addWidget(lesson_caption)
        self.lesson_input = QSpinBox()
        self.lesson_input.setRange(0, 2_147_483_647)
        # lessonId comes from the tree; when it is missing the reference
        # suggests the practice URL (/lesson/<id>/subjects/<id>/practice/kbs/…)
        fallback_lesson = (self.kbs or {}).get("lesson_id") or 0
        hint = self.main.lesson_hint(row.get("subject_id"))
        self.lesson_input.setValue(details.lesson_id or fallback_lesson or hint or 0)
        self.lesson_input.setMinimumWidth(150)
        send_row.addWidget(self.lesson_input)
        send_row.addStretch(1)
        self.send_button = QPushButton("Отправить ответ")
        self.send_button.setProperty("variant", "primary")
        self.send_button.setIcon(icons.icon("send", "#ffffff", 16))
        self.send_button.setIconSize(QSize(16, 16))
        self.send_button.clicked.connect(self.send_answer)
        send_row.addWidget(self.send_button)
        layout.addLayout(send_row)
        self.body.addWidget(card)

    def _build_meta(self, details: TestDetails, row: dict[str, Any]) -> None:
        card = QFrame()
        card.setObjectName("cardFlat")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(18)
        cached = datetime.fromtimestamp(row.get("fetched_at") or 0).strftime("%d.%m.%Y %H:%M")
        subject = db.get_subject(row["subject_id"]) or {}
        kbs = self.kbs or {}
        for caption, value in (
            ("Предмет", subject.get("title") or f"#{row['subject_id']}"),
            ("Раздел", kbs.get("title") or f"#{row['kbs_id']}"),
            ("Сохранено", cached),
            ("Правильных ответов", str(len(details.right_options))),
        ):
            box = QVBoxLayout()
            box.setSpacing(1)
            box.addWidget(label(caption, "muted"))
            box.addWidget(label(value))
            layout.addLayout(box)
        layout.addStretch(1)
        self.body.addWidget(card)

    # ------------------------------------------------------------------ #
    def go_previous(self) -> None:
        if self.test_id in self._siblings:
            index = self._siblings.index(self.test_id)
            if index > 0:
                self.navigate.emit(f"test:{self._siblings[index - 1]}")

    def go_next(self) -> None:
        if self.test_id in self._siblings:
            index = self._siblings.index(self.test_id)
            if index < len(self._siblings) - 1:
                self.navigate.emit(f"test:{self._siblings[index + 1]}")

    def show_json(self) -> None:
        if not self.details:
            return
        self.main.show_payload(self.details.title, self.details.raw)

    def send_answer(self) -> None:
        details, row = self.details, self.row
        if not details or not row:
            return
        lesson_id = self.lesson_input.value() if hasattr(self, "lesson_input") else 0
        if lesson_id <= 0:
            self.main.toast("Укажите lessonId перед отправкой", error=True)
            return
        answer = self._answer if self._answer is not None else build_answer(details)
        low, high = config.SPENT_TIME_RANGE
        spent = int(max(low, min(time.monotonic() - self._opened_at, high)))
        if not self.main.confirm_send(details, answer, lesson_id, spent):
            return
        try:
            result = self.main.api.post_answer(
                row["subject_id"], lesson_id, details.id, answer, spent
            )
        except Exception as exc:  # noqa: BLE001 - show everything to the user
            self.main.show_api_error(exc)
            return
        self.main.show_answer_result(result)


class AllTestsPage(QWidget):
    navigate = pyqtSignal(str)

    PAGE_SIZE = 800

    def __init__(self, main) -> None:
        super().__init__()
        self.main = main
        self._rows: list[dict[str, Any]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = PageHeader()
        self.header.navigate.connect(self.navigate)
        layout.addWidget(self.header)
        self.header.set_content(
            "Все тесты",
            "Поиск по всей локальной базе. Двойной клик открывает задание.",
            breadcrumb=[("Библиотека", "dash"), ("Все тесты", None)],
        )

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(24, 16, 24, 20)
        body.setSpacing(12)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.search = SearchField("Название, ID, тип или раздел…")
        self.search.textChanged.connect(self._schedule_reload)
        controls.addWidget(self.search, 1)

        self.subject_combo = QComboBox()
        self.subject_combo.setMinimumWidth(170)
        self.subject_combo.currentIndexChanged.connect(lambda _: self.reload())
        controls.addWidget(self.subject_combo)

        self.type_combo = QComboBox()
        self.type_combo.addItem("Все типы", "all")
        self.type_combo.addItem("С готовым ответом", "supported")
        self.type_combo.addItem("Без ответа", "other")
        for ttype in sorted(config.SUPPORTED_TYPES):
            self.type_combo.addItem(ttype, ttype)
        self.type_combo.currentIndexChanged.connect(lambda _: self.reload())
        controls.addWidget(self.type_combo)
        body.addLayout(controls)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ID", "Предмет", "Раздел", "Тип", "Задание"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 80)
        self.table.setColumnWidth(1, 190)
        self.table.setColumnWidth(2, 220)
        self.table.setColumnWidth(3, 140)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        body.addWidget(self.table, 1)

        footer = QHBoxLayout()
        self.count_label = label("", "muted")
        footer.addWidget(self.count_label)
        footer.addStretch(1)
        hint = label("Enter — открыть · Ctrl+C — копировать ID", "muted")
        footer.addWidget(hint)
        body.addLayout(footer)

        layout.addWidget(content, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self.reload)

        QShortcut(QKeySequence("Return"), self.table,
                  activated=lambda: self._open_row(self.table.currentRow(), 0))

    # ------------------------------------------------------------------ #
    def _schedule_reload(self) -> None:
        self._timer.start()

    def apply_theme(self, palette: Palette) -> None:
        self.search.set_icon_color(palette.muted)
        self.reload()

    def refresh_subjects(self) -> None:
        current = self.subject_combo.currentData()
        self.subject_combo.blockSignals(True)
        self.subject_combo.clear()
        self.subject_combo.addItem("Все предметы", None)
        for subject in db.list_subjects():
            self.subject_combo.addItem(subject["title"] or f"#{subject['id']}", subject["id"])
        index = self.subject_combo.findData(current)
        self.subject_combo.setCurrentIndex(index if index >= 0 else 0)
        self.subject_combo.blockSignals(False)

    def _sort_state(self) -> tuple[int, Qt.SortOrder]:
        header = self.table.horizontalHeader()
        return header.sortIndicatorSection(), header.sortIndicatorOrder()

    def reload(self) -> None:
        sort_column, sort_order = self._sort_state()
        search = self.search.text().strip()
        subject_id = self.subject_combo.currentData()
        type_filter = self.type_combo.currentData() or "all"
        rows = db.query_tests(
            search=search,
            subject_id=subject_id,
            type_filter=type_filter,
            limit=self.PAGE_SIZE,
            include_payload=False,
        )
        self._rows = rows
        total = db.query_tests_count(search=search, subject_id=subject_id, type_filter=type_filter)

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            supported = config.is_supported(row.get("type"))
            self.table.setItem(index, 0, NumericItem(int(row["id"])))

            self.table.setItem(index, 1, QTableWidgetItem(row.get("subject_title") or "—"))

            kbs_caption = row.get("kbs_title") or f"Раздел #{row['kbs_id']}"
            kbs_item = QTableWidgetItem(kbs_caption)
            kbs_item.setToolTip(f"ID {row['kbs_id']} · {config.mode_label(row.get('kbs_mode'))}")
            self.table.setItem(index, 2, kbs_item)

            type_item = QTableWidgetItem(row.get("type") or "—")
            type_item.setForeground(QColor("#4a63dd" if supported else "#8b93a1"))
            font = type_item.font()
            font.setBold(True)
            type_item.setFont(font)
            self.table.setItem(index, 3, type_item)

            title_item = QTableWidgetItem(row.get("title") or f"Тест #{row['id']}")
            title_item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            if not supported:
                title_item.setForeground(QColor("#8b93a1"))
            self.table.setItem(index, 4, title_item)
        self.table.setSortingEnabled(True)
        if self.table.rowCount():
            self.table.sortItems(sort_column, sort_order)

        shown = len(rows)
        if total > shown:
            self.count_label.setText(
                f"Показаны первые {fmt_int(shown)} из {fmt_int(total)} — уточните поиск"
            )
        else:
            self.count_label.setText(
                f"{fmt_int(total)} {plural(total, 'задание', 'задания', 'заданий')}"
            )

    # ------------------------------------------------------------------ #
    def _row_test_id(self, row: int) -> int | None:
        item = self.table.item(row, 0)
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _open_row(self, row: int, _column: int = 0) -> None:
        test_id = self._row_test_id(row)
        if test_id is not None:
            self.navigate.emit(f"test:{test_id}")

    def _context_menu(self, point) -> None:
        row = self.table.rowAt(point.y())
        test_id = self._row_test_id(row) if row >= 0 else None
        if test_id is None:
            return
        menu = QMenu(self)
        menu.addAction(icons.icon("external", "#59616e", 16), "Открыть задание",
                       lambda: self.navigate.emit(f"test:{test_id}"))
        menu.addAction(icons.icon("copy", "#59616e", 16), "Копировать ID",
                       lambda: QGuiApplication.clipboard().setText(str(test_id)))
        cached = db.get_test(test_id)
        if cached:
            action = menu.addAction(
                icons.icon("check", "#59616e", 16),
                "Копировать ответ",
                lambda: QGuiApplication.clipboard().setText(
                    json.dumps(build_answer(test_details(cached)), ensure_ascii=False)
                ),
            )
            action.setEnabled(config.is_supported(cached.get("type")))
        menu.exec(self.table.viewport().mapToGlobal(point))
