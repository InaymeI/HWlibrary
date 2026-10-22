"""Main window: sidebar navigation, toolbar, pages and sync orchestration."""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QRect, QSettings, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QActionGroup,
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
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
    TestPage,
)

APP_ICON = "book"
COUNT_ROLE = Qt.ItemDataRole.UserRole + 7


class SidebarTree(QTreeWidget):
    """Sidebar tree with whole-row rounded selection and its own chevrons.

    Qt paints a tree selection cell by cell, and the style paints a flat panel
    behind the branches; the selection state is cleared here and replaced with
    one rounded pill drawn across the whole row.  The expand arrows are painted
    by hand too, so every предмет / урок / домашка collapses on its own when
    its arrow is clicked, and the state is persisted.
    """

    CHEVRON = 16

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._branch_color = "#8c8c8c"
        self._hover_color = "#f3f3f3"
        self._selected_color = "#ececec"
        self.setMouseTracking(True)
        self.setExpandsOnDoubleClick(False)

    def set_branch_color(self, color: str) -> None:
        self._branch_color = color
        self.viewport().update()

    def set_row_colors(self, hover: str, selected: str) -> None:
        self._hover_color = hover
        self._selected_color = selected
        self.viewport().update()

    def drawRow(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        """One rounded pill for the whole row, arrow included."""
        option.state &= ~QStyle.StateFlag.State_Selected
        selected = self.currentIndex() == index
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._selected_color if selected else self._hover_color))
            pill = QRect(2, option.rect.top() + 2, self.viewport().width() - 8,
                         option.rect.height() - 4)
            painter.drawRoundedRect(pill, 9, 9)
            painter.restore()
        super().drawRow(painter, option, index)

    # ------------------------------------------------------------------ #
    def has_children(self, index) -> bool:
        return bool(index.isValid() and self.model().rowCount(index) > 0)

    def _depth(self, index) -> int:
        depth = 0
        parent = index.parent()
        while parent.isValid():
            depth += 1
            parent = parent.parent()
        return depth

    def branch_rect(self, index) -> QRect:
        """Clickable box of the expand arrow, left of the item text."""
        row = self.visualRect(index)
        if not row.isValid():
            return QRect()
        size = self.CHEVRON
        top = row.top() + max(0, (row.height() - size) // 2)
        return QRect(self._depth(index) * self.indentation() + 1, top, size, size)

    def toggle_index(self, index) -> None:
        item = self.itemFromIndex(index)
        if item is not None and item.childCount():
            item.setExpanded(not item.isExpanded())

    def drawBranches(self, painter: QPainter, rect: QRect, index) -> None:
        # No style-painted strip and no default arrows: one chevron per parent.
        if not self.has_children(index):
            return
        box = self.branch_rect(index)
        if not box.isValid():
            return
        name = "chevron-down" if self.isExpanded(index) else "chevron-right"
        pixmap = icons.pixmap(name, self._branch_color, box.width() - 2)
        painter.drawPixmap(box.x() + 1, box.y() + 1, pixmap)

    # ------------------------------------------------------------------ #
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            point = event.position().toPoint()
            index = self.indexAt(point)
            if self.has_children(index) and self.branch_rect(index).contains(point):
                self.toggle_index(index)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        point = event.position().toPoint()
        index = self.indexAt(point)
        over_arrow = self.has_children(index) and self.branch_rect(index).contains(point)
        self.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if over_arrow else Qt.CursorShape.ArrowCursor
        )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)


class SidebarDelegate(QStyledItemDelegate):
    """«Название …………… счётчик» in one column, painted by hand."""

    def __init__(self, tree: QTreeWidget) -> None:
        super().__init__(tree)
        self._tree = tree
        self._text = "#141414"
        self._secondary = "#575757"
        self._muted = "#8c8c8c"

    def set_palette(self, palette: Palette) -> None:
        self._text = palette.text
        self._secondary = palette.text_secondary
        self._muted = palette.muted
        self._tree.viewport().update()

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = option.rect.adjusted(2, 2, -4, -2)
        current = self._tree.currentIndex()
        selected = bool(current.isValid() and current == index)

        font = index.data(Qt.ItemDataRole.FontRole) or option.font
        if not isinstance(font, QFont):
            font = option.font
        painter.setFont(font)
        label_rect = rect.adjusted(9, 0, -10, 0)

        count = str(index.data(COUNT_ROLE) or "")
        if count:
            metrics = QFontMetrics(font)
            count_width = metrics.horizontalAdvance(count)
            count_rect = label_rect.adjusted(
                label_rect.width() - count_width, 0, 0, 0
            )
            painter.setPen(QColor(self._secondary if selected else self._muted))
            painter.drawText(
                count_rect,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                count,
            )
            label_rect.setRight(count_rect.left() - 8)

        brush = index.data(Qt.ItemDataRole.ForegroundRole)
        if selected:
            color = QColor(self._text)
        elif isinstance(brush, QBrush) and brush.style() != Qt.BrushStyle.NoBrush:
            color = brush.color()
        else:
            color = QColor(self._secondary)
        painter.setPen(color)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        metrics = QFontMetrics(font)
        painter.drawText(
            label_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(text, Qt.TextElideMode.ElideRight, label_rect.width()),
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        return QSize(0, 30)




class Sidebar(QWidget):
    """Left navigation: brand, quick links, subject tree, footer stats."""

    navigate = pyqtSignal(str)

    def __init__(self, main: MainWindow) -> None:
        super().__init__()
        self.main = main
        self._tree_collapsed = bool(
            QSettings().value("sidebar/collapsed", False, type=bool)
        )
        self.setMinimumWidth(248)
        self.setMaximumWidth(430)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("sidebarHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 18, 16, 8)
        header_layout.setSpacing(10)
        brand_box = QVBoxLayout()
        brand_box.setSpacing(1)
        brand_box.addWidget(label("MaximumTest", "h2"))
        brand_box.addWidget(label("локальный кэш заданий", "muted"))
        header_layout.addLayout(brand_box, 1)
        layout.addWidget(header)

        search_box = QWidget()
        search_layout = QHBoxLayout(search_box)
        search_layout.setContentsMargins(14, 2, 14, 6)
        self.search = QLineEdit()
        self.search.setObjectName("sidebarSearch")
        self.search.setPlaceholderText("Поиск по заданиям…")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self._search_triggered)
        search_layout.addWidget(self.search)
        layout.addWidget(search_box)

        section = QWidget()
        section_layout = QHBoxLayout(section)
        section_layout.setContentsMargins(12, 6, 12, 2)
        section_layout.setSpacing(4)
        self.section_label = QToolButton()
        self.section_label.setObjectName("sectionToggle")
        self.section_label.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.section_label.setText("ПРЕДМЕТЫ")
        self.section_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.section_label.setToolTip(
            "Скрыть или показать весь список (Ctrl+B). "
            "Каждый предмет сворачивается стрелкой слева."
        )
        ICONS.bind(self.section_label, "chevron-down", "text_secondary", 14)
        self.section_label.clicked.connect(lambda: self.toggle_section())
        section_layout.addWidget(self.section_label)
        section_layout.addStretch(1)
        self.count_label = label("", "muted")
        section_layout.addWidget(self.count_label)
        layout.addWidget(section)

        self.tree = SidebarTree()
        self.tree.setObjectName("sidebar")
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.header().setStretchLastSection(True)
        self.tree.setItemDelegate(SidebarDelegate(self.tree))
        self.tree.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.tree.setMouseTracking(True)
        self.tree.setIndentation(SidebarTree.CHEVRON + 2)
        self.tree.setAnimated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.itemClicked.connect(self._on_click)
        self.tree.itemActivated.connect(self._on_click)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.tree, 1)
        self.tree.setVisible(not self._tree_collapsed)
        ICONS.bind(
            self.section_label,
            "chevron-right" if self._tree_collapsed else "chevron-down",
            "text_secondary",
            14,
        )

        footer = QFrame()
        footer.setObjectName("sidebarFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(18, 10, 16, 12)
        footer_layout.setSpacing(2)
        self.cookie_badge = Badge("cookies не заданы", "danger")
        self.cookie_badge.setToolTip("Cookies нужны для доступа к заданиям")
        footer_layout.addWidget(self.cookie_badge, 0, Qt.AlignmentFlag.AlignLeft)
        self.footer_stats = label("Кэш пуст", "muted")
        footer_layout.addWidget(self.footer_stats)
        self.footer_hint = label("F5 — синхронизация", "muted")
        footer_layout.addWidget(self.footer_hint)
        layout.addWidget(footer)
        self._expanded_state: dict[str, bool] = _load_expanded_state()

        self.tree.itemExpanded.connect(lambda _: self._save_expanded())
        self.tree.itemCollapsed.connect(lambda _: self._save_expanded())

    # ------------------------------------------------------------------ #
    def highlight(self, page: str) -> None:
        """Kept for compatibility: the icon rail owns the section highlight."""
        rail = getattr(self.main, "rail", None)
        if rail is not None:
            rail.set_active(page)

    # ------------------------------------------------------------------ #
    def toggle_section(self, collapsed: bool | None = None) -> None:
        """Hide the предметы list to give the content more room."""
        if collapsed is None:
            collapsed = not self._tree_collapsed
        self._tree_collapsed = bool(collapsed)
        self.tree.setVisible(not self._tree_collapsed)
        ICONS.bind(
            self.section_label,
            "chevron-right" if self._tree_collapsed else "chevron-down",
            "text_secondary",
            14,
        )
        QSettings().setValue("sidebar/collapsed", self._tree_collapsed)

    def _search_triggered(self) -> None:
        query = self.search.text().strip()
        if not query:
            return
        self.main.navigate("all")
        self.main.all_page.search.setText(query)
        self.main.all_page.search.setFocus()

    def _rename_course(self, course_id: int) -> None:
        """Manual name: the escape hatch when the API sends no usable title."""
        course = db.get_course(course_id) or {}
        title, ok = QInputDialog.getText(
            self.main, "Название предмета", "Как называется предмет?",
            text=course.get("title") or "",
        )
        title = title.strip()
        if ok and title:
            db.rename_course(course_id, title)
            self.main.refresh()

    def _rename_homework(self, subject_id: int) -> None:
        homework = db.homework_row(subject_id) or {}
        title, ok = QInputDialog.getText(
            self.main, "Название домашки", "Как называется домашка?",
            text=homework.get("title") or "",
        )
        title = title.strip()
        if ok and title:
            db.rename_subject(subject_id, title)
            self.main.refresh()

    def _show_tree_menu(self, point) -> None:
        menu = QMenu(self)
        menu.addAction(
            "Показать список" if self._tree_collapsed else "Скрыть список",
            lambda: self.toggle_section(),
        )
        menu.exec(self.tree.viewport().mapToGlobal(point))

    # ------------------------------------------------------------------ #
    def rebuild(self, palette: Palette) -> None:
        """Предмет → урок → домашка → раздел."""
        expanded = {**self._expanded_state, **self._expanded_states()}
        delegate = self.tree.itemDelegate()
        if isinstance(delegate, SidebarDelegate):
            delegate.set_palette(palette)
        self.tree.set_branch_color(palette.muted)
        self.tree.set_row_colors(palette.surface_alt, palette.accent_soft)
        self.tree.blockSignals(True)
        self.tree.clear()
        self.section_label.setText("ПРЕДМЕТЫ")

        def add_section(parent: QTreeWidgetItem, section: dict[str, Any]) -> None:
            total = int(section.get("total") or 0)
            supported = int(section.get("supported") or 0)
            item = QTreeWidgetItem([section.get("title") or f"Раздел #{section['id']}"])
            item.setData(0, Qt.ItemDataRole.UserRole, ("kbs", int(section["id"])))
            item.setData(0, COUNT_ROLE, f"{supported}/{total}" if total else "")
            item.setToolTip(
                0,
                f"{section.get('title') or ''}\nРаздел #{section['id']} · "
                f"заданий {total} · с ответом {supported}",
            )
            parent.addChild(item)

        def add_homework(parent: QTreeWidgetItem, homework: dict[str, Any]) -> None:
            subject_id = int(homework["id"])
            total = int(homework.get("total") or 0)
            supported = int(homework.get("supported") or 0)
            item = QTreeWidgetItem([homework.get("title") or f"Домашка #{subject_id}"])
            item.setData(0, Qt.ItemDataRole.UserRole, ("homework", subject_id))
            item.setData(0, COUNT_ROLE, f"{supported}/{total}" if total else "")
            item.setToolTip(
                0,
                f"{homework.get('title') or ''}\nДомашка #{subject_id} · "
                f"разделов {int(homework.get('sections') or 0)} · заданий {total}",
            )
            for section in db.homework_sections(subject_id):
                add_section(item, section)
            parent.addChild(item)
            item.setExpanded(expanded.get(f"h{subject_id}", False))

        def add_lesson(parent: QTreeWidgetItem, lesson: dict[str, Any],
                       homeworks: list[dict[str, Any]]) -> None:
            meta = lesson_meta(lesson)
            title = meta.heading if meta.number is not None else (
                lesson.get("title") or f"Урок #{lesson['id']}"
            )
            if meta.date_text:
                title = f"{title} · {meta.date_text}"
            item = QTreeWidgetItem([title])
            item.setData(0, Qt.ItemDataRole.UserRole, ("lesson", int(lesson["id"])))
            item.setData(0, COUNT_ROLE, str(len(homeworks)) if homeworks else "")
            item.setToolTip(
                0,
                f"{title}\nlessonId {lesson.get('entity_lesson_id') or '—'} · "
                f"домашек {len(homeworks)}",
            )
            for homework in homeworks:
                add_homework(item, homework)
            parent.addChild(item)
            item.setExpanded(expanded.get(f"l{lesson['id']}", bool(homeworks)))

        count = 0
        for course, lessons in db.course_lessons_tree():
            stats = {
                "total": course.get("total") or 0,
                "supported": course.get("supported") or 0,
            }
            item = QTreeWidgetItem([course.get("title") or f"Предмет #{course['id']}"])
            item.setData(0, Qt.ItemDataRole.UserRole, ("course", course["id"]))
            item.setData(0, COUNT_ROLE, f"{stats['supported']}/{stats['total']}")
            item.setToolTip(
                0,
                f"{course.get('title') or ''}\nПредмет #{course['id']} · "
                f"уроков {course.get('lesson_count') or 0} · "
                f"домашек {course.get('homework_count') or 0} · "
                f"заданий {stats['total']} · с ответом {stats['supported']}",
            )
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            self.tree.addTopLevelItem(item)
            count += 1
            for lesson, homeworks in lessons:
                add_lesson(item, lesson, homeworks)
            item.setExpanded(expanded.get(f"c{course['id']}", True))

        standalone = db.standalone_homeworks()
        if standalone:
            caption = QTreeWidgetItem(["Домашки вне предметов"])
            caption.setForeground(0, QColor(palette.muted))
            caption.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(caption)
            for homework in standalone:
                add_homework(caption, homework)
            caption.setExpanded(expanded.get("standalone", True))

        self.tree.blockSignals(False)
        self.count_label.setText(str(count) if count else "")
        stats = db.overall_stats()
        if stats["total"]:
            self.footer_stats.setText(
                f"{stats['total']:,} заданий · {stats['supported']:,} с ответом".replace(",", " ")
            )
        else:
            self.footer_stats.setText("Кэш пуст")
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
        elif kind == "kbs":
            self.navigate.emit(f"kbs:{item_id}")

    def _context_menu(self, point) -> None:
        item = self.tree.itemAt(point)
        if item is None or not item.data(0, Qt.ItemDataRole.UserRole):
            self._show_tree_menu(point)
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            self._show_tree_menu(point)
            return
        kind, item_id = data
        menu = QMenu(self)
        if kind == "course":
            course = db.get_course(item_id) or {}
            menu.addAction(icons.icon("external", "#59616e", 16), "Открыть",
                           lambda: self.navigate.emit(f"course:{item_id}"))
            menu.addAction(icons.icon("refresh", "#59616e", 16), "Синхронизировать предмет",
                           lambda: self.main.sync_course(item_id))
            menu.addAction(icons.icon("copy", "#59616e", 16), "Копировать ID",
                           lambda: QGuiApplication.clipboard().setText(str(item_id)))
            menu.addAction(icons.icon("book", "#59616e", 16), "Переименовать…",
                           lambda: self._rename_course(item_id))
            menu.addSeparator()
            menu.addAction(icons.icon("trash", "#c53b3b", 16), "Удалить предмет из кэша",
                           lambda: self.main.delete_course(item_id))
            menu.setToolTip(course.get("title") or "")
        elif kind == "homework":
            menu.addAction(icons.icon("external", "#59616e", 16), "Открыть домашку",
                           lambda: self.navigate.emit(f"homework:{item_id}"))
            menu.addAction(icons.icon("refresh", "#59616e", 16), "Синхронизировать домашку",
                           lambda: self.main.sync_subject(item_id))
            menu.addAction(icons.icon("copy", "#59616e", 16), "Копировать ID",
                           lambda: QGuiApplication.clipboard().setText(str(item_id)))
            menu.addAction(icons.icon("homework", "#59616e", 16), "Переименовать…",
                           lambda: self._rename_homework(item_id))
        elif kind == "lesson":
            menu.addAction(icons.icon("external", "#59616e", 16), "Открыть урок",
                           lambda: self.navigate.emit(f"lesson:{item_id}"))
            menu.addAction(icons.icon("refresh", "#59616e", 16), "Синхронизировать урок",
                           lambda: self.main.sync_lesson(item_id))
            menu.addAction(icons.icon("copy", "#59616e", 16), "Копировать ID",
                           lambda: QGuiApplication.clipboard().setText(str(item_id)))
        elif kind == "kbs":
            kbs = db.get_kbs(item_id) or {}
            menu.addAction(icons.icon("external", "#59616e", 16), "Открыть",
                           lambda: self.navigate.emit(f"kbs:{item_id}"))
            menu.addAction(icons.icon("refresh", "#59616e", 16), "Синхронизировать домашку",
                           lambda: self.main.sync_subject(kbs.get("subject_id")))
            menu.addAction(icons.icon("copy", "#59616e", 16), "Копировать ID",
                           lambda: QGuiApplication.clipboard().setText(str(item_id)))
            menu.addSeparator()
            menu.addAction(icons.icon("trash", "#c53b3b", 16), "Удалить раздел из кэша",
                           lambda: self.main.delete_kbs(item_id))
        menu.exec(self.tree.viewport().mapToGlobal(point))


RAIL_ICON_IDLE = "#c3c3c8"
RAIL_ICON_ACTIVE = "#ffffff"


class Rail(QFrame):
    """Slim dark icon rail — the only permanent navigation chrome."""

    navigate = pyqtSignal(str)

    def __init__(self, main: MainWindow) -> None:
        super().__init__()
        self.main = main
        self.setObjectName("rail")
        self.setFixedWidth(68)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 14, 12, 14)
        layout.setSpacing(4)
        self._nav: dict[str, QToolButton] = {}
        self._bound: list[tuple[QToolButton, QAction, str]] = []

        self.logo = QToolButton()
        self.logo.setObjectName("railLogo")
        self.logo.setFixedSize(44, 44)
        self.logo.setIconSize(QSize(22, 22))
        self.logo.setIcon(icons.icon(APP_ICON, "#ffffff", 22))
        self.logo.setToolTip(config.APP_TITLE)
        self.logo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.logo.clicked.connect(lambda: self.navigate.emit("dash"))
        layout.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(10)

        layout.addWidget(self._nav_button("grid", "dash", "Обзор"))
        layout.addWidget(self._nav_button("book", "courses", "Предметы"))
        layout.addWidget(self._nav_button("layers", "all", "Все тесты"))
        layout.addSpacing(8)
        layout.addWidget(self._action_button(main.act_back, "arrow-left"))
        layout.addWidget(self._action_button(main.act_forward, "chevron-right"))
        layout.addStretch(1)

        self.sync_button = self._action_button(main.act_sync_selected, "refresh")
        layout.addWidget(self.sync_button)

        layout.addWidget(self._action_button(main.act_add_subject, "plus"))
        self.cookie_button = self._action_button(main.act_cookies, "key")
        layout.addWidget(self.cookie_button)

        self.theme_button = QToolButton()
        self.theme_button.setObjectName("railButton")
        self.theme_button.setFixedSize(44, 44)
        self.theme_button.setIconSize(QSize(20, 20))
        self.theme_button.setIcon(icons.icon("moon", RAIL_ICON_IDLE, 20))
        self.theme_button.setToolTip("Переключить тему")
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.clicked.connect(main.toggle_theme)
        layout.addWidget(self.theme_button)

        self.settings_button = QToolButton()
        self.settings_button.setObjectName("railButton")
        self.settings_button.setFixedSize(44, 44)
        self.settings_button.setIconSize(QSize(20, 20))
        self.settings_button.setIcon(icons.icon("settings", RAIL_ICON_IDLE, 20))
        self.settings_button.setToolTip("Настройки и журнал")
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        layout.addWidget(self.settings_button)

    # ------------------------------------------------------------------ #
    def _nav_button(self, icon_name: str, key: str, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("railButton")
        button.setFixedSize(44, 44)
        button.setIconSize(QSize(20, 20))
        button.setIcon(icons.icon(icon_name, RAIL_ICON_IDLE, 20))
        button.setToolTip(tooltip)
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _checked=False, page=key: self.navigate.emit(page))
        self._nav[key] = button
        return button

    def _action_button(self, action: QAction, icon_name: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("railButton")
        button.setFixedSize(44, 44)
        button.setIconSize(QSize(20, 20))
        button.setIcon(icons.icon(icon_name, RAIL_ICON_IDLE, 20))
        button.setToolTip(action.text().rstrip("…"))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setEnabled(action.isEnabled())
        button.clicked.connect(action.trigger)
        action.changed.connect(
            lambda act=action, btn=button: btn.setEnabled(act.isEnabled())
        )
        self._bound.append((button, action, icon_name))
        return button

    def set_active(self, page: str) -> None:
        active = {"dash": "dash", "courses": "courses", "course": "courses"}.get(
            page, page if page in self._nav else ""
        )
        for key, button in self._nav.items():
            button.setChecked(key == active)
            color = RAIL_ICON_ACTIVE if key == active else RAIL_ICON_IDLE
            button.setIcon(icons.icon(_NAV_ICONS[key], color, 20))

    def restyle(self, dark: bool) -> None:
        """Keep the rail legible: it stays dark in both themes."""
        for button, _action, icon_name in self._bound:
            button.setIcon(icons.icon(icon_name, RAIL_ICON_IDLE, 20))
        for key, button in self._nav.items():
            active = button.isChecked()
            color = RAIL_ICON_ACTIVE if active else RAIL_ICON_IDLE
            button.setIcon(icons.icon(_NAV_ICONS[key], color, 20))
        self.theme_button.setIcon(
            icons.icon("sun" if dark else "moon", RAIL_ICON_IDLE, 20)
        )
        self.settings_button.setIcon(icons.icon("settings", RAIL_ICON_IDLE, 20))


_NAV_ICONS = {"dash": "grid", "courses": "book", "all": "layers"}


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
            "Синхронизировать текущий предмет, урок или домашка (F5)"
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
        """One menu for the settings button — no menu bar, no clutter."""
        menu = QMenu(self)
        menu.setObjectName("mainMenu")

        file_menu = menu.addMenu("Файл")
        file_menu.addAction(self.act_cookies)
        file_menu.addAction(self.act_refresh)
        file_menu.addSeparator()
        file_menu.addAction(self.act_export)
        file_menu.addAction(self.act_clear)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        sync_menu = menu.addMenu("Синхронизация")
        sync_menu.addAction(self.act_add_subject)
        sync_menu.addAction(self.act_sync_selected)
        sync_menu.addAction(self.act_sync_all)
        sync_menu.addAction(self.act_stop)

        view_menu = menu.addMenu("Вид")
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

        menu.addAction(self.act_log)
        menu.addSeparator()
        self.act_reset_layout = QAction("Сбросить настройки окна", self)
        self.act_reset_layout.triggered.connect(self.reset_layout)
        menu.addAction(self.act_reset_layout)
        menu.addAction(self.act_help)
        about = QAction("О программе", self)
        about.triggered.connect(self.show_about)
        menu.addAction(about)

        self.main_menu = menu

    def _build_ui(self) -> None:
        shell = QWidget()
        shell.setObjectName("shell")
        outer = QHBoxLayout(shell)
        outer.setContentsMargins(14, 14, 14, 2)
        outer.setSpacing(10)

        self.rail = Rail(self)
        self.rail.navigate.connect(self.navigate)
        self.rail.settings_button.setMenu(self.main_menu)
        outer.addWidget(self.rail, 0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("mainSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(10)

        panel = QFrame()
        panel.setObjectName("sidePanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        self.sidebar = Sidebar(self)
        self.sidebar.navigate.connect(self.navigate)
        panel_layout.addWidget(self.sidebar)
        self.splitter.addWidget(panel)

        content = QFrame()
        content.setObjectName("content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.stack = QStackedWidget()
        self.dashboard_page = DashboardPage(self)
        self.courses_page = CoursesPage(self)
        self.course_page = CoursePage(self)
        self.lesson_page = LessonPage(self)
        self.homework_page = HomeworkPage(self)
        self.kbs_page = KbsPage(self)
        self.test_page = TestPage(self)
        self.all_page = AllTestsPage(self)
        for page in (self.dashboard_page, self.courses_page, self.course_page,
                     self.lesson_page, self.homework_page, self.kbs_page,
                     self.test_page, self.all_page):
            page.navigate.connect(self.navigate)
            self.stack.addWidget(page)
        content_layout.addWidget(self.stack)
        self.splitter.addWidget(content)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([300, 1040])
        outer.addWidget(self.splitter, 1)
        self.setCentralWidget(shell)

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
        self.rail.restyle(dark=resolved == "dark")
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
        if widget is self.course_page and self.course_page.course_id:
            return f"course:{self.course_page.course_id}"
        if widget is self.homework_page and self.homework_page.subject_id:
            return f"homework:{self.homework_page.subject_id}"
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
            subject_id = _to_int(target.split(":", 1)[1])
            if subject_id is None:
                return
            self.homework_page.show_homework(subject_id)
            self.homework_page.scroll_to_top()
            self.stack.setCurrentWidget(self.homework_page)
            self.sidebar.highlight("")
            self.sidebar.select("homework", subject_id)
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
        if self.homework_page.subject_id:
            self.homework_page.refresh()
        if self.kbs_page.kbs_id:
            self.kbs_page.refresh()
        if self.test_page.test_id:
            self.test_page.show_test(self.test_page.test_id)
        self._update_status()

    def _update_cookie_badge(self) -> None:
        has_cookies = self.api.has_cookies()
        badge = self.sidebar.cookie_badge
        badge.setText("cookies активны" if has_cookies else "cookies не заданы")
        badge.set_variant("success" if has_cookies else "danger")
        badge.setToolTip(
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
        if widget is self.homework_page and self.homework_page.subject_id:
            self.sync_subject(self.homework_page.subject_id)
            return
        subject_id = self._current_subject_id()
        if subject_id is None:
            self.add_link_dialog()
            return
        self.sync_subject(subject_id)

    def _current_subject_id(self) -> int | None:
        widget = self.stack.currentWidget()
        if widget is self.kbs_page and self.kbs_page.kbs_id:
            kbs = db.get_kbs(self.kbs_page.kbs_id)
            return kbs["subject_id"] if kbs else None
        if widget is self.homework_page and self.homework_page.subject_id:
            return int(self.homework_page.subject_id)
        if widget is self.test_page and self.test_page.row:
            return int(self.test_page.row["subject_id"])
        item = self.sidebar.tree.currentItem()
        while item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data[0] == "homework":
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
                "В уроке нет загруженных домашек — синхронизируйте предмет", error=True
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
        targets += [f"домашка {sid}" for sid in subject_ids]
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
                    f"{lessons} {_plural(lessons, 'урок', 'урока', 'уроков')}"
                )
            if plain:
                parts.append(
                    f"{len(plain)} {_plural(len(plain), 'домашка', 'домашки', 'домашек')}"
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
            "Вставьте ссылку на предмет, урок или задание:\n"
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

    def open_link(self, link: ParsedLink) -> None:
        """Do the right thing for a parsed link."""
        if link.subject_id is not None and link.lesson_id is not None:
            # /lesson/<entity_lesson_id>/… — the documented lessonId fallback.
            self.set_lesson_hint(link.subject_id, link.lesson_id)

        if link.kind == "course":
            if link.course_id is None:
                self.toast("В ссылке не найден ID предмета", error=True)
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
                    "Раздел ещё не в кэше — вставьте ссылку на предмет", error=True
                )
            return

        if link.subject_id is None:
            self.toast("В ссылке не найдена домашка", error=True)
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
            f"Удалить домашку «{subject['title']}» и все её задания из локального кэша?\n"
            "На сервере ничего не изменится.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        db.delete_subject(subject_id)
        self.toast("Домашка удалена из кэша")
        self.refresh()
        if self.homework_page.subject_id == subject_id or self.kbs_page.kbs_id and db.get_kbs(self.kbs_page.kbs_id) is None or self.test_page.test_id and db.get_test(self.test_page.test_id) is None:
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
            self.navigate(f"homework:{kbs['subject_id']}")

    def clear_cache_dialog(self) -> None:
        stats = db.overall_stats()
        answer = QMessageBox.question(
            self,
            "Очистить кэш",
            f"Удалить все данные ({stats['courses']} предметов, "
            f"{stats['subjects']} домашек, {stats['total']} тестов)?\n"
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
