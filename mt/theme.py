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
    # --- Frutiger Aero extras (empty in the flat themes) ------------------
    #: Canvas: a bright sky behind the glass panels.
    sky_top: str = ""
    sky_mid: str = ""
    sky_bottom: str = ""
    sky_glow: str = ""
    #: Glossy bubbles floating on the canvas.
    bubble: str = ""
    bubble_rim: str = ""
    #: Bottom taskbar (Windows 7 style) and the glass panels.
    taskbar_top: str = ""
    taskbar_bottom: str = ""
    taskbar_border: str = ""
    panel_top: str = ""
    panel_bottom: str = ""
    panel_edge: str = ""
    #: Fill of a glossy selection pill / button highlight.
    gloss_top: str = ""
    gloss_bottom: str = ""


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

#: Frutiger Aero / Windows 7: a bright sky, white glass panels, an aqua-blue
#: taskbar and glossy pills.  Every extra field above is used only here.
AERO = Palette(
    name="aero",
    bg="#3f9ad6",
    surface="rgba(247, 253, 255, 0.86)",
    surface_alt="rgba(228, 246, 255, 0.80)",
    sidebar="rgba(242, 251, 255, 0.82)",
    border="rgba(255, 255, 255, 0.95)",
    border_strong="rgba(255, 255, 255, 1.0)",
    text="#0a2742",
    text_secondary="#26557c",
    muted="#5a7f9e",
    accent="#0f6cb4",
    accent_hover="#1a86d6",
    accent_pressed="#0a5088",
    accent_soft="rgba(255, 255, 255, 0.55)",
    success="#1a7f45",
    success_soft="rgba(163, 255, 205, 0.45)",
    warning="#8a5c00",
    warning_soft="rgba(255, 226, 150, 0.50)",
    danger="#b03028",
    danger_soft="rgba(255, 178, 170, 0.45)",
    selection="rgba(255, 255, 255, 0.62)",
    rail="rgba(9, 45, 78, 0.90)",
    rail_hover="rgba(255, 255, 255, 0.22)",
    rail_active="rgba(255, 255, 255, 0.34)",
    on_accent="#ffffff",
    glass_start="rgba(255, 255, 255, 0.62)",
    glass_end="rgba(255, 255, 255, 0.86)",
    glass_border="rgba(255, 255, 255, 0.95)",
    glass_highlight="rgba(255, 255, 255, 0.55)",
    glass_shadow="rgba(8, 42, 72, 0.40)",
    rail_shadow="rgba(8, 42, 72, 0.42)",
    sky_top="#11558f",
    sky_mid="#3f9ad6",
    sky_bottom="#a6e5f2",
    sky_glow="rgba(255, 255, 255, 0.55)",
    bubble="rgba(255, 255, 255, 0.16)",
    bubble_rim="rgba(255, 255, 255, 0.55)",
    taskbar_top="rgba(46, 108, 158, 0.94)",
    taskbar_bottom="rgba(9, 40, 70, 0.94)",
    taskbar_border="rgba(255, 255, 255, 0.55)",
    panel_top="rgba(255, 255, 255, 0.92)",
    panel_bottom="rgba(214, 240, 254, 0.86)",
    panel_edge="rgba(255, 255, 255, 0.98)",
    gloss_top="rgba(255, 255, 255, 0.96)",
    gloss_bottom="rgba(150, 212, 246, 0.92)",
)

PALETTES = {"light": LIGHT, "dark": DARK, "aero": AERO}


def palette(name: str) -> Palette:
    return PALETTES.get(name, LIGHT)


def is_aero(palette: Palette) -> bool:
    """True for the Frutiger Aero theme (sky canvas + taskbar chrome)."""
    return palette.name == "aero"


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

/* ======================= Frutiger Aero (Windows 7) ======================= */
$aero
""")


#: Extra rules appended for the Aero theme only.  Later rules win, so this
#: block repaints the flat grey chrome as glossy sky-blue glass.
AERO_QSS = """
QMainWindow, QDialog, QWizard, QWidget#shell { background: $bg; }

/* --- glass panels: white rim, glossy vertical gradient --- */
QFrame#sidePanel, QFrame#content {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 $panel_top, stop:0.55 $panel_top, stop:1 $panel_bottom);
    border: 1px solid $panel_edge;
    border-radius: 18px;
}
QFrame#card {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(255, 255, 255, 0.95), stop:1 rgba(222, 244, 255, 0.78));
    border: 1px solid rgba(255, 255, 255, 0.90);
    border-radius: 12px;
}
QFrame#cardFlat {
    background: rgba(255, 255, 255, 0.60);
    border: 1px solid rgba(255, 255, 255, 0.92);
    border-radius: 9px;
}
QFrame#card[clickable="true"]:hover { background: rgba(255, 255, 255, 0.90); }

/* --- taskbar: the rail becomes a Windows 7 glass bar --- */
QFrame#rail[chrome="taskbar"] {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 $taskbar_top, stop:0.5 $taskbar_top, stop:1 $taskbar_bottom);
    border: 1px solid $taskbar_border;
    border-radius: 14px;
}
QToolButton#railButton[chrome="taskbar"] {
    border-radius: 8px;
    border: 1px solid transparent;
}
QToolButton#railButton[chrome="taskbar"]:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(255, 255, 255, 0.30), stop:1 rgba(255, 255, 255, 0.12));
    border: 1px solid rgba(255, 255, 255, 0.45);
}
QToolButton#railButton[chrome="taskbar"]:checked {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(176, 226, 255, 0.62), stop:1 rgba(92, 168, 226, 0.42));
    border: 1px solid rgba(255, 255, 255, 0.80);
}
QToolButton#startOrb { background: transparent; border: none; }
QWidget#railTray { background: transparent; }
QLabel#trayStatus, QLabel#trayClock { color: #eefaff; font-size: 12px; }
QFrame#railDivider { background: rgba(255, 255, 255, 0.28); border: none; }

/* --- glossy buttons, like the 2009 control panel --- */
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ffffff, stop:0.48 #f0fbff, stop:0.52 #d6eefc, stop:1 #b6dff6);
    border: 1px solid #8cc3e2;
    border-radius: 6px;
    padding: 7px 14px;
}
QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ffffff, stop:0.48 #ffffff, stop:0.52 #e4f6ff, stop:1 #c8ebff);
    border-color: #5aa8d8;
}
QPushButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #b6dff6, stop:0.5 #cfeafa, stop:1 #eaf8ff);
}
QPushButton:disabled {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(255, 255, 255, 0.70), stop:1 rgba(226, 240, 248, 0.70));
    border-color: rgba(160, 194, 214, 0.60);
    color: $muted;
}
QPushButton[variant="primary"] {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #8fd8ff, stop:0.45 #3a9ede, stop:0.55 #1f7dc4, stop:1 #0f5f9f);
    border: 1px solid #0a4a7d;
    color: #ffffff;
    font-weight: 600;
}
QPushButton[variant="primary"]:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #b3e6ff, stop:0.45 #57b4ee, stop:0.55 #2a8ed4, stop:1 #1470b3);
}
QPushButton[variant="primary"]:pressed {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #0f5f9f, stop:0.5 #1f7dc4, stop:1 #3a9ede);
}
QPushButton[variant="primary"]:disabled {
    background: rgba(255, 255, 255, 0.55);
    border-color: rgba(150, 190, 215, 0.55);
    color: $muted;
}
QPushButton[variant="ghost"] {
    background: transparent;
    border: 1px solid transparent;
    color: $text_secondary;
}
QPushButton[variant="ghost"]:hover {
    background: rgba(255, 255, 255, 0.70);
    border-color: rgba(255, 255, 255, 0.95);
    color: $text;
}
QPushButton[variant="subtle"] {
    background: rgba(255, 255, 255, 0.65);
    border: 1px solid rgba(255, 255, 255, 0.95);
    color: $text_secondary;
}
QPushButton[variant="danger"] {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ffe9e6, stop:1 #f3c3bc);
    border: 1px solid #d79a92;
    color: $danger;
}
QPushButton[variant="danger"]:hover { background: #e8a9a2; color: #ffffff; }

QToolButton:hover { background: rgba(255, 255, 255, 0.60); border-color: rgba(255, 255, 255, 0.90); }
QToolButton:pressed { background: rgba(255, 255, 255, 0.85); }
QToolButton:checked { background: rgba(255, 255, 255, 0.75); }

/* --- inputs: white wells, blue focus ring --- */
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox {
    background: rgba(255, 255, 255, 0.92);
    border: 1px solid #9fcfe8;
    border-radius: 6px;
    padding: 6px 10px;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QTextBrowser:focus,
QComboBox:focus, QSpinBox:focus {
    border: 1px solid #2f8fd0;
    background: #ffffff;
}
QLineEdit#sidebarSearch {
    border-radius: 12px;
    padding: 7px 12px;
    background: rgba(255, 255, 255, 0.94);
    border: 1px solid #a8d4ea;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #8cc3e2;
    border-radius: 4px;
}

/* --- menus look like the 7 start menu --- */
QMenu {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ffffff, stop:1 #e6f5ff);
    border: 1px solid #7fb9dd;
    border-radius: 8px;
    padding: 6px;
}
QMenu::item { border-radius: 4px; padding: 7px 26px 7px 12px; }
QMenu::item:selected {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #d8f1ff, stop:1 #a9dcf8);
    color: $text;
}
QMenu::separator { background: #bcdcee; height: 1px; margin: 5px 8px; }
QMenuBar { background: transparent; border: none; }

/* --- tables and headers --- */
QTableWidget, QTableView, QListWidget {
    background: rgba(255, 255, 255, 0.88);
    border: 1px solid rgba(255, 255, 255, 0.95);
    border-radius: 8px;
}
QHeaderView::section {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ffffff, stop:1 #dceffb);
    color: $text_secondary;
    border-bottom: 1px solid #bcdcee;
}
QTableWidget::item:selected, QListWidget::item:selected {
    background: rgba(160, 219, 250, 0.55);
    color: $text;
}
QTableCornerButton::section { background: #eaf6fe; }

/* --- the tree sits in a white well like the Explorer nav pane --- */
QTreeWidget#sidebar {
    background: rgba(255, 255, 255, 0.55);
    border: 1px solid rgba(255, 255, 255, 0.90);
    border-radius: 10px;
    padding: 4px;
}
QTreeWidget#sidebar::item { color: $text_secondary; }
QTreeWidget#sidebar::item:selected { color: $text; }

/* --- status bar / log --- */
QStatusBar { color: $muted; }
QPlainTextEdit#log {
    background: rgba(255, 255, 255, 0.88);
    border: 1px solid rgba(255, 255, 255, 0.95);
    border-radius: 6px;
}
QToolTip {
    background: #ffffff;
    border: 1px solid #7fb9dd;
    border-radius: 4px;
    color: $text;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: rgba(120, 175, 210, 0.55);
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: rgba(90, 150, 195, 0.75);
}
QProgressBar {
    background: rgba(255, 255, 255, 0.80);
    border: 1px solid #9fcfe8;
    border-radius: 5px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #a9e4ff, stop:1 #2f8fd0);
    border-radius: 4px;
}
QCheckBox::indicator {
    background: #ffffff;
    border: 1px solid #9fcfe8;
    border-radius: 3px;
}
QCheckBox::indicator:checked { background: #2f8fd0; border-color: #2f8fd0; }
"""


def stylesheet(theme: str) -> str:
    pal = palette(theme)
    aero = Template(AERO_QSS).substitute(vars(pal)) if theme == "aero" else ""
    return STYLESHEET.substitute(**vars(pal), aero=aero)
