"""Entry point: ``python -m mt`` / ``maximumtest``."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import traceback

from . import config


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
    parser.add_argument("--version", action="version", version=f"%(prog)s {config.APP_VERSION}")
    parser.add_argument("--db", metavar="PATH", help="использовать другую базу SQLite")
    parser.add_argument("--data-dir", metavar="DIR", help="каталог данных (вместо XDG)")
    parser.add_argument("--theme", choices=("light", "dark", "system"), help="тема оформления")
    parser.add_argument("--sync", metavar="SUBJECT_ID", type=int,
                        help="синхронизировать домашку (subject id) и выйти")
    parser.add_argument("--course", metavar="COURSE_ID", type=int,
                        help="синхронизировать предмет (уроки, домашки, задания) и выйти")
    parser.add_argument("--link", metavar="URL",
                        help="ссылка на предмет, урок или задание — синхронизировать и выйти")
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

    from . import db

    config.ensure_dirs()
    if args.db:
        db.set_database_path(args.db)
    db.init()

    # Headless modes never create a QApplication: they work over SSH / cron
    # without a display and do not need a Qt platform plugin.
    if args.link:
        from .links import parse_link

        link = parse_link(args.link)
        if link is None:
            print(f"Не удалось разобрать ссылку: {args.link}", file=sys.stderr)
            return 2
        if link.is_course and link.course_id:
            return _run_sync([], [link.course_id])
        if link.subject_id:
            return _run_sync([link.subject_id], [])
        print("В ссылке не найдена домашка", file=sys.stderr)
        return 2
    if args.course:
        return _run_sync([], [args.course])
    if args.sync:
        return _run_sync([args.sync])

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

    install_excepthook()
    from .ui import MainWindow

    window = MainWindow()
    window.show()
    return app.exec()


def _run_sync(subject_ids: list[int], course_ids: list[int] | None = None) -> int:
    """Headless synchronisation, useful for cron jobs and scripts."""
    from PyQt6.QtCore import QCoreApplication, Qt

    from . import db
    from .api import Api
    from .sync import SyncThread

    config.ensure_dirs()
    app = QCoreApplication(sys.argv[:1])  # noqa: F841 - needed for QThread signals
    api = Api()

    thread = SyncThread(api, subject_ids, course_ids)
    # No event loop runs here, so deliver signals directly from the worker.
    thread.log.connect(
        lambda line: print(line, flush=True), type=Qt.ConnectionType.DirectConnection
    )
    results: list = []
    thread.finished_all.connect(results.extend, type=Qt.ConnectionType.DirectConnection)
    thread.start()
    thread.wait()

    if not results:
        print("Ошибка: синхронизация не запустилась", file=sys.stderr)
        return 1
    failed = False
    for result in results:
        if result.error:
            failed = True
            print(f"Ошибка: {result.error}", file=sys.stderr)
            continue
        if result.course_id:
            stats = db.course_stats(result.course_id)
            print(
                f"Предмет #{result.course_id}: уроков {result.lessons} · "
                f"домашек {stats['homeworks']} · разделов {stats['sections']} · "
                f"тестов {stats['total']}, с ответом {stats['supported']}"
            )
        else:
            stats = db.subject_stats(result.subject_id)
            print(
                f"Домашка #{result.subject_id}: разделов {stats['kbs']} · "
                f"тестов {stats['total']}, с ответом {stats['supported']}"
            )
    if not failed:
        total = sum(result.tests for result in results)
        print(f"Готово: тестов {total}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
