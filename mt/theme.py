"""Colour palettes and the global Qt stylesheet.

The whole look of the application is defined here.  Two palettes (light and
dark) share one stylesheet template, so switching themes is instant and no
widget needs to know about colours.
"""

from __future__ import annotations

from dataclasses import dataclass
from string import Template


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    surface: str
    surface_alt: str
    sidebar: str
    border: str
    border_strong: str
    text: str
    text_secondary: str
    muted: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    danger: str
    danger_soft: str
    selection: str
    shadow: str


LIGHT = Palette(
    name="light",
    bg="#f3f5f8",
    surface="#ffffff",
    surface_alt="#f8f9fb",
    sidebar="#fbfcfe",
    border="#e5e8ee",
    border_strong="#d5dae3",
    text="#161920",
    text_secondary="#59616e",
    muted="#8b93a1",
    accent="#4a63dd",
    accent_hover="#3d55c9",
    accent_pressed="#3348ad",
    accent_soft="#eceffe",
    success="#12795a",
    success_soft="#e4f5ed",
    warning="#a2660f",
    warning_soft="#fdf2e0",
    danger="#c53b3b",
    danger_soft="#fdecec",
    selection="#eef1fe",
    shadow="rgba(16, 24, 40, 0.06)",
)

DARK = Palette(
    name="dark",
    bg="#131519",
    surface="#1b1e24",
    surface_alt="#21252c",
    sidebar="#171a1f",
    border="#2a2f38",
    border_strong="#3a414c",
    text="#e9ecf1",
    text_secondary="#a9b2c0",
    muted="#7c8695",
    accent="#7389ff",
    accent_hover="#8b9dff",
    accent_pressed="#5b72e6",
    accent_soft="#232b45",
    success="#4ed3a0",
    success_soft="#152c23",
    warning="#e2ab55",
    warning_soft="#2b2416",
    danger="#ff8080",
    danger_soft="#2e1b1d",
    selection="#232a3d",
    shadow="rgba(0, 0, 0, 0.35)",
)

PALETTES = {"light": LIGHT, "dark": DARK}


def palette(name: str) -> Palette:
    return PALETTES.get(name, LIGHT)


STYLESHEET = Template("""
* { outline: none; }

QWidget {
    color: $text;
    font-size: 13px;
}

QMainWindow, QDialog, QWizard { background: $bg; }

QToolTip {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 6px 9px;
}

/* ---------------- menu ---------------- */
QMenuBar {
    background: $surface;
    border-bottom: 1px solid $border;
    padding: 2px 8px;
}
QMenuBar::item {
    background: transparent;
    padding: 6px 10px;
    border-radius: 6px;
}
QMenuBar::item:selected { background: $accent_soft; color: $accent; }
QMenu {
    background: $surface;
    border: 1px solid $border;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item {
    padding: 7px 28px 7px 12px;
    border-radius: 6px;
}
QMenu::item:selected { background: $accent_soft; color: $accent; }
QMenu::item:disabled { color: $muted; }
QMenu::separator { height: 1px; background: $border; margin: 6px 8px; }

/* ---------------- toolbars & status ---------------- */
QToolBar {
    background: $surface;
    border: none;
    border-bottom: 1px solid $border;
    padding: 8px 12px;
    spacing: 8px;
}
QToolBar::separator { background: $border; width: 1px; margin: 4px 6px; }
QStatusBar {
    background: $surface;
    border-top: 1px solid $border;
    color: $muted;
    padding: 2px 8px;
}
QStatusBar::item { border: none; }

/* ---------------- buttons ---------------- */
QPushButton {
    background: $surface;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 8px;
    padding: 7px 14px;
    font-weight: 500;
    min-height: 18px;
}
QPushButton:hover { background: $surface_alt; border-color: $muted; }
QPushButton:pressed { background: $bg; }
QPushButton:disabled {
    color: $muted;
    background: $surface_alt;
    border-color: $border;
}
QPushButton[variant="primary"] {
    background: $accent;
    border: 1px solid $accent;
    color: #ffffff;
}
QPushButton[variant="primary"]:hover {
    background: $accent_hover;
    border-color: $accent_hover;
}
QPushButton[variant="primary"]:pressed { background: $accent_pressed; }
QPushButton[variant="primary"]:disabled {
    background: $border_strong;
    border-color: $border_strong;
    color: $surface;
}
QPushButton[variant="ghost"] {
    background: transparent;
    border: 1px solid transparent;
    color: $text_secondary;
}
QPushButton[variant="ghost"]:hover {
    background: $surface_alt;
    border-color: $border;
    color: $text;
}
QPushButton[variant="danger"] {
    background: $danger_soft;
    border: 1px solid transparent;
    color: $danger;
}
QPushButton[variant="danger"]:hover { background: $danger; color: #ffffff; }
QPushButton[variant="subtle"] {
    background: $surface_alt;
    border: 1px solid $border;
    color: $text_secondary;
}
QPushButton[variant="subtle"]:hover { background: $surface; color: $text; }

QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 6px;
}
QToolButton:hover { background: $surface_alt; border-color: $border; }
QToolButton:pressed { background: $bg; }
QToolButton:checked { background: $accent_soft; border-color: transparent; }

/* ---------------- inputs ---------------- */
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox {
    background: $surface;
    border: 1px solid $border_strong;
    border-radius: 8px;
    padding: 7px 10px;
    selection-background-color: $accent;
    selection-color: #ffffff;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QTextBrowser:focus,
QComboBox:focus, QSpinBox:focus { border-color: $accent; }
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled {
    background: $surface_alt;
    color: $muted;
}
QComboBox { padding-right: 26px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: $surface;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: $accent_soft;
    selection-color: $accent;
}
QSpinBox::up-button, QSpinBox::down-button { width: 16px; border: none; background: transparent; }

QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }

/* ---------------- scrollbars ---------------- */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px 2px 2px 0;
}
QScrollBar::handle:vertical {
    background: $border_strong;
    min-height: 32px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover { background: $muted; }
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0 2px 2px 2px;
}
QScrollBar::handle:horizontal {
    background: $border_strong;
    min-width: 32px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover { background: $muted; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---------------- sidebar tree ---------------- */
QTreeWidget#sidebar {
    background: $sidebar;
    border: none;
    padding: 6px 8px;
    show-decoration-selected: 0;
}
QTreeWidget#sidebar::item {
    height: 34px;
    border-radius: 8px;
    color: $text_secondary;
    padding-left: 8px;
    margin: 1px 0;
}
QTreeWidget#sidebar::item:hover { background: $surface_alt; color: $text; }
QTreeWidget#sidebar::item:selected {
    background: $accent_soft;
    color: $accent;
}
/* the branch column must be painted explicitly, otherwise Qt draws the
   selection highlight behind the expand arrow */
QTreeWidget#sidebar::branch { background: $sidebar; border: none; }

/* ---------------- tables ---------------- */
QTableWidget, QTableView, QListWidget {
    background: $surface;
    border: 1px solid $border;
    border-radius: 10px;
    gridline-color: $border;
    alternate-background-color: $surface_alt;
    selection-background-color: $accent_soft;
    selection-color: $text;
}
QTableWidget::item, QListWidget::item { padding: 6px 8px; border: none; }
QTableWidget::item:selected, QListWidget::item:selected {
    background: $accent_soft;
    color: $text;
}
QHeaderView::section {
    background: $surface_alt;
    color: $muted;
    padding: 8px 10px;
    border: none;
    border-bottom: 1px solid $border;
    font-weight: 600;
    font-size: 12px;
}
QTableCornerButton::section { background: $surface_alt; border: none; }

/* ---------------- cards / labels ---------------- */
QFrame#card {
    background: $surface;
    border: 1px solid $border;
    border-radius: 12px;
}
QFrame#cardFlat {
    background: $surface_alt;
    border: 1px solid $border;
    border-radius: 10px;
}
QFrame#pageHeader {
    background: $surface;
    border-bottom: 1px solid $border;
}
QFrame#sidebarHeader, QFrame#sidebarFooter {
    background: $sidebar;
    border: none;
}
QFrame#sidebarFooter { border-top: 1px solid $border; }

QLabel[role="h1"] { font-size: 23px; font-weight: 700; color: $text; }
QLabel[role="h2"] { font-size: 16px; font-weight: 600; color: $text; }
QLabel[role="h3"] { font-size: 13px; font-weight: 600; color: $text; }
QLabel[role="muted"] { color: $muted; }
QLabel[role="secondary"] { color: $text_secondary; }
QLabel[role="accent"] { color: $accent; font-weight: 600; }
QLabel[role="stat"] { font-size: 26px; font-weight: 700; color: $text; }
QLabel[role="mono"] { font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; color: $text_secondary; }
QLabel[role="badge"] {
    background: $surface_alt;
    color: $text_secondary;
    border: 1px solid $border;
    border-radius: 9px;
    padding: 2px 9px;
    font-size: 11px;
    font-weight: 600;
}
QLabel[role="badge"][variant="accent"] {
    background: $accent_soft;
    color: $accent;
    border-color: transparent;
}
QLabel[role="badge"][variant="success"] {
    background: $success_soft;
    color: $success;
    border-color: transparent;
}
QLabel[role="badge"][variant="warning"] {
    background: $warning_soft;
    color: $warning;
    border-color: transparent;
}
QLabel[role="badge"][variant="danger"] {
    background: $danger_soft;
    color: $danger;
    border-color: transparent;
}

/* ---------------- misc ---------------- */
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:hover { background: $border; }

QProgressBar {
    background: $surface_alt;
    border: 1px solid $border;
    border-radius: 6px;
    text-align: center;
    color: $text_secondary;
    font-size: 11px;
}
QProgressBar::chunk { background: $accent; border-radius: 5px; }

QDockWidget { color: $text_secondary; }
QDockWidget::title {
    background: $surface_alt;
    padding: 7px 10px;
    border-top: 1px solid $border;
}
QPlainTextEdit#log {
    background: $surface;
    border: none;
    border-radius: 0;
    font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
    font-size: 12px;
    color: $text_secondary;
}

QDialog QLabel[role="h2"] { font-size: 17px; }
QMessageBox { background: $surface; }
QMessageBox QLabel { color: $text; }
QMessageBox QPushButton { min-width: 90px; }

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid $border_strong;
    border-radius: 4px;
    background: $surface;
}
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }
""")


def stylesheet(theme: str) -> str:
    pal = palette(theme)
    return STYLESHEET.substitute(vars(pal))
