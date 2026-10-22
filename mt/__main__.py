"""Entry point: ``python -m mt`` / ``maximumtest``."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import traceback


def _prepare_platform() -> None:
    """Wayland-first on Hyprland / Wayland sessions, X11 fallback everywhere."""
    if os.environ.get("QT_QPA_PLATFORM"):
        return
    if (
        os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        or os.environ.get("XDG_SESSION_TYPE") == "wayland"
        or os.environ.get("WAYLAND_DISPLAY")
    ):
        os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"
        os.environ.setdefault("QT_WAYLAND_DISABLE_WINDOWDECORATION", "1")


def install_excepthook() -> None:
    """Show uncaught errors instead of letting Qt abort the process.

    By default PyQt6 turns an exception raised inside a slot into ``qFatal``
    (``Aborted (core dumped)``) with no chance to react.  With a custom
    ``sys.excepthook`` the traceback is printed, the user sees a dialog and the
    application keeps running.
    """

    def hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        sys.stderr.write("".join(traceback.format_exception(exc_type, exc, tb)))
        sys.stderr.flush()
        with contextlib.suppress(Exception):
            from PyQt6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is None:
                return
            box = QMessageBox()
            box.setWindowTitle("Непредвиденная ошибка")
            box.setIcon(QMessageBox.Icon.Critical)
            box.setText(f"{exc_type.__name__}: {exc}")
            box.setInformativeText(
                "Приложение продолжает работу. Подробности — в выводе терминала."
            )
            box.exec()

    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="maximumtest",
        description="MaximumTest Library — просмотр заданий из локального кэша.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.2.0")
    parser.add_argument("--db", metavar="PATH", help="использовать другую базу SQLite")
    parser.add_argument("--data-dir", metavar="DIR", help="каталог данных (вместо XDG)")
    parser.add_argument("--theme", choices=("light", "dark", "system"), help="тема оформления")
    parser.add_argument("--sync", metavar="SUBJECT_ID", type=int,
                        help="синхронизировать предмет и выйти")
    args = parser.parse_args(argv)

    if args.data_dir:
        from pathlib import Path

        base = Path(args.data_dir).expanduser()
        os.environ["MT_DATA_DIR"] = str(base)
        os.environ["MT_CONFIG_DIR"] = str(base / "config")
        os.environ["MT_CACHE_DIR"] = str(base / "cache")

    _prepare_platform()

    # Imported late so --version / --help do not require Qt.
    from PyQt6.QtCore import QSettings
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QApplication

    from . import config, db

    config.ensure_dirs()
    if args.db:
        db.set_database_path(args.db)
    db.init()

    app = QApplication(sys.argv[:1])
    app.setApplicationName(config.APP_NAME)
    app.setApplicationDisplayName(config.APP_TITLE)
    app.setApplicationVersion(config.APP_VERSION)
    app.setOrganizationName(config.ORG_NAME)
    app.setDesktopFileName(config.APP_NAME)  # Hyprland windowrulev2 name

    font = QFont()
    font.setFamilies([
        "Inter", "SF Pro Text", "Segoe UI", "Roboto", "Noto Sans",
        "Ubuntu", "Cantarell", "DejaVu Sans",
    ])
    font.setPointSizeF(10.0)
    app.setFont(font)

    if args.theme:
        QSettings().setValue("ui/theme", args.theme)

    if args.sync:
        return _run_sync(args.sync)

    install_excepthook()
    from .ui import MainWindow

    window = MainWindow()
    window.show()
    return app.exec()


def _run_sync(subject_id: int) -> int:
    """Headless synchronisation, useful for cron jobs and scripts."""
    from PyQt6.QtCore import QCoreApplication, Qt

    from . import config, db
    from .api import Api, ApiError
    from .models import extract_kbs_entries
    from .sync import SyncThread

    config.ensure_dirs()
    app = QCoreApplication(sys.argv[:1])  # noqa: F841 - needed for QThread signals
    api = Api()
    try:
        base = api.get_curriculum_subject(subject_id)
    except ApiError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    total = len(extract_kbs_entries(base))
    print(f"Предмет #{subject_id}, разделов: {total}")

    thread = SyncThread(api, [subject_id])
    # No event loop runs here, so deliver signals directly from the worker.
    thread.log.connect(lambda line: print(line, flush=True), type=Qt.ConnectionType.DirectConnection)
    results: list = []
    thread.finished_all.connect(results.extend, type=Qt.ConnectionType.DirectConnection)
    thread.start()
    thread.wait()
    if not results or results[0].error:
        message = results[0].error if results else "синхронизация не запустилась"
        print(f"Ошибка: {message}", file=sys.stderr)
        return 1
    stats = db.subject_stats(subject_id)
    print(f"Готово: {stats['kbs']} разделов, {stats['total']} тестов, "
          f"{stats['supported']} с ответом")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
