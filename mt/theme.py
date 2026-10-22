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
    rail: str
    rail_hover: str
    rail_active: str
    on_accent: str


LIGHT = Palette(
    name="light",
    bg="#e6e6e6",
    surface="#fbfbfb",
    surface_alt="#f3f3f3",
    sidebar="#f7f7f7",
    border="#ebebeb",
    border_strong="#dadada",
    text="#141414",
    text_secondary="#575757",
    muted="#8c8c8c",
    accent="#1f1f1f",
    accent_hover="#333333",
    accent_pressed="#000000",
    accent_soft="#ececec",
    success="#1f6b4d",
    success_soft="#e6f2ec",
    warning="#8a5a12",
    warning_soft="#f6eddd",
    danger="#b23b3b",
    danger_soft="#f8e8e8",
    selection="#efefef",
    shadow="rgba(0, 0, 0, 0.07)",
    rail="#1b1b1d",
    rail_hover="rgba(255, 255, 255, 0.10)",
    rail_active="rgba(255, 255, 255, 0.16)",
    on_accent="#ffffff",
)

DARK = Palette(
    name="dark",
    bg="#0d0d0e",
    surface="#18181a",
    surface_alt="#212123",
    sidebar="#141416",
    border="#262628",
    border_strong="#353537",
    text="#ededed",
    text_secondary="#b3b3b6",
    muted="#7e7e83",
    accent="#ededed",
    accent_hover="#ffffff",
    accent_pressed="#d5d5d5",
    accent_soft="#2a2a2d",
    success="#5fd6a4",
    success_soft="#152a22",
    warning="#e5b264",
    warning_soft="#2b2316",
    danger="#ff8b8b",
    danger_soft="#2e1a1a",
    selection="#26262a",
    shadow="rgba(0, 0, 0, 0.45)",
    rail="#08080a",
    rail_hover="rgba(255, 255, 255, 0.08)",
    rail_active="rgba(255, 255, 255, 0.14)",
    on_accent="#141414",
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
QWidget#shell { background: $bg; }

/* ---------------- app shell ---------------- */
QFrame#rail {
    background: $rail;
    border: none;
    border-radius: 24px;
}
QToolButton#railButton {
    background: transparent;
    border: none;
    border-radius: 13px;
}
QToolButton#railButton:hover { background: $rail_hover; }
QToolButton#railButton:checked { background: $rail_active; }
QToolButton#railButton:pressed { background: $rail_active; }
QToolButton#railLogo {
    background: $rail_active;
    border: none;
    border-radius: 15px;
}
QToolButton#railLogo:hover { background: $rail_hover; }

QFrame#sidePanel {
    background: $sidebar;
    border: none;
    border-radius: 22px;
}
QFrame#content {
    background: $surface;
    border: none;
    border-radius: 22px;
}
QFrame#content QWidget#pageBody { background: transparent; }

QToolButton#sectionToggle {
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 2px;
}
QToolButton#sectionToggle:hover { background: $surface_alt; }
QToolButton#moreActions {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 6px 8px;
    color: $text_secondary;
    font-weight: 700;
}
QToolButton#moreActions:hover { background: $surface_alt; color: $text; }
QToolButton#moreActions::menu-indicator { image: none; width: 0; }

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
    background: transparent;
    border: none;
    color: $muted;
    padding: 2px 18px 8px 18px;
}
QStatusBar::item { border: none; }

/* ---------------- buttons ---------------- */
QPushButton {
    background: $surface;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 10px;
    padding: 8px 15px;
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
    color: $on_accent;
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
QPushButton[variant="danger"]:hover { background: $danger; color: $on_accent; }
QPushButton[variant="subtle"] {
    background: $surface_alt;
    border: 1px solid $border;
    color: $text_secondary;
}
QPushButton[variant="subtle"]:hover { background: $surface; color: $text; }

QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
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
    selection-color: $on_accent;
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
    background: transparent;
    border: none;
    padding: 2px 6px;
    show-decoration-selected: 0;
    outline: 0;
}
QTreeWidget#sidebar::item {
    height: 32px;
    border-radius: 9px;
    color: $text_secondary;
    padding-left: 6px;
    margin: 1px 0;
}
QTreeWidget#sidebar::item:hover { background: $surface_alt; color: $text; }
QTreeWidget#sidebar::item:selected {
    background: $accent_soft;
    color: $accent;
}
/* The strip left of the items (expand arrows) is painted by SidebarTree:
   the style must not draw its own selection panel there. */
QTreeWidget#sidebar::branch { background: transparent; border: none; }
QTreeWidget#sidebar::branch:selected { background: transparent; }
QTreeWidget#sidebar::item:selected:!active {
    background: $accent_soft;
    color: $text;
}

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
    background: $surface_alt;
    border: 1px solid transparent;
    border-radius: 18px;
}
QFrame#card[clickable="true"]:hover { background: $selection; }
QFrame#cardFlat {
    background: $surface;
    border: 1px solid $border;
    border-radius: 12px;
}
QFrame#pageHeader {
    background: transparent;
    border: none;
}
QFrame#sidebarHeader, QFrame#sidebarFooter {
    background: transparent;
    border: none;
}
QFrame#sidebarFooter { border-top: 1px solid $border; }

QLabel[role="h1"] { font-size: 27px; font-weight: 700; color: $text; }
QLabel[role="h2"] { font-size: 16px; font-weight: 600; color: $text; }
QLabel[role="h3"] { font-size: 13px; font-weight: 600; color: $text; }
QLabel[role="muted"] { color: $muted; }
QLabel[role="secondary"] { color: $text_secondary; }
QLabel[role="accent"] { color: $accent; font-weight: 600; }
QLabel[role="stat"] { font-size: 30px; font-weight: 700; color: $text; }
QLabel[role="statSmall"] { font-size: 19px; font-weight: 600; color: $text; margin-bottom: 8px; }
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
QLabel[role="badge"][variant="outline"] {
    background: transparent;
    color: $success;
    border: 1px solid $success;
}

/* ---------------- misc ---------------- */
QSplitter::handle { background: transparent; }

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
