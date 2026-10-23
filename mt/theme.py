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
    rail: str
    rail_hover: str
    rail_active: str
    on_accent: str
    # «liquid glass»: translucent surfaces for selection and the dark rail
    glass_start: str
    glass_end: str
    glass_border: str
    glass_highlight: str
    glass_shadow: str
    rail_shadow: str


LIGHT = Palette(
    name="light",
    bg="#8f8f8f",
    surface="#a8a8a8",
    surface_alt="#b2b2b2",
    sidebar="#a8a8a8",
    border="rgba(255, 255, 255, 0.48)",
    border_strong="rgba(255, 255, 255, 0.72)",
    text="#1a1a1c",
    text_secondary="#3d3d40",
    muted="#5e5e62",
    accent="#1d1d1f",
    accent_hover="#2e2e31",
    accent_pressed="#000000",
    accent_soft="rgba(255, 255, 255, 0.18)",
    success="#14523a",
    success_soft="rgba(20, 82, 58, 0.16)",
    warning="#6d490f",
    warning_soft="rgba(109, 73, 15, 0.16)",
    danger="#8c2f2f",
    danger_soft="rgba(140, 47, 47, 0.16)",
    selection="rgba(255, 255, 255, 0.42)",
    rail="#232325",
    rail_hover="rgba(255, 255, 255, 0.10)",
    rail_active="rgba(255, 255, 255, 0.16)",
    on_accent="#ffffff",
    glass_start="rgba(242, 255, 251, 0.12)",
    glass_end="rgba(242, 255, 251, 0.34)",
    glass_border="rgba(255, 255, 255, 0.46)",
    glass_highlight="rgba(255, 255, 255, 0.30)",
    glass_shadow="rgba(28, 32, 31, 0.22)",
    rail_shadow="rgba(22, 22, 26, 0.30)",
)

DARK = Palette(
    name="dark",
    bg="#0e0e0f",
    surface="#19191a",
    surface_alt="#232324",
    sidebar="#19191a",
    border="rgba(255, 255, 255, 0.10)",
    border_strong="rgba(255, 255, 255, 0.18)",
    text="#ededed",
    text_secondary="#b8b8bb",
    muted="#808085",
    accent="#ededed",
    accent_hover="#ffffff",
    accent_pressed="#d5d5d5",
    accent_soft="rgba(255, 255, 255, 0.10)",
    success="#5fd6a4",
    success_soft="rgba(95, 214, 164, 0.16)",
    warning="#e5b264",
    warning_soft="rgba(229, 178, 100, 0.16)",
    danger="#ff8b8b",
    danger_soft="rgba(255, 139, 139, 0.16)",
    selection="rgba(255, 255, 255, 0.12)",
    rail="#0b0b0c",
    rail_hover="rgba(255, 255, 255, 0.08)",
    rail_active="rgba(255, 255, 255, 0.14)",
    on_accent="#141414",
    glass_start="rgba(255, 255, 255, 0.06)",
    glass_end="rgba(255, 255, 255, 0.16)",
    glass_border="rgba(255, 255, 255, 0.30)",
    glass_highlight="rgba(255, 255, 255, 0.12)",
    glass_shadow="rgba(0, 0, 0, 0.55)",
    rail_shadow="rgba(0, 0, 0, 0.70)",
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
    border-radius: 26px;
}
QToolButton#railButton {
    background: transparent;
    border: none;
    border-radius: 15px;
}
QToolButton#railButton { border: 1px solid transparent; }
QToolButton#railButton:hover { background: $rail_hover; }
QToolButton#navRow {
    background: transparent;
    border: none;
    border-radius: 12px;
    padding: 0 12px;
    color: $text_secondary;
    font-size: 14px;
    text-align: left;
}
QToolButton#navRow:hover { color: $text; }
QToolButton#navRow:checked { color: $text; }
QToolButton#railButton:checked {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(255, 255, 255, 0.24), stop:1 rgba(255, 255, 255, 0.10));
    border: 1px solid rgba(255, 255, 255, 0.18);
}
QToolButton#railButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(255, 255, 255, 0.28), stop:1 rgba(255, 255, 255, 0.14));
}
QToolButton#railLogo {
    background: transparent;
    border: none;
    border-radius: 15px;
}
QToolButton#railLogo:hover { background: $rail_hover; }

QFrame#sidePanel {
    background: $sidebar;
    border: 1px solid rgba(255, 255, 255, 0.28);
    border-radius: 28px;
}
QFrame#content {
    background: $surface;
    border: 1px solid rgba(255, 255, 255, 0.28);
    border-radius: 28px;
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
    padding: 0 22px 6px 22px;
}
QStatusBar::item { border: none; }

/* ---------------- buttons ---------------- */
QPushButton {
    background: $surface_alt;
    color: $text;
    border: 1px solid $border;
    border-radius: 13px;
    padding: 9px 16px;
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
    background: $accent_soft;
    border: 1px solid $border;
    border-radius: 12px;
    padding: 8px 12px;
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
    font-size: 14px;
}
QTreeWidget#sidebar::item {
    height: 38px;
    border-radius: 12px;
    color: $text_secondary;
    padding-left: 6px;
    margin: 2px 0;
}
/* The row background is a glass pill painted by SidebarTree (drawBranches),
   so the style must keep the item and branch strips transparent — otherwise
   its flat selection colour covers the left cap of the pill. */
QTreeWidget#sidebar::item:hover { background: transparent; color: $text; }
QTreeWidget#sidebar::item:selected { background: transparent; color: $accent; }
QTreeWidget#sidebar::item:selected:!active { background: transparent; color: $text; }
QTreeWidget#sidebar::branch { background: transparent; border: none; }
QTreeWidget#sidebar::branch:selected,
QTreeWidget#sidebar::branch:selected:!active,
QTreeWidget#sidebar::branch:hover { background: transparent; }

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
    border: none;
    border-radius: 20px;
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

QLabel[role="h1"] { font-size: 34px; font-weight: 600; color: $text; }
QLabel[role="h2"] { font-size: 17px; font-weight: 600; color: $text; }
QLabel[role="h3"] { font-size: 14px; font-weight: 600; color: $text; }
QLabel[role="muted"] { color: $muted; font-size: 13px; }
QLabel[role="secondary"] { color: $text_secondary; }
QLabel[role="accent"] { color: $accent; font-weight: 600; }
QLabel[role="stat"] { font-size: 40px; font-weight: 700; color: $text; }
QLabel[role="statSmall"] { font-size: 24px; font-weight: 600; color: $text; }
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
