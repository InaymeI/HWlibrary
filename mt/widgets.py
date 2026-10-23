"""Small reusable widgets that give the app its consistent look."""

from __future__ import annotations

import contextlib
import weakref
from collections.abc import Iterable

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .theme import Palette


def repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def label(
    text: str = "",
    role: str | None = None,
    *,
    wrap: bool = False,
    selectable: bool = False,
) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    if selectable:
        widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


class Badge(QLabel):
    """Pill-shaped label: accent / success / warning / danger / muted."""

    def __init__(self, text: str = "", variant: str = "muted", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setProperty("role", "badge")
        self.setProperty("variant", variant)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_variant(self, variant: str) -> None:
        self.setProperty("variant", variant)
        repolish(self)


class StatCard(QFrame):
    """A card with a big number, a caption and an icon."""

    def __init__(
        self,
        caption: str,
        value: str = "0",
        icon_name: str | None = None,
        color: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._color = color
        self._icon_name = icon_name
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 13, 15, 13)
        layout.setSpacing(10)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        # both labels elide instead of forcing the row wider than the page
        self.value_label = ElidedLabel(value, min_width=36, max_width=240)
        self.value_label.setProperty("role", "stat")
        self._apply_value_role(value)
        self.caption_label = ElidedLabel(caption, min_width=44, max_width=220)
        self.caption_label.setProperty("role", "muted")
        text_box.addWidget(self.value_label)
        text_box.addWidget(self.caption_label)
        layout.addLayout(text_box, 1)

        self._icon_label: QLabel | None = None
        if icon_name:
            self._icon_label = QLabel()
            self._icon_label.setFixedSize(36, 36)
            self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignTop)
        self.set_theme(None)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        # five cards in one row get narrow: the icon goes first, the values stay
        if self._icon_label is not None:
            self._icon_label.setVisible(self.width() >= 190)

    def _apply_value_role(self, value: str) -> None:
        """Long values («только что») shrink so all cards keep the same rhythm."""
        role = "stat" if len(value) <= 4 else "statSmall"
        if self.value_label.property("role") != role:
            self.value_label.setProperty("role", role)
            style = self.value_label.style()
            style.unpolish(self.value_label)
            style.polish(self.value_label)

    def set_theme(self, palette: Palette | None) -> None:
        if not self._icon_label or not self._icon_name:
            return
        color = self._color or (palette.accent if palette else "#4a63dd")
        soft = palette.accent_soft if palette else "#eceffe"
        self._icon_label.setPixmap(icons.pixmap(self._icon_name, color, 19))
        self._icon_label.setStyleSheet(
            f"background: {soft}; border-radius: 12px;"
        )


def paint_color(value: str) -> QColor:
    """QColor from ``#rrggbb`` or the ``rgba(r, g, b, a)`` used in the palettes."""
    text = (value or "").strip()
    if text.startswith("rgba(") and text.endswith(")"):
        parts = [part.strip() for part in text[5:-1].split(",")]
        if len(parts) == 4:
            try:
                red, green, blue = (int(float(part)) for part in parts[:3])
                alpha = float(parts[3])
            except ValueError:
                return QColor()
            if alpha <= 1.0:
                alpha *= 255.0
            return QColor(red, green, blue, max(0, min(255, int(round(alpha)))))
    return QColor(text)


def paint_aero_pill(
    painter: QPainter,
    rect: QRect,
    palette: Palette,
    *,
    radius: int = 12,
    strength: float = 1.0,
) -> None:
    """Windows Vista style selection: a glossy aqua pill with a white rim.

    The 2009 look is a *vertical* gradient — almost white at the top, deeper
    aqua at the bottom — plus a bright specular band across the upper half.
    ``strength`` < 1 fades everything for hover.
    """
    box = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    if box.isEmpty():
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    body_path = QPainterPath()
    radius = 3
    body_path.addRoundedRect(box, radius, radius)

    top = paint_color(palette.gloss_top)
    bottom = paint_color(palette.gloss_bottom)
    top.setAlphaF(min(1.0, top.alphaF() * strength))
    bottom.setAlphaF(min(1.0, bottom.alphaF() * strength))
    body = QLinearGradient(box.topLeft(), box.bottomLeft())
    body.setColorAt(0.0, top)
    body.setColorAt(0.52, top)
    body.setColorAt(1.0, bottom)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(body)
    painter.drawPath(body_path)

    # specular band: the upper half is much brighter than the lower one
    painter.save()
    painter.setClipPath(body_path)
    clear = QColor(Qt.GlobalColor.white)
    clear.setAlphaF(0.0)
    gloss = QColor(Qt.GlobalColor.white)
    gloss.setAlphaF(0.55 * strength)
    band = QLinearGradient(box.topLeft(), box.bottomLeft())
    band.setColorAt(0.0, gloss)
    band.setColorAt(0.42, gloss)
    band.setColorAt(0.55, clear)
    band.setColorAt(1.0, clear)
    painter.setBrush(band)
    painter.drawRect(box)
    painter.restore()

    # white rim, thinner at the bottom — exactly like an Aero selection
    rim = QColor("#7da2ce")
    light = QColor(rim)
    light.setAlphaF(min(1.0, rim.alphaF() * strength))
    dim = QColor(rim)
    dim.setAlphaF(min(1.0, rim.alphaF() * 0.55 * strength))
    rim_gradient = QLinearGradient(box.topLeft(), box.bottomLeft())
    rim_gradient.setColorAt(0.0, light)
    rim_gradient.setColorAt(0.6, light)
    rim_gradient.setColorAt(1.0, dim)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QBrush(rim_gradient), 1.0))
    painter.drawRoundedRect(box, radius, radius)
    painter.restore()


def paint_glass(
    painter: QPainter,
    rect: QRect,
    palette: Palette,
    *,
    radius: int = 12,
    strength: float = 1.0,
) -> None:
    """Liquid-glass pill: soft shadow, milky body and a bright hairline rim.

    Qt cannot blur what is *behind* a widget, so the surface is built from what
    glass actually shows on screen: an almost clear left side that turns milky
    towards the right, a specular line along the top edge and a rim that stays
    bright on top and fades at the bottom.  ``strength`` < 1 fades it down for
    hover.

    The Frutiger Aero theme swaps this for :func:`paint_aero_pill`, so every
    caller (tree rows, nav rows, rail tiles) gets the glossy 2009 selection
    without knowing about themes.
    """
    if palette.name == "aero":
        paint_aero_pill(painter, rect, palette, radius=radius, strength=strength)
        return
    box = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    if box.isEmpty():
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    body_path = QPainterPath()
    body_path.addRoundedRect(box, radius, radius)

    # ambient shadow, strictly outside the body so it cannot grey the glass
    shade = paint_color(palette.glass_shadow)
    painter.save()
    outside = QPainterPath()
    outside.addRect(box.adjusted(-9.0, -9.0, 9.0, 11.0))
    painter.setClipPath(outside.subtracted(body_path))
    painter.setPen(Qt.PenStyle.NoPen)
    for spread, factor in ((5.0, 0.06), (3.2, 0.10), (1.6, 0.16)):
        shadow = QColor(shade)
        shadow.setAlphaF(min(1.0, shade.alphaF() * factor * strength))
        painter.setBrush(shadow)
        painter.drawRoundedRect(
            box.adjusted(-spread, -spread + 1.5, spread, spread + 2.0),
            radius + spread,
            radius + spread,
        )
    painter.restore()

    # body: clearer on the left, milky on the right, with a cool tint
    start = paint_color(palette.glass_start)
    end = paint_color(palette.glass_end)
    start.setAlphaF(start.alphaF() * strength)
    end.setAlphaF(end.alphaF() * strength)
    body = QLinearGradient(box.topLeft(), box.topRight())
    body.setColorAt(0.0, start)
    body.setColorAt(1.0, end)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(body)
    painter.drawPath(body_path)

    # light pools towards the bottom edge
    sheen = paint_color(palette.glass_highlight)
    sheen.setAlphaF(sheen.alphaF() * strength * 0.7)
    clear = QColor(Qt.GlobalColor.white)
    clear.setAlphaF(0.0)
    painter.save()
    painter.setClipPath(body_path)
    lens = QLinearGradient(box.topLeft(), box.bottomLeft())
    lens.setColorAt(0.0, clear)
    lens.setColorAt(0.55, clear)
    lens.setColorAt(1.0, sheen)
    painter.setBrush(lens)
    painter.drawRect(box)
    painter.restore()

    # hairline rim: bright at the top, softer at the bottom
    rim = paint_color(palette.glass_border)
    light = QColor(rim)
    light.setAlphaF(min(1.0, rim.alphaF() * strength))
    dim = QColor(rim)
    dim.setAlphaF(min(1.0, rim.alphaF() * 0.45 * strength))
    rim_gradient = QLinearGradient(box.topLeft(), box.bottomLeft())
    rim_gradient.setColorAt(0.0, light)
    rim_gradient.setColorAt(0.5, light)
    rim_gradient.setColorAt(1.0, dim)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QBrush(rim_gradient), 1.0))
    painter.drawRoundedRect(box, radius, radius)
    painter.restore()


class SearchField(QLineEdit):
    """Line edit with a magnifier icon and a clear button."""

    def __init__(self, placeholder: str = "Поиск…", parent: QWidget | None = None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self._action = self.addAction(icons.icon("search", "#8b93a1", 16),
                                      QLineEdit.ActionPosition.LeadingPosition)
        self.setMinimumWidth(220)

    def set_icon_color(self, color: str) -> None:
        self._action.setIcon(icons.icon("search", color, 16))


class ElidedLabel(QLabel):
    """A one-line label that shortens itself with «…» instead of clipping.

    Page headers keep the title on the left and the actions on the right; on a
    narrow window a long title must give way, not push the buttons out of the
    viewport.
    """

    MAX_WIDTH = 720
    MIN_WIDTH = 140

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        min_width: int = MIN_WIDTH,
        max_width: int = MAX_WIDTH,
    ) -> None:
        super().__init__(text, parent)
        self._full = text
        self._min_width = min_width
        self._max_width = max_width
        # Ask for the width of the whole text (so it is never cut when there is
        # room) but never push the neighbours: the layout caps the width at
        # ``max_width``, and eliding keeps it readable down to ``min_width``.
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(min_width)
        self.setMaximumWidth(max_width)

    def sizeHint(self):  # noqa: N802 - Qt API
        self.ensurePolished()  # the QSS font size is known only after this
        metrics = QFontMetrics(self.font())
        width = min(metrics.horizontalAdvance(self._full) + 4, self._max_width)
        return QSize(max(width, self._min_width), super().sizeHint().height())

    def minimumSizeHint(self):  # noqa: N802 - Qt API
        return QSize(self._min_width, super().minimumSizeHint().height())

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        self._full = text
        super().setText(text)
        self.updateGeometry()
        self._elide()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        metrics = QFontMetrics(self.font())
        shown = metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, self.width())
        if shown != super().text():
            super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class ClickableFrame(QFrame):
    """A card that behaves like a button (hover, click and right click)."""

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setProperty("clickable", True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._menu_handler = None

    def set_menu_handler(self, handler) -> None:
        """``handler(global_pos)`` opens the context menu of this row."""
        self._menu_handler = handler
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._menu_handler is not None:
            self._menu_handler(event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

class ListRow(ClickableFrame):
    """A раздел / домашка row used inside the lesson and course pages."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        trailing: str = "",
        icon_name: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        self.icon_label: QLabel | None = None
        if icon_name:
            self.icon_label = QLabel()
            self.icon_label.setFixedSize(34, 34)
            self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.icon_label.setPixmap(icons.pixmap(icon_name, "#59616e", 18))
            layout.addWidget(self.icon_label)

        text_box = QVBoxLayout()
        text_box.setSpacing(3)
        self.title_label = label(title, "h3", wrap=True)
        self.title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        text_box.addWidget(self.title_label)
        self.subtitle_label: QLabel | None = None
        if subtitle:
            self.subtitle_label = label(subtitle, "muted", wrap=True)
            text_box.addWidget(self.subtitle_label)
        layout.addLayout(text_box, 1)

        if trailing:
            self.trailing = label(trailing, "muted")
            self.trailing.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(self.trailing)

        chevron = QLabel()
        chevron.setPixmap(icons.pixmap("chevron-right", "#8b93a1", 16))
        layout.addWidget(chevron)

    def set_icon(self, name: str, color: str) -> None:
        if self.icon_label:
            self.icon_label.setPixmap(icons.pixmap(name, color, 18))


class AutoHeightTextBrowser(QTextBrowser):
    """HTML view that grows with its content up to a maximum height.

    A plain ``QTextBrowser`` always reports a large size hint, which leaves a
    big empty block for short conditions.  Here the height follows the rendered
    document, so the card hugs its content.
    """

    def __init__(self, maximum: int = 1200, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._maximum = maximum
        self._last_width = -1
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            "QTextBrowser { background: transparent; border: none; margin: 0; padding: 0; }"
        )
        self.document().setDocumentMargin(2)
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._update_height()
        )

    # ------------------------------------------------------------------ #
    def _content_height(self, width: int) -> int:
        document = self.document()
        if document.textWidth() != width:
            document.setTextWidth(width)
        return int(document.documentLayout().documentSize().height())

    def _update_height(self) -> None:
        if sip.isdeleted(self):  # a deferred call may fire after the page is gone
            return
        width = self.viewport().width()
        if width <= 0:
            return
        if width == self._last_width and self.height() > 0:
            return
        # `chrome` covers frame/margins so the content never triggers a
        # scrollbar; +2 absorbs rounding.
        chrome = max(0, self.height() - self.viewport().height())
        target = max(28, min(self._content_height(width) + chrome + 2, self._maximum))
        if abs(self.height() - target) > 1:
            self.setFixedHeight(target)
        self._last_width = width

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        width = self.viewport().width()
        if width != self._last_width:
            self._update_height()

    def setHtml(self, html: str) -> None:  # noqa: N802 - Qt API
        super().setHtml(html)
        self._last_width = -1
        self._update_height()

    def fit_to_content(self) -> None:
        """Recompute the height once the widget is inside its final layout."""
        if sip.isdeleted(self):
            return
        self._last_width = -1
        self._update_height()


class SectionTitle(QWidget):
    """Quiet heading above a group of rows."""

    def __init__(self, text: str, count: int | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 12, 2, 0)
        layout.setSpacing(8)
        title = QLabel(text)
        font = QFont(title.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() + 0.5))
        font.setWeight(QFont.Weight.DemiBold)
        title.setFont(font)
        title.setProperty("role", "muted")
        layout.addWidget(title)
        if count is not None:
            layout.addWidget(label(str(count), "muted"))
        layout.addStretch(1)


class EmptyState(QWidget):
    """Friendly placeholder with an icon, a title and optional actions."""

    def __init__(
        self,
        icon_name: str,
        title: str,
        description: str = "",
        actions: Iterable[QPushButton] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 40, 24, 40)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel()
        self.icon_label.setPixmap(icons.pixmap(icon_name, "#8b93a1", 40))
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label)

        title_label = label(title, "h2")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)

        if description:
            desc = label(description, "muted", wrap=True)
            desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # Both bounds are needed so QLabel computes its height-for-width.
            desc.setMinimumWidth(460)
            desc.setMaximumWidth(460)
            layout.addWidget(desc, 0, Qt.AlignmentFlag.AlignHCenter)

        buttons = list(actions)
        if buttons:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addStretch(1)
            for button in buttons:
                row.addWidget(button)
            row.addStretch(1)
            layout.addSpacing(6)
            layout.addLayout(row)
        layout.addStretch(1)


def primary_button(text: str, icon_name: str | None = None, size: int = 16) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("variant", "primary")
    button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    if icon_name:
        ICONS.bind(button, icon_name, "on_accent", size)
    return button


def secondary_button(
    text: str,
    icon_name: str | None = None,
    size: int = 16,
    color_key: str = "text_secondary",
) -> QPushButton:
    button = QPushButton(text)
    button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    if icon_name:
        ICONS.bind(button, icon_name, color_key, size)
    return button


def ghost_button(
    text: str,
    icon_name: str | None = None,
    size: int = 16,
    color_key: str = "text_secondary",
) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("variant", "ghost")
    button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    if icon_name:
        ICONS.bind(button, icon_name, color_key, size)
    return button


class FlowLayout(QLayout):
    """Left-to-right layout that wraps — used for chips and badges."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)
        if parent is not None:
            self.setParent(parent)

    def addItem(self, item) -> None:  # noqa: N802 - Qt API
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 - Qt API
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802 - Qt API
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802 - Qt API
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt API
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt API
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def clear(self) -> None:
        while self._items:
            item = self._items.pop()
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _layout(self, rect: QRect, apply: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line_height = effective.x(), effective.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class IconBinder:
    """Keeps track of painted icons so they can be recoloured on theme change."""

    def __init__(self) -> None:
        self._items: list[tuple[weakref.ReferenceType, str, str, int]] = []

    def bind(self, target, name: str, color_key: str = "text_secondary", size: int = 16) -> None:
        def set_color(color: str) -> None:
            target.setIcon(icons.icon(name, color, size))
            with contextlib.suppress(AttributeError, TypeError):  # pragma: no cover
                target.setIconSize(QSize(size, size))

        if color_key == "white":
            set_color("#ffffff")
            return
        initial = {"muted": "#8b93a1", "on_accent": "#ffffff"}
        set_color(initial.get(color_key, "#59616e"))
        self._items.append((weakref.ref(target), name, color_key, size))

    def recolor(self, palette: Palette) -> None:
        alive = []
        for ref, name, color_key, size in self._items:
            target = ref()
            if target is None:
                continue
            color = getattr(palette, color_key, palette.muted)
            try:
                target.setIcon(icons.icon(name, color, size))
            except RuntimeError:  # deleted C++ object
                continue
            alive.append((ref, name, color_key, size))
        self._items = alive


ICONS = IconBinder()
