"""Application pages: dashboard, subject, knowledge base, test and search."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
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
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import config, db, icons
from ..answers import answer_preview, build_answer
from ..models import (
    TestDetails,
    lesson_meta,
    plural,
    relative_time,
    test_details,
    test_title,
)
from ..theme import Palette
from ..widgets import (
    ICONS,
    AutoHeightTextBrowser,
    Badge,
    ElidedLabel,
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


def type_caption(row: dict[str, Any]) -> str:
    """Human name of a test type; unsupported ones read as one phrase."""
    return TYPE_NAMES.get(row.get("type") or "") or "без автоответа"


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
        self.title = ElidedLabel("")
        self.title.setProperty("role", "h1")
        self.title.setMinimumWidth(120)
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
        self._action_widgets: list[QWidget] = []
        self._more_button: QToolButton | None = None

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
        for widget in self._action_widgets:
            widget.setParent(None)
            widget.deleteLater()
        self._action_widgets = list(actions)
        for widget in self._action_widgets:
            self.actions.addWidget(widget)
        self._more_button = QToolButton(self)
        self._more_button.setObjectName("moreActions")
        self._more_button.setText("•••")
        self._more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more_button.setToolTip("Ещё действия")
        self._more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.actions.addWidget(self._more_button)
        self._fit_actions()

    def _fit_actions(self) -> None:
        """Show as many header buttons as the width allows, the rest in «…».

        Measured, not guessed: the title (capped), the badges and every button
        are compared with the header width, so a narrow window moves the last
        buttons into a menu instead of squeezing the title into «Истори…».
        """
        if self._more_button is None:
            return
        buttons = self._action_widgets
        if not buttons:
            self._more_button.setVisible(False)
            return

        margin = self.layout().contentsMargins()
        available = max(320, self.width() - margin.left() - margin.right())
        badges = self.badge_box.sizeHint().width() if self._badges else 0
        more_width = self._more_button.sizeHint().width()
        shown = len(buttons)
        while shown > 0:
            buttons_width = sum(
                widget.sizeHint().width() for widget in buttons[:shown]
            )
            more = more_width if shown < len(buttons) else 0
            gaps = 10 * 3 + 8 * max(0, shown - 1)  # title|badges|stretch|actions
            needed = (
                ElidedLabel.MIN_WIDTH + badges + buttons_width + more + gaps
            )
            if needed <= available:
                break
            shown -= 1

        for widget in buttons[:shown]:
            widget.setVisible(True)
        for widget in buttons[shown:]:
            widget.setVisible(False)
        self._more_button.setVisible(shown < len(buttons))
        if shown == len(buttons):
            self._more_button.setMenu(None)
            return
        menu = QMenu(self._more_button)
        for widget in buttons[shown:]:
            text = widget.text() or widget.toolTip() or "Действие"
            action = menu.addAction(text)
            action.triggered.connect(widget.click)
        self._more_button.setMenu(menu)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._fit_actions()


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
        courses = int(stats["courses"] or 0)
        homeworks = int(stats["subjects"] or 0)
        tests = int(stats["total"] or 0)
        for caption, value, icon_name in (
            (plural(courses, "предмет", "предмета", "предметов"), fmt_int(courses), "book"),
            (plural(homeworks, "домашка", "домашки", "домашек"), fmt_int(homeworks), "homework"),
            (plural(tests, "тест", "теста", "тестов"), fmt_int(tests), "layers"),
            ("с готовым ответом", fmt_int(stats["supported"]), "check"),
            ("обновлено", relative_time(stats["updated_at"]), "clock"),
        ):
            card = StatCard(caption, value, icon_name)
            card.set_theme(palette)
            cards.addWidget(card, 1)
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
            ("2", "Добавьте предмет по ссылке",
             "Вставьте ссылку /student/courses/111137/lessons — загрузятся все уроки, "
             "практики и задания.",
             secondary_button("Добавить из ссылки", "plus"), self.main.add_link_dialog),
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
        add_button.clicked.connect(self.main.add_link_dialog)
        row.addWidget(add_button)
        all_button = secondary_button("Все тесты", "grid")
        all_button.clicked.connect(lambda: self.navigate.emit("all"))
        row.addWidget(all_button)
        row.addStretch(1)
        self.body.addLayout(row)

    def _build_recent(self, stats: dict[str, int]) -> None:
        courses = db.list_courses()
        if courses:
            self.body.addWidget(SectionTitle("Предметы", len(courses)))
            for course in courses[:4]:
                row = ListRow(
                    course["title"] or f"Предмет #{course['id']}",
                    " · ".join((
                        f"{course['lesson_count']} "
                        f"{plural(course['lesson_count'], 'урок', 'урока', 'уроков')}",
                        f"{course['homework_count']} "
                        f"{plural(course['homework_count'], 'домашка', 'домашки', 'домашек')}",
                        f"{fmt_int(course['total'])} "
                        f"{plural(course['total'], 'тест', 'теста', 'тестов')}",
                    )),
                    trailing=f"{course['supported']}/{course['total']}",
                    icon_name="book",
                )
                row.clicked.connect(
                    lambda _checked=False, cid=course["id"]: self.navigate.emit(f"course:{cid}")
                )
                self.body.addWidget(row)
            if len(courses) > 4:
                more_courses = ghost_button(
                    f"Ещё {len(courses) - 4} — открыть предметы", "layers"
                )
                more_courses.clicked.connect(lambda: self.navigate.emit("courses"))
                self.body.addWidget(more_courses, 0, Qt.AlignmentFlag.AlignLeft)

        standalone = db.standalone_homeworks()
        if standalone:
            self.body.addWidget(SectionTitle("Домашки вне предметов", len(standalone)))
            for homework in standalone[:6]:
                row = ListRow(
                    homework["title"] or f"Домашка #{homework['id']}",
                    " · ".join((
                        f"{int(homework['sections'])} "
                        f"{plural(int(homework['sections']), 'раздел', 'раздела', 'разделов')}",
                        f"{fmt_int(homework['total'])} "
                        f"{plural(homework['total'], 'тест', 'теста', 'тестов')}",
                    )),
                    trailing=f"{homework['supported']}/{homework['total']}",
                    icon_name="homework",
                )
                row.clicked.connect(
                    lambda _checked=False, sid=int(homework["id"]):
                        self.navigate.emit(f"homework:{sid}")
                )
                self.body.addWidget(row)
        self.body.addStretch(1)


class CoursesPage(ScrollPage):
    """All subjects added from ``/student/courses/<id>/lessons`` links.

    A "subject" here is the top level of the site: the page whose first link
    lists уроки; the practice blocks inside it are only a storage detail.
    """

    def __init__(self, main) -> None:
        super().__init__(main)
        self.navigate.connect(main.navigate)
        self.refresh()

    def apply_theme(self, palette: Palette) -> None:
        self.refresh()

    def refresh(self) -> None:
        clear_layout(self.body)
        add_button = primary_button("Добавить из ссылки", "plus")
        add_button.clicked.connect(self.main.add_link_dialog)
        self.header.set_content(
            "Предметы",
            "Уроки и домашки, загруженные из ссылки на предмет.",
            breadcrumb=[("Библиотека", "dash"), ("Предметы", None)],
            actions=[add_button],
        )

        courses = db.list_courses()
        if not courses:
            empty_add = primary_button("Добавить предмет", "plus")
            empty_add.clicked.connect(self.main.add_link_dialog)
            self.body.addWidget(EmptyState(
                "book",
                "Предметы ещё не добавлены",
                "Скопируйте адрес страницы предмета, например "
                "https://education.maximumtest.ru/student/courses/111137/lessons, "
                "и вставьте его в поле «Добавить из ссылки». Приложение загрузит "
                "уроки, домашки и все задания.",
                [empty_add],
            ))
            self.body.addStretch(1)
            return

        self.body.addWidget(SectionTitle("Предметы", len(courses)))
        for course in courses:
            lessons = course.get("lesson_count") or 0
            homeworks = course.get("homework_count") or 0
            total = course.get("total") or 0
            supported = course.get("supported") or 0
            subtitle = " · ".join((
                f"{fmt_int(lessons)} {plural(lessons, 'урок', 'урока', 'уроков')}",
                f"{fmt_int(homeworks)} {plural(homeworks, 'домашка', 'домашки', 'домашек')}",
                f"обновлено {relative_time(course.get('fetched_at'))}",
            ))
            row = ListRow(
                course["title"] or f"Предмет #{course['id']}",
                subtitle,
                trailing=f"{supported}/{total}",
                icon_name="book",
            )
            row.clicked.connect(
                lambda _checked=False, cid=course["id"]: self.navigate.emit(f"course:{cid}")
            )
            self.body.addWidget(row)
        self.body.addStretch(1)


def homework_row_widget(
    homework: dict[str, Any], navigate, *, icon: str = "homework"
) -> ListRow:
    """One homework row.

    Домашка = «subject» из ссылки ``/lesson/<id>/subjects/<id>/...``; внутри неё
    лежат разделы (knowledge bases) с заданиями.
    """
    subject_id = int(homework["id"])
    total = int(homework.get("total") or 0)
    supported = int(homework.get("supported") or 0)
    sections = int(homework.get("sections") or 0)
    bits: list[str] = []
    if sections:
        bits.append(f"{fmt_int(sections)} {plural(sections, 'раздел', 'раздела', 'разделов')}")
    if total:
        bits.append(f"{fmt_int(total)} {plural(total, 'тест', 'теста', 'тестов')}")
    elif sections:
        bits.append("заданий пока нет")
    row = ListRow(
        homework.get("title") or f"Домашка #{subject_id}",
        " · ".join(bits),
        trailing=f"{supported}/{total}" if total else "",
        icon_name=icon,
    )
    row.clicked.connect(lambda _checked=False, sid=subject_id: navigate(f"homework:{sid}"))
    return row


class LessonCard(QFrame):
    """«Урок N · 3 сентября» with its домашками inside, like the site."""

    def __init__(
        self,
        lesson: dict[str, Any],
        homeworks: list[dict[str, Any]],
        index: int,
        palette: Palette,
        navigate,
        expanded: bool = True,
    ) -> None:
        super().__init__()
        self.setObjectName("card")
        self.navigate = navigate
        self.lesson = lesson
        self._expanded = expanded
        self._palette = palette
        meta = lesson_meta(lesson)
        self.meta = meta

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(4)

        if meta.date_text:
            date_row = QHBoxLayout()
            date_row.setSpacing(6)
            calendar = QLabel()
            calendar.setPixmap(icons.pixmap("calendar", palette.muted, 14))
            date_row.addWidget(calendar)
            date_row.addWidget(label(meta.date_text, "muted"))
            date_row.addStretch(1)
            left.addLayout(date_row)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        heading = meta.heading if meta.number is not None else (lesson.get("title") or "Урок")
        title_row.addWidget(label(heading, "h2"))
        if meta.status:
            title_row.addWidget(Badge(meta.status, meta.status_kind or "success"))
        title_row.addStretch(1)
        left.addLayout(title_row)
        top.addLayout(left, 1)

        right = QHBoxLayout()
        right.setSpacing(8)
        if meta.online:
            online_icon = QLabel()
            online_icon.setPixmap(icons.pixmap("video", palette.muted, 14))
            right.addWidget(online_icon)
            time_text = f" {meta.time_text}" if meta.time_text else ""
            right.addWidget(label(f"Онлайн{time_text}", "muted"))
        self.chevron = QToolButton()
        self.chevron.setAutoRaise(True)
        self.chevron.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chevron.setToolTip("Свернуть урок" if expanded else "Показать домашки")
        self.chevron.clicked.connect(self.toggle)
        right.addWidget(self.chevron)
        top.addLayout(right, 0)
        top.setAlignment(right, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        if meta.progress is not None:
            progress_row = QHBoxLayout()
            progress_row.setSpacing(8)
            progress_row.addWidget(label("Прогресс по ДЗ", "muted"))
            progress_row.addStretch(1)
            progress_row.addWidget(label(f"{meta.progress}%", "muted"))
            layout.addLayout(progress_row)

            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(meta.progress))
            bar.setTextVisible(False)
            bar.setFixedHeight(7)
            fill = "#4caf50" if palette.name == "light" else "#4ed3a0"
            bar.setStyleSheet(
                "QProgressBar { background: " + palette.surface_alt + ";"
                " border: none; border-radius: 4px; }"
                "QProgressBar::chunk { background: " + fill + ";"
                " border-radius: 4px; }"
            )
            layout.addWidget(bar)

        if meta.topics:
            quoted = ", ".join(f"«{topic}»" for topic in meta.topics)
            layout.addWidget(label(f"Темы урока: {quoted}", "muted", wrap=True))

        self.homework_box = QWidget()
        homework_layout = QVBoxLayout(self.homework_box)
        homework_layout.setContentsMargins(0, 4, 0, 0)
        homework_layout.setSpacing(6)
        if homeworks:
            for homework in homeworks:
                homework_layout.addWidget(homework_row_widget(homework, navigate))
        else:
            homework_layout.addWidget(label(
                "Домашек нет — синхронизируйте предмет", "muted"
            ))
        layout.addWidget(self.homework_box)
        self._apply_chevron()
        self.homework_box.setVisible(expanded)

    def _apply_chevron(self) -> None:
        name = "chevron-down" if self._expanded else "chevron-right"
        self.chevron.setIcon(icons.icon(name, self._palette.text_secondary, 16))

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self.homework_box.setVisible(self._expanded)
        self._apply_chevron()


class CoursePage(ScrollPage):
    """Предмет (courses/<id>): уроки, в каждом — домашки."""

    def __init__(self, main) -> None:
        super().__init__(main)
        self.navigate.connect(main.navigate)
        self.course_id: int | None = None

    def show_course(self, course_id: int) -> None:
        self.course_id = course_id
        self.refresh()

    def apply_theme(self, palette: Palette) -> None:
        self.refresh()

    def refresh(self) -> None:
        if self.course_id is None:
            return
        course = db.get_course(self.course_id)
        clear_layout(self.body)
        if not course:
            self.header.set_content(
                "Предмет не найден", "", [("Библиотека", "dash"), ("Предметы", "courses")]
            )
            add_button = primary_button("Добавить из ссылки", "plus")
            add_button.clicked.connect(self.main.add_link_dialog)
            self.body.addWidget(EmptyState(
                "alert", "Предмет удалён из кэша",
                "Добавьте ссылку на предмет снова, чтобы загрузить уроки и домашки.",
                [add_button],
            ))
            self.body.addStretch(1)
            return

        stats = db.course_stats(self.course_id)
        groups = db.course_homeworks(self.course_id)
        title = course["title"] or f"Предмет #{self.course_id}"
        sync_button = primary_button("Синхронизировать", "refresh")
        sync_button.clicked.connect(lambda: self.main.sync_course(self.course_id))
        delete_button = ghost_button("Удалить из кэша", "trash", color_key="danger")
        delete_button.clicked.connect(lambda: self.main.delete_course(self.course_id))
        self.header.set_content(
            title,
            " · ".join((
                f"{fmt_int(stats['lessons'])} "
                f"{plural(stats['lessons'], 'урок', 'урока', 'уроков')}",
                f"{fmt_int(stats['homeworks'])} "
                f"{plural(stats['homeworks'], 'домашка', 'домашки', 'домашек')}",
                f"{fmt_int(stats['total'])} {plural(stats['total'], 'тест', 'теста', 'тестов')}",
                f"обновлено {relative_time(course.get('fetched_at'))}",
            )),
            breadcrumb=[("Библиотека", "dash"), ("Предметы", "courses"), (title, None)],
            badges=[
                (f"{fmt_int(stats['supported'])} с ответом",
                 "success" if stats["supported"] else "muted"),
            ],
            actions=[sync_button, delete_button],
        )

        if not groups["lessons"]:
            load_button = primary_button("Загрузить уроки", "refresh")
            load_button.clicked.connect(lambda: self.main.sync_course(self.course_id))
            self.body.addWidget(EmptyState(
                "layers",
                "Уроки ещё не загружены",
                "Синхронизируйте предмет: приложение прочитает структуру предмета, "
                "уроки и скачает все домашки.",
                [load_button],
            ))
            self.body.addStretch(1)
            return

        self.body.addWidget(SectionTitle("Уроки", len(groups["lessons"])))
        for index, (lesson, homeworks) in enumerate(groups["lessons"], start=1):
            self.body.addWidget(LessonCard(
                lesson, homeworks, index, self.main.theme_palette(), self.navigate.emit
            ))
        self.body.addStretch(1)


class LessonPage(ScrollPage):
    """Урок (lesson/<id>) и его домашки."""

    def __init__(self, main) -> None:
        super().__init__(main)
        self.navigate.connect(main.navigate)
        self.lesson_id: int | None = None

    def show_lesson(self, lesson_id: int) -> None:
        self.lesson_id = lesson_id
        self.refresh()

    def apply_theme(self, palette: Palette) -> None:
        self.refresh()

    def refresh(self) -> None:
        if self.lesson_id is None:
            return
        lesson = db.get_lesson(self.lesson_id)
        clear_layout(self.body)
        if not lesson:
            self.header.set_content(
                "Урок не найден", "", [("Библиотека", "dash"), ("Предметы", "courses")]
            )
            self.body.addWidget(EmptyState(
                "alert", "Урок отсутствует в кэше",
                "Синхронизируйте предмет, чтобы построить структуру уроков.",
            ))
            self.body.addStretch(1)
            return

        course = db.get_course(lesson["course_id"]) or {}
        meta = lesson_meta(lesson)
        homeworks = db.lesson_homeworks(self.lesson_id)
        title = meta.heading if meta.number is not None else (
            lesson["title"] or f"Урок #{self.lesson_id}"
        )
        sync_button = primary_button("Синхронизировать", "refresh")
        sync_button.clicked.connect(lambda: self.main.sync_lesson(self.lesson_id))
        open_course = secondary_button("К предмету", "book")
        open_course.clicked.connect(
            lambda: self.navigate.emit(f"course:{lesson['course_id']}")
        )
        bits: list[str] = []
        if meta.date_text:
            bits.append(meta.date_text)
        if homeworks:
            bits.append(
                f"{fmt_int(len(homeworks))} "
                f"{plural(len(homeworks), 'домашка', 'домашки', 'домашек')}"
            )
        self.header.set_content(
            title,
            " · ".join(bits),
            breadcrumb=[
                ("Библиотека", "dash"),
                ("Предметы", "courses"),
                (course.get("title") or f"Предмет #{lesson['course_id']}",
                 f"course:{lesson['course_id']}"),
                (title, None),
            ],
            actions=[sync_button, open_course],
        )

        if meta.topics:
            card = QFrame()
            card.setObjectName("cardFlat")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 12, 16, 12)
            card_layout.addWidget(label(
                "Темы урока: " + ", ".join(f"«{topic}»" for topic in meta.topics),
                "", wrap=True,
            ))
            self.body.addWidget(card)

        if not homeworks:
            self.body.addWidget(EmptyState(
                "layers",
                "В уроке нет домашек",
                "Этот урок содержит только теорию — заданий для загрузки нет.",
                [secondary_button("Открыть предмет", "book")],
            ))
            self.body.addStretch(1)
            return

        self.body.addWidget(SectionTitle("Домашки", len(homeworks)))
        for homework in homeworks:
            self.body.addWidget(homework_row_widget(homework, self.navigate.emit))
        self.body.addStretch(1)


class HomeworkPage(ScrollPage):
    """Домашка (subject): разделы + тренажёр с пронумерованными заданиями."""

    def __init__(self, main) -> None:
        super().__init__(main)
        self.navigate.connect(main.navigate)
        self.subject_id: int | None = None
        self.index = 0
        self._tests: list[dict[str, Any]] = []
        self._chips: list[QPushButton] = []
        self._shortcuts: list[QShortcut] = []
        self._palette = main.theme_palette()

    # ------------------------------------------------------------------ #
    def show_homework(self, subject_id: int, index: int = 0) -> None:
        self.subject_id = subject_id
        self.index = max(0, int(index))
        self.refresh()

    #: Old name kept for callers.
    @property
    def kbs_id(self) -> int | None:
        return self.subject_id

    def apply_theme(self, palette: Palette) -> None:
        self._palette = palette
        self.refresh()

    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        if self.subject_id is None:
            return
        homework = db.homework_row(self.subject_id)
        clear_layout(self.body)
        self._chips = []
        if not homework:
            self.header.set_content(
                "Домашка не найдена", "", [("Библиотека", "dash"), ("Предметы", "courses")]
            )
            self.body.addWidget(EmptyState("alert", "Домашка отсутствует в кэше", ""))
            self.body.addStretch(1)
            return

        self._tests = db.homework_tests(self.subject_id)
        if self._tests:
            self.index = min(self.index, len(self._tests) - 1)
        sections = db.homework_sections(self.subject_id)
        lesson = homework.get("lesson") or {}
        course = homework.get("course") or {}
        meta = lesson_meta(lesson) if lesson else None
        total = int(homework.get("total") or 0)
        supported = int(homework.get("supported") or 0)
        title = homework.get("title") or f"Домашка #{self.subject_id}"

        sync_button = primary_button("Синхронизировать", "refresh")
        sync_button.clicked.connect(lambda: self.main.sync_subject(self.subject_id))

        breadcrumb: list[tuple[str, str | None]] = [("Библиотека", "dash")]
        if lesson:
            if course:
                breadcrumb.append(
                    (course.get("title") or f"Предмет #{course.get('id')}",
                     f"course:{course.get('id')}")
                )
            breadcrumb.append(
                (meta.heading if meta and meta.number is not None
                 else lesson.get("title") or "Урок",
                 f"lesson:{lesson['id']}")
            )
        breadcrumb.append((title, None))

        self.header.set_content(
            title,
            " · ".join(bit for bit in (
                f"{fmt_int(len(sections))} {plural(len(sections), 'раздел', 'раздела', 'разделов')}",
                f"{fmt_int(total)} {plural(total, 'тест', 'теста', 'тестов')}",
                meta.date_text if meta else "",
            ) if bit),
            breadcrumb=breadcrumb,
            badges=[(f"{fmt_int(supported)} с ответом", "success" if supported else "muted")],
            actions=[sync_button],
        )

        if sections:
            self.body.addWidget(SectionTitle("Разделы домашки", len(sections)))
            for section in sections:
                row_widget = ListRow(
                    section.get("title") or f"Раздел #{section['id']}",
                    f"{fmt_int(int(section.get('total') or 0))} "
                    f"{plural(int(section.get('total') or 0), 'тест', 'теста', 'тестов')}",
                    trailing=f"{int(section.get('supported') or 0)}/{int(section.get('total') or 0)}",
                )
                section_id = int(section["id"])
                row_widget.clicked.connect(
                    lambda _checked=False, sid=section_id: self.navigate.emit(f"kbs:{sid}")
                )
                self.body.addWidget(row_widget)

        if not self._tests:
            load_button = primary_button("Загрузить задания", "refresh")
            load_button.clicked.connect(lambda: self.main.sync_subject(self.subject_id))
            self.body.addWidget(EmptyState(
                "layers",
                "В домашке пока нет заданий",
                "Синхронизируйте предмет, чтобы скачать тесты.",
                [load_button],
            ))
            self.body.addStretch(1)
            return

        chips_card = QFrame()
        chips_card.setObjectName("card")
        chips_layout = QVBoxLayout(chips_card)
        chips_layout.setContentsMargins(18, 14, 18, 14)
        chips_layout.setSpacing(8)
        chips_row = QHBoxLayout()
        chips_row.setSpacing(6)
        for position, test in enumerate(self._tests):
            chip = QPushButton(str(position + 1))
            chip.setFixedSize(36, 32)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(test_title(test))
            chip.clicked.connect(
                lambda _checked=False, pos=position: self.open_test_at(pos)
            )
            self._chips.append(chip)
            chips_row.addWidget(chip)
        chips_row.addStretch(1)
        chips_layout.addLayout(chips_row)
        chips_layout.addWidget(label(
            f"Задание {self.index + 1} из {len(self._tests)}", "muted"
        ))
        self.body.addWidget(chips_card)
        self._style_chips()

        row = self._tests[self.index]
        details = test_details(row)
        self._build_question(details)
        self._build_options(details)
        self._build_nav()
        self.body.addStretch(1)
        self._install_shortcuts()

    # ------------------------------------------------------------------ #
    def _style_chips(self) -> None:
        palette = self._palette
        for position, chip in enumerate(self._chips):
            row = self._tests[position]
            try:
                answered = test_details(row).has_right_answers
            except Exception:  # noqa: BLE001 - styling must never break the page
                answered = False
            current = position == self.index
            if current:
                background, color, border = palette.surface, palette.accent, palette.accent
            elif answered:
                background, color, border = palette.success_soft, palette.success, "transparent"
            else:
                background, color, border = palette.surface_alt, palette.text_secondary, "transparent"
            chip.setStyleSheet(
                f"QPushButton {{ background: {background}; color: {color};"
                f" border: 2px solid {border}; border-radius: 9px;"
                f" font-weight: {'700' if current else '600'}; }}"
                "QPushButton:hover { border-color: " + palette.accent + "; }"
            )
            # The stylesheet resets the constraints it knows about, so the
            # fixed size goes last: square chips with the number inside.
            chip.setFixedSize(38, 34)

    def chip_count(self) -> int:
        return len(self._chips)

    def open_test_at(self, position: int) -> None:
        if 0 <= position < len(self._tests):
            self.index = position
            self.refresh()

    def go_previous(self) -> None:
        if self.index > 0:
            self.open_test_at(self.index - 1)

    def go_next(self) -> None:
        if self.index < len(self._tests) - 1:
            self.open_test_at(self.index + 1)

    def open_current_test(self) -> None:
        if self._tests:
            self.navigate.emit(f"test:{self._tests[self.index]['id']}")

    def _install_shortcuts(self) -> None:
        """Bind ←/→/Enter once: rebuilds must not stack duplicate shortcuts."""
        if self._shortcuts:
            return
        for key, slot in (
            (Qt.Key.Key_Left, self.go_previous),
            (Qt.Key.Key_Right, self.go_next),
            (Qt.Key.Key_Return, self.open_current_test),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

    # ------------------------------------------------------------------ #
    def _build_question(self, details: TestDetails) -> None:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(Badge(
            TYPE_NAMES.get(details.type, details.type or "тип неизвестен"), "accent"
        ))
        head.addWidget(label(details.title, "muted"))
        head.addStretch(1)
        layout.addLayout(head)

        browser = AutoHeightTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.document().setDefaultStyleSheet(
            f"body {{ color: {self._palette.text}; font-size: 15px; line-height: 150%; }}"
            f"p {{ margin: 6px 0; }} a {{ color: {self._palette.accent}; }}"
            "img { max-width: 100%; }"
        )
        browser.setHtml(details.question_html or "<p><i>Условие не сохранено в кэше.</i></p>")
        layout.addWidget(browser)
        QTimer.singleShot(0, browser.fit_to_content)
        self.body.addWidget(card)

    def _build_options(self, details: TestDetails) -> None:
        if not details.options:
            return
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 14, 20, 18)
        layout.setSpacing(6)
        letters = "АБВГДЕЖЗИКЛМНОПРСТУФ"
        palette = self._palette
        for position, option in enumerate(details.options):
            row = QFrame()
            row.setObjectName("cardFlat")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 10, 12, 10)
            row_layout.setSpacing(12)

            mark = QLabel()
            mark.setFixedSize(18, 18)
            mark.setStyleSheet(
                f"border-radius: 9px; border: 2px solid "
                f"{palette.success if option.is_right else palette.border_strong};"
                f" background: {palette.success_soft if option.is_right else 'transparent'};"
            )
            row_layout.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
            prefix = letters[position] if position < len(letters) else f"{position + 1}."
            text = label(
                f"{prefix}. {option.display}",
                "" if option.is_right else "muted",
                wrap=True,
            )
            row_layout.addWidget(text, 1)
            if option.is_right:
                row_layout.addWidget(Badge("верно", "success"))
            layout.addWidget(row)
        self.body.addWidget(card)

    def _build_nav(self) -> None:
        card = QFrame()
        card.setObjectName("cardFlat")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        previous = secondary_button("Назад", "chevron-left")
        previous.clicked.connect(self.go_previous)
        previous.setEnabled(self.index > 0)
        layout.addWidget(previous)
        following = secondary_button("Далее", "chevron-right")
        following.clicked.connect(self.go_next)
        following.setEnabled(self.index < len(self._tests) - 1)
        layout.addWidget(following)
        layout.addStretch(1)

        copy_button = ghost_button("Копировать ответ", "copy", color_key="muted")
        copy_button.clicked.connect(self._copy_answer)
        layout.addWidget(copy_button)
        open_button = primary_button("Открыть задание", "external")
        open_button.clicked.connect(self.open_current_test)
        layout.addWidget(open_button)
        self.body.addWidget(card)

    def _copy_answer(self) -> None:
        if not self._tests:
            return
        details = test_details(self._tests[self.index])
        answer = build_answer(details)
        self.main.copy_text(json.dumps(answer, ensure_ascii=False), "Ответ скопирован")


class KbsPage(QWidget):
    """Knowledge base: a searchable table of its tests."""

    navigate = pyqtSignal(str)

    def __init__(self, main) -> None:
        super().__init__()
        self.main = main
        self.kbs_id: int | None = None
        self._tests: list[dict[str, Any]] = []
        self._filtered: list[dict[str, Any]] = []
        self._palette = main.theme_palette()

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
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 165)
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
        self._palette = palette
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
            f"Домашка: {subject.get('title') or 'без названия'}"
            f" · режим: {config.mode_label(kbs.get('mode'))}",
            breadcrumb=[
                ("Библиотека", "dash"),
                (subject.get("title") or "Домашка", f"homework:{kbs['subject_id']}"),
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
        palette = self._palette
        for index, row in enumerate(rows):
            supported = config.is_supported(row.get("type"))
            type_item = QTableWidgetItem(type_caption(row))
            type_item.setForeground(
                QColor(palette.accent if supported else palette.muted)
            )
            font = type_item.font()
            font.setBold(True)
            type_item.setFont(font)
            self.table.setItem(index, 0, type_item)

            self.table.setItem(index, 1, NumericItem(int(row["id"])))

            title_item = QTableWidgetItem(test_title(row))
            title_item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            if not supported:
                title_item.setForeground(QColor(palette.muted))
            self.table.setItem(index, 2, title_item)

            answer = "—"
            if supported:
                try:
                    answer = answer_preview(test_details(row), limit=2)
                except Exception:  # noqa: BLE001 - preview must never break the table
                    answer = "—"
            answer_item = QTableWidgetItem(answer)
            answer_item.setForeground(QColor(palette.text_secondary))
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
            ("поддерживается" if details.supported else "только просмотр",
             "success" if details.supported else "warning"),
        ]
        self.header.set_content(
            details.title,
            f"{subject.get('title') or 'Предмет'} · {kbs.get('title') or 'Раздел'} · {position}".strip(" ·"),
            breadcrumb=[
                ("Библиотека", "dash"),
                (subject.get("title") or "Домашка", f"homework:{row['subject_id']}"),
                (kbs.get("title") or f"Раздел #{row['kbs_id']}", f"kbs:{row['kbs_id']}"),
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
            "Отправка ответа изменит результат в личном кабинете. lessonId — это "
            "id урока из ссылки на задание (entity lesson id), он подставляется из "
            "структуры предмета; проверьте значение перед отправкой.",
            "muted", wrap=True,
        )
        layout.addWidget(warning)

        send_row = QHBoxLayout()
        send_row.setSpacing(8)
        lesson_caption = label("lessonId", "muted")
        send_row.addWidget(lesson_caption)
        self.lesson_input = QSpinBox()
        self.lesson_input.setRange(0, 2_147_483_647)
        # The answer endpoint expects the *entity* lesson id — the one in
        # /lesson/<id>/subjects/… and in the course structure.  The curriculum
        # tree numbers lessons differently, so it is only the last resort.
        entity_lesson = db.lesson_id_for_subject(row.get("subject_id"))
        hint = self.main.lesson_hint(row.get("subject_id"))
        tree_lesson = (self.kbs or {}).get("lesson_id") or 0
        self.lesson_input.setValue(
            entity_lesson or hint or details.lesson_id or tree_lesson or 0
        )
        self.lesson_input.setToolTip(
            "Из структуры предмета" if entity_lesson
            else "Из ссылки на задание" if hint
            else "Из данных задания" if details.lesson_id
            else "Из дерева предмета" if tree_lesson
            else "Не найден — укажите вручную"
        )
        self.lesson_input.setMinimumWidth(150)
        send_row.addWidget(self.lesson_input)
        send_row.addStretch(1)
        self.send_button = QPushButton("Отправить ответ")
        self.send_button.setProperty("variant", "primary")
        ICONS.bind(self.send_button, "send", "on_accent", 16)
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
            ("Домашка", subject.get("title") or f"Домашка #{row['subject_id']}"),
            ("Раздел", kbs.get("title") or f"Раздел #{row['kbs_id']}"),
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
        self._palette = main.theme_palette()
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
        self.table.setHorizontalHeaderLabels(["ID", "Домашка", "Раздел", "Тип", "Задание"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 74)
        self.table.setColumnWidth(1, 190)
        self.table.setColumnWidth(2, 200)
        self.table.setColumnWidth(3, 168)
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
        self.subject_combo.addItem("Все домашки", None)
        for subject in db.list_subjects():
            self.subject_combo.addItem(
                subject["title"] or f"Домашка #{subject['id']}", subject["id"]
            )
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

        palette = self._palette
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

            type_item = QTableWidgetItem(type_caption(row))
            type_item.setForeground(
                QColor(palette.accent if supported else palette.muted)
            )
            font = type_item.font()
            font.setBold(True)
            type_item.setFont(font)
            self.table.setItem(index, 3, type_item)

            title_item = QTableWidgetItem(row.get("title") or f"Тест #{row['id']}")
            title_item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            if not supported:
                title_item.setForeground(QColor(palette.muted))
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
