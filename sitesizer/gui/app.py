"""GUI entry point: QApplication setup, logging, crash safety."""

from __future__ import annotations

import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler
from types import TracebackType

from PySide6.QtCore import QCoreApplication, QSettings, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from .. import __version__
from .state import AppState, app_data_dir

log = logging.getLogger("sitesizer")


def setup_logging() -> str:
    log_dir = app_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "sitesizer.log"
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
    QCoreApplication.setOrganizationName("SiteSizer")
    QCoreApplication.setApplicationName("SiteSizer")
    QCoreApplication.setApplicationVersion(__version__)
    app = QApplication(argv)
    app.setStyle("Fusion")
    log_path = setup_logging()
    log.info("SiteSizer %s starting, log: %s", __version__, log_path)

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
