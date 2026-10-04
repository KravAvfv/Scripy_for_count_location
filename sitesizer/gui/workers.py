"""Run slow jobs (Excel/PDF export) off the UI thread."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)


class _Signals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class Job(QRunnable):
    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = _Signals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            out = self.fn()
        except Exception as err:  # reported to the UI as a toast
            log.error("Background job failed:\n%s", traceback.format_exc())
            self.signals.failed.emit(str(err) or err.__class__.__name__)
            return
        self.signals.finished.emit(out)


def run_in_background(fn: Callable[[], Any], on_done: Callable[[Any], None], on_error: Callable[[str], None]) -> Job:
    job = Job(fn)
    job.signals.finished.connect(on_done)
    job.signals.failed.connect(on_error)
    QThreadPool.globalInstance().start(job)
    return job
