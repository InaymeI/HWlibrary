import os
import sys


def main():
    # Wayland-first for Hyprland, fall back to X11
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        os.environ.setdefault("QT_QPA_PLATFORM", "wayland;xcb")
        os.environ.setdefault("QT_WAYLAND_DISABLE_WINDOWDECORATION", "1")

    from PyQt6.QtWidgets import QApplication
    from . import db
    from .ui import MainWindow

    db.init()
    app = QApplication(sys.argv)
    app.setApplicationName("MaximumTest Desktop")
    app.setApplicationDisplayName("MaximumTest Desktop")
    app.setOrganizationName("maximumtest-desktop")
    app.setDesktopFileName("maximumtest-desktop")  # used by Hyprland windowrulev2

    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
