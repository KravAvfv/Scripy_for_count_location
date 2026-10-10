"""Dialogs, command palette and the first-run tour."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...i18n import keys, tr
from .. import icons
from ..theme import tokens
from .controls import button, label, px


def _shadow(w: QWidget, blur: int = 36, alpha: int = 60) -> None:
    fx = QGraphicsDropShadowEffect(w)
    fx.setBlurRadius(blur)
    fx.setOffset(0, 8)
    c = QColor(tokens().shadow)
    c.setAlpha(alpha if not tokens().dark else 160)
    fx.setColor(c)
    w.setGraphicsEffect(fx)


class ConfirmDialog(QDialog):
    """Calm, consistently styled confirmation dialog."""

    def __init__(
        self,
        parent: QWidget,
        title: str,
        text: str,
        buttons: list[tuple[str, str, str]],
        icon_name: str = "circle-help",
        icon_color: str = "info",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.choice = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(24), px(22), px(24), px(18))
        lay.setSpacing(px(14))
        head = QHBoxLayout()
        head.setSpacing(px(12))
        ic = QLabel()
        ic.setPixmap(icons.pixmap(icon_name, px(22), icon_color))
        head.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(px(6))
        col.addWidget(label(title, "subtitle"))
        body = label(text, "muted", wrap=True)
        body.setMinimumWidth(px(360))
        col.addWidget(body)
        head.addLayout(col, 1)
        lay.addLayout(head)
        row = QHBoxLayout()
        row.addStretch(1)
        for key, text_, variant in buttons:
            b = button(text_, variant or None)
            b.clicked.connect(lambda _=False, k=key: self._done(k))
            row.addWidget(b)
            if variant == "primary" or variant == "danger":
                b.setDefault(True)
        lay.addLayout(row)

    def _done(self, key: str) -> None:
        self.choice = key
        self.accept()


def confirm(parent: QWidget, title: str, text: str, ok: str, cancel: str | None = None, danger: bool = False) -> bool:
    dlg = ConfirmDialog(
        parent,
        title,
        text,
        [("cancel", cancel or tr("ui.cancel"), ""), ("ok", ok, "danger" if danger else "primary")],
        "triangle-alert" if danger else "circle-help",
        "error" if danger else "info",
    )
    dlg.exec()
    return dlg.choice == "ok"


def ask_save(parent: QWidget) -> str:
    """'save' | 'discard' | 'cancel'."""
    dlg = ConfirmDialog(
        parent,
        tr("ui.unsaved_title"),
        tr("ui.unsaved_text"),
        [("cancel", tr("ui.cancel"), ""), ("discard", tr("ui.discard"), "danger"), ("save", tr("ui.save"), "primary")],
        "save",
        "info",
    )
    dlg.exec()
    return dlg.choice or "cancel"


def prompt_text(parent: QWidget, title: str, text: str, value: str = "") -> str | None:
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(px(24), px(20), px(24), px(18))
    lay.setSpacing(px(12))
    lay.addWidget(label(title, "subtitle"))
    lay.addWidget(label(text, "muted", wrap=True))
    edit = QLineEdit(value)
    edit.selectAll()
    lay.addWidget(edit)
    row = QHBoxLayout()
    row.addStretch(1)
    cancel = button(tr("ui.cancel"))
    ok = button(tr("ui.ok"), "primary")
    ok.setDefault(True)
    cancel.clicked.connect(dlg.reject)
    ok.clicked.connect(dlg.accept)
    row.addWidget(cancel)
    row.addWidget(ok)
    lay.addLayout(row)
    dlg.setMinimumWidth(px(420))
    return edit.text().strip() if dlg.exec() == QDialog.DialogCode.Accepted else None


# ---------------------------------------------------------------------------------------------
# command palette
# ---------------------------------------------------------------------------------------------
@dataclass
class Command:
    title: str
    callback: Callable[[], None]
    icon: str = "command"
    shortcut: str = ""
    group: str = ""
    keywords: str = ""


def fuzzy_score(query: str, text: str) -> int | None:
    """Simple subsequence score: lower is better, None = no match."""
    q = query.lower().strip()
    t = text.lower()
    if not q:
        return 0
    if q in t:
        return t.index(q)
    pos = -1
    gaps = 0
    for ch in q:
        nxt = t.find(ch, pos + 1)
        if nxt < 0:
            return None
        gaps += nxt - pos - 1
        pos = nxt
    return 100 + gaps


class CommandPalette(QDialog):
    def __init__(self, parent: QWidget, commands: list[Command]) -> None:
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.commands = commands
        outer = QVBoxLayout(self)
        outer.setContentsMargins(px(20), px(20), px(20), px(28))
        frame = QFrame()
        frame.setObjectName("Card")
        _shadow(frame)
        outer.addWidget(frame)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(px(10), px(10), px(10), px(10))
        lay.setSpacing(px(8))
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(tr("ui.palette_ph"))
        self.edit.addAction(icons.icon("search", size=15), QLineEdit.ActionPosition.LeadingPosition)
        self.edit.setStyleSheet(f"font-size: {px(15)}px; padding: {px(8)}px;")
        lay.addWidget(self.edit)
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setStyleSheet(
            "QListWidget { border: none; background: transparent; }"
            f"QListWidget::item {{ padding: {px(7)}px {px(8)}px; border-radius: {px(6)}px; }}"
        )
        self.list.setIconSize(QSize(px(16), px(16)))
        lay.addWidget(self.list)
        self.hint = label(tr("ui.palette_hint"), "faint")
        lay.addWidget(self.hint)
        self.edit.textChanged.connect(self._filter)
        self.edit.installEventFilter(self)
        self.list.itemActivated.connect(self._run)
        self.list.itemClicked.connect(self._run)
        self.resize(px(600), px(460))
        self._filter("")

    def _filter(self, text: str) -> None:
        self.list.clear()
        scored = []
        for c in self.commands:
            s = fuzzy_score(text, f"{c.title} {c.keywords} {c.group}")
            if s is not None:
                scored.append((s, c))
        scored.sort(key=lambda x: x[0])
        for _s, c in scored[:40]:
            it = QListWidgetItem(
                icons.icon(c.icon, size=16), c.title + (f"     {keys(c.shortcut)}" if c.shortcut else "")
            )
            it.setData(Qt.ItemDataRole.UserRole, c)
            it.setToolTip(c.group)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if obj is self.edit and isinstance(e, QKeyEvent) and e.type() == QEvent.Type.KeyPress:
            if e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                row = self.list.currentRow() + (1 if e.key() == Qt.Key.Key_Down else -1)
                self.list.setCurrentRow(max(0, min(self.list.count() - 1, row)))
                return True
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                it = self.list.currentItem()
                if it:
                    self._run(it)
                return True
            if e.key() == Qt.Key.Key_Escape:
                self.reject()
                return True
        return False

    def _run(self, item: QListWidgetItem) -> None:
        cmd: Command = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        cmd.callback()

    def popup(self, anchor: QWidget) -> None:
        g = anchor.mapToGlobal(QPoint(0, 0))
        self.move(g.x() + (anchor.width() - self.width()) // 2, g.y() + px(70))
        self.edit.setFocus()
        self.exec()


# ---------------------------------------------------------------------------------------------
# first-run tour
# ---------------------------------------------------------------------------------------------
@dataclass
class TourStep:
    target: Callable[[], QWidget | None]
    title: str
    text: str
    before: Callable[[], None] | None = None


class TourOverlay(QWidget):
    """Dims the window except the highlighted widget and shows a step card."""

    finished = Signal()

    def __init__(self, host: QWidget, steps: list[TourStep]) -> None:
        super().__init__(host)
        self.host = host
        self.steps = steps
        self.i = 0
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.card = QFrame(self)
        self.card.setObjectName("Card")
        _shadow(self.card)
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(px(18), px(16), px(18), px(14))
        cl.setSpacing(px(8))
        self.step_label = label("", "faint")
        self.title = label("", "subtitle")
        self.text = label("", "muted", wrap=True)
        self.text.setFixedWidth(px(320))
        cl.addWidget(self.step_label)
        cl.addWidget(self.title)
        cl.addWidget(self.text)
        row = QHBoxLayout()
        self.skip = button(tr("ui.tour_skip"), "ghost")
        self.back = button(tr("ui.tour_back"))
        self.next = button(tr("ui.tour_next"), "primary")
        row.addWidget(self.skip)
        row.addStretch(1)
        row.addWidget(self.back)
        row.addWidget(self.next)
        cl.addLayout(row)
        self.skip.clicked.connect(self.close_tour)
        self.back.clicked.connect(lambda: self.go(self.i - 1))
        self.next.clicked.connect(lambda: self.go(self.i + 1))
        host.installEventFilter(self)
        self.setGeometry(host.rect())
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def start(self) -> None:
        self.show()
        self.raise_()
        self.go(0)
        self.setFocus()

    def go(self, i: int) -> None:
        if i >= len(self.steps):
            self.close_tour()
            return
        self.i = max(0, i)
        step = self.steps[self.i]
        if step.before:
            step.before()
        self.step_label.setText(tr("ui.tour_step", i=self.i + 1, n=len(self.steps)))
        self.title.setText(step.title)
        self.text.setText(step.text)
        self.back.setEnabled(self.i > 0)
        self.next.setText(tr("ui.tour_done") if self.i == len(self.steps) - 1 else tr("ui.tour_next"))
        self.card.adjustSize()
        self._place()
        self.update()

    def _target_rect(self) -> QRect:
        w = self.steps[self.i].target()
        if w is None or not w.isVisible():
            return QRect()
        top_left = w.mapTo(self.host, QPoint(0, 0))
        rect = QRect(top_left, w.size())
        # clip to the part that is actually visible inside scroll areas
        parent = w.parentWidget()
        while parent is not None and parent is not self.host:
            vis = QRect(parent.mapTo(self.host, QPoint(0, 0)), parent.size())
            rect = rect.intersected(vis)
            parent = parent.parentWidget()
        return rect.adjusted(-6, -6, 6, 6) if not rect.isEmpty() else QRect()

    def _place(self) -> None:
        r = self._target_rect()
        cw, ch = self.card.width(), self.card.height()
        if r.isNull():
            self.card.move((self.width() - cw) // 2, (self.height() - ch) // 2)
            return
        x = r.right() + px(16)
        y = r.top()
        if x + cw > self.width() - px(12):
            x = max(px(12), r.left() - cw - px(16))
        if x < px(12) or (r.left() - cw - px(16) < px(12) and r.right() + px(16) + cw > self.width()):
            x = max(px(12), min(self.width() - cw - px(12), r.center().x() - cw // 2))
            y = r.bottom() + px(14) if r.bottom() + ch + px(20) < self.height() else r.top() - ch - px(14)
        y = max(px(12), min(self.height() - ch - px(12), y))
        self.card.move(x, y)

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))
        r = self._target_rect()
        if not r.isNull():
            hole = QPainterPath()
            hole.addRoundedRect(QRectF(r), px(10), px(10))
            path = path.subtracted(hole)
        p.fillPath(path, QColor(10, 16, 24, 120))
        if not r.isNull():
            p.setPen(QPen(QColor(tokens().focus), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(r), px(10), px(10))
        p.end()

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if obj is self.host and e.type() == QEvent.Type.Resize:
            self.setGeometry(self.host.rect())
            self._place()
        return False

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.close_tour()
        elif e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.go(self.i + 1)
        elif e.key() == Qt.Key.Key_Left:
            self.go(self.i - 1)

    def mousePressEvent(self, _e) -> None:
        pass  # swallow clicks on the dimmed area

    def close_tour(self) -> None:
        self.hide()
        self.finished.emit()
        self.deleteLater()


def _keep(_: object = QPushButton) -> None:
    pass
