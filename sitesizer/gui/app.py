"""GUI entry point: QApplication setup, logging, crash safety."""

from __future__ import annotations

import logging
import os
import shutil
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

from PySide6.QtCore import QCoreApplication, QSettings, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from .. import APP_NAME, LEGACY_APP_NAME, __version__
from .state import AppState, app_data_dir

log = logging.getLogger("sitesizer")


def migrate_legacy_data(
    settings: QSettings | None = None, legacy: QSettings | None = None, new_dir: Path | None = None
) -> bool:
    """Carry settings and user files (catalog, logs) over from the app's previous name.

    Runs once: only when the new location is still empty. Returns True if anything was moved.
    """
    moved = False
    settings = settings or QSettings()
    legacy = legacy or QSettings(LEGACY_APP_NAME, LEGACY_APP_NAME)
    if not settings.allKeys() and legacy.allKeys():
        for key in legacy.allKeys():
            settings.setValue(key, legacy.value(key))
        settings.sync()
        moved = True
    new_dir = new_dir or app_data_dir()  # <base>/<org>/<app>, the old one is <base>/SiteSizer/SiteSizer
    old_dir = new_dir.parent.parent / LEGACY_APP_NAME / LEGACY_APP_NAME
    if old_dir.is_dir() and old_dir != new_dir and not any(new_dir.iterdir()):
        shutil.copytree(old_dir, new_dir, dirs_exist_ok=True)
        moved = True
    return moved


def setup_logging() -> str:
    log_dir = app_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "localcount.log"
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if os.environ.get("SITESIZER_DEBUG"):
        root.addHandler(logging.StreamHandler())
        root.setLevel(logging.DEBUG)
    return str(path)


def install_excepthook(window_getter) -> None:
    """Log unexpected exceptions and show a toast instead of crashing the app."""

    def hook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        log.error("Unhandled exception:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        win = window_getter()
        if win is not None:
            try:
                from ..i18n import tr

                win.toasts.show("error", tr("ui.unexpected_error"), 8000)
            except Exception:
                pass

    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QCoreApplication.setOrganizationName(APP_NAME)
    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setApplicationVersion(__version__)
    app = QApplication(argv)
    app.setStyle("Fusion")
    try:
        migrated = migrate_legacy_data()
    except OSError:
        migrated = False
    log_path = setup_logging()
    log.info("%s %s starting, log: %s", APP_NAME, __version__, log_path)
    if migrated:
        log.info("Settings and catalog moved over from %s", LEGACY_APP_NAME)

    from .theme import load_fonts, set_font_family, theme_manager

    set_font_family(load_fonts())
    state = AppState(QSettings())
    theme_manager.apply(state.settings.theme, state.settings.ui_scale)
    hints = QGuiApplication.styleHints()
    if hints is not None:
        hints.colorSchemeChanged.connect(lambda _s: theme_manager.mode == "system" and theme_manager.apply())

    from .main_window import MainWindow

    window: list[MainWindow] = []
    install_excepthook(lambda: window[0] if window else None)
    win = MainWindow(state)
    window.append(win)
    project_arg = next((a for a in argv[1:] if a.endswith(".json")), None)
    if project_arg:
        win.open_path(project_arg, ask=False)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
