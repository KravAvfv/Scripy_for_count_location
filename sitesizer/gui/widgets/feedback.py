"""Toasts and the warnings/checks panel."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.models import Check, Severity
from ...i18n import tr
from .. import icons
from ..theme import tokens
from .controls import Callout, label, px


class Toast(QFrame):
    closed = Signal()

    def __init__(
        self,
        parent: QWidget,
        severity: str,
        text: str,
        busy: bool = False,
        action: tuple[str, Callable[[], None]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("callout", severity)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(px(14), px(10), px(10), px(10))
        lay.setSpacing(px(10))
        ic = QLabel()
        color = getattr(tokens(), severity, tokens().info)
        ic.setPixmap(icons.pixmap(Callout.ICONS.get(severity, "info"), px(16), color))
        lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(px(6))
        self.label = label(text, wrap=True)
        self.label.setMaximumWidth(px(380))
        col.addWidget(self.label)
        self.progress: QProgressBar | None = None
        if busy:
            self.progress = QProgressBar()
            self.progress.setRange(0, 0)
            self.progress.setFixedHeight(px(4))
            col.addWidget(self.progress)
        lay.addLayout(col, 1)
        if action:
            b = QPushButton(action[0])
            b.setProperty("variant", "ghost")
            b.clicked.connect(action[1])
            b.clicked.connect(self.dismiss)
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        close = QPushButton()
        close.setProperty("variant", "icon")
        close.setIcon(icons.icon("x", size=14))
        close.setToolTip(tr("ui.close"))
        close.clicked.connect(self.dismiss)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def show_animated(self) -> None:
        self.show()
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def dismiss(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self._finish)
        self._anim.start()

    def _finish(self) -> None:
        self.hide()
        self.closed.emit()
        self.deleteLater()


class ToastHost(QObject):
    """Stacks toasts in the bottom-right corner of ``host``."""

    def __init__(self, host: QWidget, bottom_offset: Callable[[], int] | None = None) -> None:
        super().__init__(host)
        self.host = host
        self.bottom_offset = bottom_offset or (lambda: 0)
        self.toasts: list[Toast] = []
        host.installEventFilter(self)

    def show(
        self,
        severity: str,
        text: str,
        timeout_ms: int = 4200,
        busy: bool = False,
        action: tuple[str, Callable[[], None]] | None = None,
    ) -> Toast:
        toast = Toast(self.host, severity, text, busy, action)
        toast.closed.connect(lambda: self._remove(toast))
        self.toasts.append(toast)
        toast.adjustSize()
        self._relayout()
        toast.show_animated()
        toast.raise_()
        if timeout_ms and not busy:
            QTimer.singleShot(timeout_ms, lambda: toast.dismiss() if toast in self.toasts else None)
        return toast

    def _remove(self, toast: Toast) -> None:
        if toast in self.toasts:
            self.toasts.remove(toast)
        self._relayout()

    def _relayout(self) -> None:
        y = self.host.height() - self.bottom_offset() - px(16)
        for toast in reversed(self.toasts):
            toast.adjustSize()
            y -= toast.height()
            toast.move(QPoint(self.host.width() - toast.width() - px(20), y))
            y -= px(10)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.host and event.type() == QEvent.Type.Resize:
            self._relayout()
        return False


SEVERITY_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


class ChecksPanel(QWidget):
    """List of checks as callouts; emits ``action(code)`` for actionable items."""

    action = Signal(str, str)  # action id, choice

    def __init__(self, parent: QWidget | None = None, compact: bool = True) -> None:
        super().__init__(parent)
        self.compact = compact
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(px(8))
        self._widgets: list[QWidget] = []
        self._shown: list[tuple[str, str, str, str]] | None = None

    def set_checks(self, checks: list[Check], force: bool = False) -> None:
        key = [(c.severity.value, c.message, c.hint, c.action) for c in checks]
        if key == self._shown and not force:
            return  # same findings: keep the widgets (rebuilding them on every keystroke is slow)
        self._shown = key
        for w in self._widgets:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        self._widgets.clear()
        if not checks:
            ok = Callout("success", tr("ui.checks_ok"), compact=self.compact)
            self.lay.addWidget(ok)
            self._widgets.append(ok)
            return
        for c in sorted(checks, key=lambda c: SEVERITY_ORDER[c.severity]):
            box = Callout(c.severity.value, c.message, c.hint, compact=self.compact)
            if c.action == "confirm_psu":
                yes = box.add_action(tr("ui.psu_confirm_yes"), "primary")
                no = box.add_action(tr("ui.psu_confirm_no"))
                yes.clicked.connect(lambda _=False: self.action.emit("confirm_psu", "yes"))
                no.clicked.connect(lambda _=False: self.action.emit("confirm_psu", "no"))
                box.actions.addStretch(1)
            self.lay.addWidget(box)
            self._widgets.append(box)


def severity_color(sev: Severity) -> str:
    t = tokens()
    return {Severity.ERROR: t.error, Severity.WARNING: t.warning, Severity.INFO: t.info}[sev]
