"""Crisp vector icons drawn with QPainter.

Using painted icons keeps the app dependency free (no SVG files, no icon
themes) and lets every icon follow the active colour palette.
"""

from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

_VIEWBOX = 24.0


def _pen(color: str, width: float = 1.8) -> QPen:
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _draw(name: str, painter: QPainter, color: str) -> None:
    pen = _pen(color)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    def line(x1, y1, x2, y2):
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def poly(*points):
        path = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            path.lineTo(QPointF(*point))
        painter.drawPath(path)

    def dot(x, y, radius=1.35):
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(x, y), radius, radius)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    if name == "search":
        painter.drawEllipse(QPointF(10.6, 10.6), 6.1, 6.1)
        line(15.2, 15.2, 20.0, 20.0)
    elif name == "refresh":
        rect = QRectF(4.4, 4.4, 15.2, 15.2)
        painter.drawArc(rect, 35 * 16, 285 * 16)
        poly((18.9, 4.9), (19.0, 9.4), (14.6, 8.4))
    elif name == "plus":
        line(12, 5.2, 12, 18.8)
        line(5.2, 12, 18.8, 12)
    elif name == "cookie":
        painter.drawEllipse(QPointF(12, 12), 8.0, 8.0)
        dot(9.2, 9.0)
        dot(14.6, 10.4)
        dot(10.4, 15.0)
        dot(15.0, 15.6, 1.0)
    elif name == "key":
        painter.drawEllipse(QPointF(8.3, 9.4), 3.5, 3.5)
        line(10.8, 12.0, 19.2, 20.4)
        line(16.2, 17.4, 17.8, 15.8)
        line(18.2, 19.4, 19.8, 17.8)
    elif name == "terminal":
        painter.drawRoundedRect(QRectF(3.4, 4.4, 17.2, 15.2), 3.0, 3.0)
        poly((7.6, 9.4), (10.0, 12.0), (7.6, 14.6))
        line(11.8, 15.0, 16.0, 15.0)
    elif name == "sun":
        painter.drawEllipse(QPointF(12, 12), 4.2, 4.2)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0),
                       (-0.7, -0.7), (0.7, -0.7), (-0.7, 0.7), (0.7, 0.7)):
            norm = (dx * dx + dy * dy) ** 0.5
            ux, uy = dx / norm, dy / norm
            line(12 + ux * 6.6, 12 + uy * 6.6, 12 + ux * 9.0, 12 + uy * 9.0)
    elif name == "moon":
        outer = QPainterPath()
        outer.addEllipse(QPointF(12, 12), 7.6, 7.6)
        inner = QPainterPath()
        inner.addEllipse(QPointF(15.6, 9.2), 7.0, 7.0)
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(outer.subtracted(inner))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
    elif name == "copy":
        painter.drawRoundedRect(QRectF(8.6, 8.6, 10.8, 10.8), 2.4, 2.4)
        poly((15.4, 5.4), (5.4, 5.4), (5.4, 15.4))
    elif name == "send":
        path = QPainterPath(QPointF(20.2, 3.8))
        path.lineTo(QPointF(4.2, 10.8))
        path.lineTo(QPointF(10.9, 13.4))
        path.lineTo(QPointF(13.4, 20.2))
        path.closeSubpath()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        line(10.9, 13.4, 20.2, 3.8)
    elif name == "check":
        poly((5.4, 12.6), (9.8, 17.0), (18.6, 7.2))
    elif name == "close":
        line(6.4, 6.4, 17.6, 17.6)
        line(17.6, 6.4, 6.4, 17.6)
    elif name == "chevron-right":
        poly((9.4, 5.6), (15.8, 12.0), (9.4, 18.4))
    elif name == "chevron-left":
        poly((14.6, 5.6), (8.2, 12.0), (14.6, 18.4))
    elif name == "chevron-down":
        poly((6.0, 9.4), (12.0, 15.4), (18.0, 9.4))
    elif name == "trash":
        line(4.8, 7.0, 19.2, 7.0)
        poly((9.4, 7.0), (9.4, 4.8), (14.6, 4.8), (14.6, 7.0))
        poly((6.8, 7.0), (7.8, 20.0), (16.2, 20.0), (17.2, 7.0))
        line(10.4, 10.6, 10.8, 16.6)
        line(13.6, 10.6, 13.2, 16.6)
    elif name == "book":
        painter.drawRoundedRect(QRectF(4.6, 4.4, 3.8, 15.2), 1.2, 1.2)
        painter.drawRoundedRect(QRectF(10.1, 3.6, 3.8, 16.0), 1.2, 1.2)
        painter.drawRoundedRect(QRectF(15.6, 6.0, 3.8, 13.6), 1.2, 1.2)
    elif name == "external":
        poly((8.6, 15.4), (19.0, 5.0))
        poly((12.4, 5.0), (19.0, 5.0), (19.0, 11.6))
        poly((6.0, 5.4), (4.6, 5.4), (4.6, 19.4), (18.6, 19.4), (18.6, 18.0))
    elif name == "info":
        painter.drawEllipse(QPointF(12, 12), 7.8, 7.8)
        dot(12, 8.0, 1.15)
        line(12, 11.2, 12, 16.4)
    elif name == "download":
        line(12, 4.0, 12, 15.2)
        poly((7.6, 10.8), (12, 15.2), (16.4, 10.8))
        line(4.8, 19.4, 19.2, 19.4)
    elif name == "filter":
        painter.drawRoundedRect(QRectF(4.2, 5.0, 15.6, 3.4), 1.7, 1.7)
        painter.drawRoundedRect(QRectF(7.0, 10.5, 10.0, 3.4), 1.7, 1.7)
        painter.drawRoundedRect(QRectF(9.6, 16.0, 4.8, 3.2), 1.6, 1.6)
    elif name == "layers":
        poly((12, 3.6), (20.4, 8.0), (12, 12.4), (3.6, 8.0), (12, 3.6))
        poly((4.2, 12.0), (12, 16.0), (19.8, 12.0))
        poly((4.2, 16.0), (12, 20.0), (19.8, 16.0))
    elif name == "grid":
        painter.drawRoundedRect(QRectF(4.2, 4.2, 6.4, 6.4), 1.6, 1.6)
        painter.drawRoundedRect(QRectF(13.4, 4.2, 6.4, 6.4), 1.6, 1.6)
        painter.drawRoundedRect(QRectF(4.2, 13.4, 6.4, 6.4), 1.6, 1.6)
        painter.drawRoundedRect(QRectF(13.4, 13.4, 6.4, 6.4), 1.6, 1.6)
    elif name == "arrow-left":
        line(19.4, 12, 5.0, 12)
        poly((9.6, 7.4), (5.0, 12), (9.6, 16.6))
    elif name == "alert":
        poly((12, 4.2), (20.8, 19.4), (3.2, 19.4), (12, 4.2))
        line(12, 9.6, 12, 13.8)
        dot(12, 16.6, 1.1)
    elif name == "clock":
        painter.drawEllipse(QPointF(12, 12), 7.8, 7.8)
        poly((12, 7.4), (12, 12.2), (15.6, 14.2))
    elif name == "settings":
        painter.drawEllipse(QPointF(12, 12), 3.2, 3.2)
        painter.drawEllipse(QPointF(12, 12), 7.4, 7.4)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0),
                       (-0.71, -0.71), (0.71, -0.71), (-0.71, 0.71), (0.71, 0.71)):
            line(12 + dx * 7.4, 12 + dy * 7.4, 12 + dx * 9.6, 12 + dy * 9.6)
    else:  # fallback: a dot so a typo never crashes the UI
        dot(12, 12, 3.0)


@lru_cache(maxsize=512)
def pixmap(name: str, color: str, size: int = 18, ratio: float = 2.0) -> QPixmap:
    px = int(size * ratio)
    canvas = QPixmap(px, px)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(px / _VIEWBOX, px / _VIEWBOX)
    _draw(name, painter, color)
    painter.end()
    canvas.setDevicePixelRatio(ratio)
    return canvas


@lru_cache(maxsize=512)
def icon(name: str, color: str = "#59616e", size: int = 18) -> QIcon:
    result = QIcon()
    result.addPixmap(pixmap(name, color, size))
    return result
