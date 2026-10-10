"""Views refresh only while they are shown.

Every edit recomputes the location and broadcasts the result; repainting the hidden pages too
(cabinets, specification, power, comparison…) made typing feel sluggish. :class:`WhenShown`
runs a slot right away when its view is visible and otherwise keeps the latest call for the
moment the view is shown.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QWidget


class WhenShown(QObject):
    """A slot wrapper owned by ``view`` (so the connection goes away with the view)."""

    def __init__(self, view: QWidget, fn: Callable[..., Any]) -> None:
        super().__init__(view)
        self.view = view
        self.fn = fn
        self.pending: tuple[Any, ...] | None = None
        view.installEventFilter(self)

    def __call__(self, *args: Any) -> None:
        if self.view.isVisible():
            self.pending = None
            self.fn(*args)
        else:
            self.pending = args

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.view and event.type() == QEvent.Type.Show and self.pending is not None:
            args, self.pending = self.pending, None
            self.fn(*args)
        return False


def when_shown(view: QWidget, fn: Callable[..., Any]) -> Callable[..., None]:
    """``signal.connect(when_shown(self, self.on_result))``."""
    return WhenShown(view, fn).__call__
