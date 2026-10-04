"""Reusable, theme-aware controls: cards, steppers, toggles, segmented controls, chips, callouts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QToolTip,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)

from .. import icons
from ..theme import theme_manager, tokens

ANIM_MS = 150


def px(v: float) -> int:
    return theme_manager.px(v)


def set_role(label: QLabel, role: str) -> QLabel:
    label.setProperty("role", role)
    return label


def label(text: str = "", role: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    if selectable:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


def button(
    text: str = "",
    variant: str | None = None,
    icon_name: str | None = None,
    tooltip: str = "",
    icon_color: str | None = None,
) -> QPushButton:
    b = QPushButton(text.replace("&", "&&"))
    if variant:
        b.setProperty("variant", variant)
    if icon_name:
        color = icon_color or ("#FFFFFF" if variant == "primary" else None)
        b.setIcon(icons.icon(icon_name, color, size=16))
        b.setIconSize(QSize(px(16), px(16)))
        b.setProperty("icon_name", icon_name)
        b.setProperty("icon_color", color or "")
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def icon_button(icon_name: str, tooltip: str, color: str | None = None, size: int = 18) -> QPushButton:
    b = QPushButton()
    b.setProperty("variant", "icon")
    b.setIcon(icons.icon(icon_name, color, size=size))
    b.setIconSize(QSize(px(size), px(size)))
    b.setToolTip(tooltip)
    b.setAccessibleName(tooltip)
    b.setProperty("icon_name", icon_name)
    b.setProperty("icon_color", color or "")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def refresh_icons(root: QWidget) -> None:
    """Re-tint every button icon under ``root`` after a theme change."""
    for b in root.findChildren(QPushButton):
        name = b.property("icon_name")
        if name:
            color = b.property("icon_color") or None
            b.setIcon(icons.icon(str(name), color, size=16))


def clear_layout(lay: QLayout | None) -> None:
    """Remove and delete every item of ``lay`` (recursively), hiding widgets immediately."""
    if lay is None:
        return
    while lay.count():
        item = lay.takeAt(0)
        if item is None:
            continue
        w = item.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())
            item.layout().deleteLater()


def hline() -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    f.setFrameShape(QFrame.Shape.NoFrame)
    return f


def vline() -> QFrame:
    f = QFrame()
    f.setObjectName("VDivider")
    f.setFrameShape(QFrame.Shape.NoFrame)
    return f


class Card(QFrame):
    """Rounded surface with a border; optional title row."""

    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        parent: QWidget | None = None,
        margins: int = 16,
        spacing: int = 12,
        soft: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SoftCard" if soft else "Card")
        self.body = QVBoxLayout(self)
        m = px(margins)
        self.body.setContentsMargins(m, m, m, m)
        self.body.setSpacing(px(spacing))
        self.header: QHBoxLayout | None = None
        self.title_label: QLabel | None = None
        if title:
            self.header = QHBoxLayout()
            self.header.setSpacing(px(8))
            col = QVBoxLayout()
            col.setSpacing(px(2))
            self.title_label = label(title, "subtitle")
            col.addWidget(self.title_label)
            if subtitle:
                self.subtitle_label = label(subtitle, "caption", wrap=True)
                col.addWidget(self.subtitle_label)
            self.header.addLayout(col, 1)
            self.body.addLayout(self.header)

    def add(self, w: QWidget | QLayout, stretch: int = 0) -> None:
        if isinstance(w, QLayout):
            self.body.addLayout(w, stretch)
        else:
            self.body.addWidget(w, stretch)


class InfoTip(QLabel):
    """Small (i) icon that shows a rich tooltip immediately on hover or keyboard focus."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self.setFixedSize(px(16), px(16))
        self.setPixmap(icons.pixmap("info", px(14), "text_faint"))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setAccessibleDescription(text)
        self.setToolTip(f"<div style='max-width:340px'>{text}</div>")
        theme_manager.changed.connect(self._retint)

    def set_text(self, text: str) -> None:
        self._text = text
        self.setToolTip(f"<div style='max-width:340px'>{text}</div>")

    def _retint(self) -> None:
        self.setPixmap(icons.pixmap("info", px(14), "text_faint"))

    def enterEvent(self, event: QEvent) -> None:
        QToolTip.showText(self.mapToGlobal(QPoint(px(18), 0)), self.toolTip(), self)
        super().enterEvent(event)  # type: ignore[arg-type]

    def focusInEvent(self, event: QEvent) -> None:
        QToolTip.showText(self.mapToGlobal(QPoint(px(18), 0)), self.toolTip(), self)
        super().focusInEvent(event)  # type: ignore[arg-type]


class _NoWheelMixin:
    """Ignore the mouse wheel unless the field has focus (no accidental edits while scrolling)."""

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.hasFocus():  # type: ignore[attr-defined]
            super().wheelEvent(event)  # type: ignore[misc]
        else:
            event.ignore()


class SpinBox(_NoWheelMixin, QSpinBox):
    pass


class DoubleSpinBox(_NoWheelMixin, QDoubleSpinBox):
    pass


class Stepper(QWidget):
    """[−] [ value ] [+] with keyboard entry; emits ``valueChanged(int)``."""

    valueChanged = Signal(int)

    def __init__(
        self,
        minimum: int = 0,
        maximum: int = 100_000,
        step: int = 1,
        suffix: str = "",
        parent: QWidget | None = None,
        width: int = 132,
    ) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.minus = QPushButton("−")
        self.minus.setObjectName("StepperMinus")
        self.plus = QPushButton("+")
        self.plus.setObjectName("StepperPlus")
        for b in (self.minus, self.plus):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(350)
            b.setAutoRepeatInterval(60)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.field = SpinBox()
        self.field.setObjectName("StepperField")
        self.field.setRange(minimum, maximum)
        self.field.setSingleStep(step)
        self.field.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.field.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.field.setKeyboardTracking(True)
        self.field.setAccelerated(True)
        if suffix:
            self.field.setSuffix(suffix)
        lay.addWidget(self.minus)
        lay.addWidget(self.field, 1)
        lay.addWidget(self.plus)
        self.setFixedWidth(px(width))
        self.minus.clicked.connect(self.field.stepDown)
        self.plus.clicked.connect(self.field.stepUp)
        self.field.valueChanged.connect(self.valueChanged)
        self.setFocusProxy(self.field)

    def value(self) -> int:
        return int(self.field.value())

    def setValue(self, v: int) -> None:
        if self.field.value() != v:
            self.field.blockSignals(True)
            self.field.setValue(v)
            self.field.blockSignals(False)


class ToggleSwitch(QAbstractButton):
    """iOS/Fluent-like switch with a 150 ms knob animation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        theme_manager.changed.connect(self.update)

    def sizeHint(self) -> QSize:
        return QSize(px(36), px(20))

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def setChecked(self, checked: bool) -> None:
        super().setChecked(checked)
        self._anim.stop()
        self._pos = 1.0 if checked else 0.0
        self.update()

    def paintEvent(self, _e: QPaintEvent) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        h = min(r.height(), px(20))
        track = QRectF(r.left(), r.center().y() - h / 2, min(r.width(), px(36)), h)
        on = QColor(t.primary if not t.dark else t.focus)
        off = QColor(t.border_strong)
        c = QColor(
            round(off.red() + (on.red() - off.red()) * self._pos),
            round(off.green() + (on.green() - off.green()) * self._pos),
            round(off.blue() + (on.blue() - off.blue()) * self._pos),
        )
        if not self.isEnabled():
            c.setAlphaF(0.45)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(track, h / 2, h / 2)
        d = h - px(4)
        x = track.left() + px(2) + (track.width() - d - px(4)) * self._pos
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, track.top() + px(2), d, d))
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(t.focus), 1.5))
            p.drawRoundedRect(track.adjusted(-2, -2, 2, 2), h / 2 + 2, h / 2 + 2)
        p.end()

    def hitButton(self, pos: QPoint) -> bool:
        return self.rect().contains(pos)


class ToggleRow(QWidget):
    """Title + caption on the left, switch on the right. Clicking the text toggles too."""

    toggled = Signal(bool)

    def __init__(self, title: str, caption: str = "", info: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(px(12))
        text = QVBoxLayout()
        text.setSpacing(px(2))
        head = QHBoxLayout()
        head.setSpacing(px(6))
        self.title = label(title)
        self.title.setStyleSheet("font-weight: 500;")
        head.addWidget(self.title)
        self.badge = label("", "faint")
        head.addWidget(self.badge)
        if info:
            head.addWidget(InfoTip(info))
        head.addStretch(1)
        text.addLayout(head)
        self.caption = label(caption, "caption", wrap=True)
        self.caption.setVisible(bool(caption))
        text.addWidget(self.caption)
        lay.addLayout(text, 1)
        self.switch = ToggleSwitch()
        self.switch.setAccessibleName(title)
        lay.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignTop)
        self.switch.toggled.connect(self.toggled)
        self.title.mousePressEvent = lambda _e: self.switch.toggle()  # type: ignore[method-assign]
        self.setFocusProxy(self.switch)

    def isChecked(self) -> bool:
        return self.switch.isChecked()

    def setChecked(self, v: bool) -> None:
        if self.switch.isChecked() != v:
            self.switch.blockSignals(True)
            self.switch.setChecked(v)
            self.switch.blockSignals(False)

    def set_badge(self, text: str) -> None:
        self.badge.setText(text)


class SegmentedControl(QWidget):
    """Horizontal single-choice control with a sliding selection pill. Arrow keys move."""

    valueChanged = Signal(object)

    def __init__(
        self,
        options: Sequence[tuple[Any, str]],
        tooltips: Sequence[str] | None = None,
        parent: QWidget | None = None,
        expand: bool = True,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self._options = list(options)
        self._tooltips = list(tooltips or [])
        self._index = 0
        self._hover = -1
        self._pill = 0.0
        self._compact = compact
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding if expand else QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        theme_manager.changed.connect(self.update)

    def _on_anim(self, v: object) -> None:
        self._pill = float(v)  # type: ignore[arg-type]
        self.update()

    def set_options(self, options: Sequence[tuple[Any, str]], tooltips: Sequence[str] | None = None) -> None:
        value = self.value()
        self._options = list(options)
        self._tooltips = list(tooltips or [])
        self.setValue(value)
        self.updateGeometry()
        self.update()

    def value(self) -> Any:
        return self._options[self._index][0] if self._options else None

    def setValue(self, value: Any, animate: bool = False) -> None:
        for i, (v, _) in enumerate(self._options):
            if v == value:
                self._move_to(i, animate)
                return

    def _move_to(self, i: int, animate: bool) -> None:
        self._index = i
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._pill)
            self._anim.setEndValue(float(i))
            self._anim.start()
        else:
            self._pill = float(i)
        self.update()

    def _font(self) -> QFont:
        return theme_manager.font(theme_manager.type.caption if self._compact else None, QFont.Weight.Medium)

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self._font())
        pad = px(12 if self._compact else 16)
        w = sum(fm.horizontalAdvance(text) + pad * 2 for _, text in self._options) + px(6)
        return QSize(w, px(28 if self._compact else 34))

    def minimumSizeHint(self) -> QSize:
        if self.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding:
            return QSize(px(64) * max(len(self._options), 1), self.sizeHint().height())
        return self.sizeHint()

    def _segments(self) -> list[QRectF]:
        n = max(len(self._options), 1)
        r = QRectF(self.rect()).adjusted(px(3), px(3), -px(3), -px(3))
        if self.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding:
            w = r.width() / n
            return [QRectF(r.left() + i * w, r.top(), w, r.height()) for i in range(n)]
        fm = QFontMetrics(self._font())
        pad = px(12 if self._compact else 16)
        x = r.left()
        out = []
        for _, text in self._options:
            w = fm.horizontalAdvance(text) + pad * 2
            out.append(QRectF(x, r.top(), w, r.height()))
            x += w
        return out

    def paintEvent(self, _e: QPaintEvent) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = px(8)
        p.setPen(QPen(QColor(t.border), 1))
        p.setBrush(QColor(t.surface_alt if not t.dark else t.surface_sunken))
        p.drawRoundedRect(outer, radius, radius)
        segs = self._segments()
        if not segs:
            return
        # sliding pill
        i0 = int(self._pill)
        frac = self._pill - i0
        a = segs[min(i0, len(segs) - 1)]
        b = segs[min(i0 + 1, len(segs) - 1)]
        pill = QRectF(
            a.left() + (b.left() - a.left()) * frac, a.top(), a.width() + (b.width() - a.width()) * frac, a.height()
        )
        p.setPen(QPen(QColor(t.border), 1) if not t.dark else Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.surface if not t.dark else t.surface_alt))
        p.drawRoundedRect(pill, radius - 2, radius - 2)
        p.setFont(self._font())
        for i, (seg, (_, text)) in enumerate(zip(segs, self._options, strict=False)):
            color = QColor(t.text) if i in (self._index, self._hover) else QColor(t.text_muted)
            if not self.isEnabled():
                color = QColor(t.text_faint)
            p.setPen(color)
            fm = QFontMetrics(p.font())
            p.drawText(
                seg,
                Qt.AlignmentFlag.AlignCenter,
                fm.elidedText(text, Qt.TextElideMode.ElideRight, int(seg.width() - px(8))),
            )
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(t.focus), 1.5))
            p.drawRoundedRect(pill.adjusted(-1, -1, 1, 1), radius - 1, radius - 1)
        p.end()

    def _index_at(self, pos: QPointF) -> int:
        for i, seg in enumerate(self._segments()):
            if seg.contains(pos):
                return i
        return -1

    def mousePressEvent(self, e: QMouseEvent) -> None:
        i = self._index_at(e.position())
        if i >= 0 and i != self._index:
            self._move_to(i, True)
            self.valueChanged.emit(self.value())

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        i = self._index_at(e.position())
        if i != self._hover:
            self._hover = i
            tip = self._tooltips[i] if 0 <= i < len(self._tooltips) else ""
            self.setToolTip(tip)
            self.update()

    def leaveEvent(self, _e: QEvent) -> None:
        self._hover = -1
        self.update()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = -1 if e.key() == Qt.Key.Key_Left else 1
            i = max(0, min(len(self._options) - 1, self._index + step))
            if i != self._index:
                self._move_to(i, True)
                self.valueChanged.emit(self.value())
            return
        if Qt.Key.Key_1 <= e.key() <= Qt.Key.Key_9:
            i = e.key() - Qt.Key.Key_1
            if i < len(self._options) and i != self._index:
                self._move_to(i, True)
                self.valueChanged.emit(self.value())
            return
        super().keyPressEvent(e)


class Callout(QFrame):
    """Severity-coloured message box with an icon, text and optional action buttons."""

    ICONS: ClassVar[dict[str, str]] = {
        "info": "info",
        "warning": "triangle-alert",
        "error": "circle-alert",
        "success": "circle-check",
    }

    def __init__(
        self,
        severity: str = "info",
        text: str = "",
        hint: str = "",
        parent: QWidget | None = None,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self.severity = severity
        self.setProperty("callout", severity)
        lay = QHBoxLayout(self)
        m = px(8 if compact else 12)
        lay.setContentsMargins(m, px(7 if compact else 10), m, px(7 if compact else 10))
        lay.setSpacing(px(10))
        self.icon = QLabel()
        self.icon.setFixedWidth(px(18))
        self.icon.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(px(3))
        self.text = label(text, wrap=True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.hint = label(hint, "caption", wrap=True)
        self.hint.setVisible(bool(hint))
        col.addWidget(self.text)
        col.addWidget(self.hint)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(px(8))
        col.addLayout(self.actions)
        lay.addLayout(col, 1)
        self._retint()
        theme_manager.changed.connect(self._retint)

    def _retint(self) -> None:
        color = getattr(tokens(), self.severity, tokens().info)
        self.icon.setPixmap(icons.pixmap(self.ICONS.get(self.severity, "info"), px(16), color))

    def add_action(self, text: str, variant: str = "secondary") -> QPushButton:
        b = button(text, variant if variant != "secondary" else None)
        b.setStyleSheet(f"padding: {px(3)}px {px(10)}px; min-height: {px(16)}px;")
        self.actions.addWidget(b)
        return b


class Chip(QPushButton):
    def __init__(
        self, text: str, checkable: bool = False, icon_name: str | None = None, parent: QWidget | None = None
    ) -> None:
        text = text.replace("&", "&&")
        super().__init__((" " + text) if icon_name else text, parent)
        self.setProperty("variant", "chip")
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if icon_name:
            self.setIcon(icons.icon(icon_name, size=14))
            self.setIconSize(QSize(px(14), px(14)))
            self.setProperty("icon_name", icon_name)


class Pill(QWidget):
    """Small rounded label with a colour (category chips, tags)."""

    def __init__(self, text: str, color: QColor | str, parent: QWidget | None = None, filled: bool = False) -> None:
        super().__init__(parent)
        self._text = text
        self._color = QColor(color)
        self._filled = filled
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(theme_manager.font(theme_manager.type.caption - 1, QFont.Weight.Medium))
        return QSize(fm.horizontalAdvance(self._text) + px(14), px(20))

    def paintEvent(self, _e: QPaintEvent) -> None:
        paint_pill(QPainter(self), QRectF(self.rect()), self._text, self._color, self._filled)


def paint_pill(p: QPainter, r: QRectF, text: str, color: QColor, filled: bool = False) -> None:
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    bg = QColor(color)
    bg.setAlphaF(0.85 if filled else (0.16 if tokens().dark else 0.11))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), r.height() / 2, r.height() / 2)
    p.setPen(QColor("#FFFFFF") if filled else color)
    p.setFont(theme_manager.font(theme_manager.type.caption - 1, QFont.Weight.Medium))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)


class FlowLayout(QLayout):
    """Wraps children onto new lines (for chip rows)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = px(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def addWidget(self, w: QWidget) -> None:
        self.addChildWidget(w)
        self.addItem(QWidgetItem(w))

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int) -> QLayoutItem | None:
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int) -> QLayoutItem | None:
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._layout(rect, dry=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _layout(self, rect: QRect, dry: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._items:
            if item.widget() is not None and not item.widget().isVisible() and not dry:
                pass
            hint = item.sizeHint()
            nx = x + hint.width() + self._spacing
            if nx - self._spacing > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + self._spacing
                nx = x + hint.width() + self._spacing
                line_h = 0
            if not dry:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = nx
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class FieldRow(QWidget):
    """Label (+ info tip + caption) on the left, editor on the right."""

    def __init__(
        self, title: str, editor: QWidget, caption: str = "", info: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(px(12))
        text = QVBoxLayout()
        text.setSpacing(px(2))
        head = QHBoxLayout()
        head.setSpacing(px(6))
        self.title = label(title)
        self.title.setStyleSheet("font-weight: 500;")
        self.title.setBuddy(editor)
        head.addWidget(self.title)
        if info:
            self.info = InfoTip(info)
            head.addWidget(self.info)
        head.addStretch(1)
        text.addLayout(head)
        self.caption = label(caption, "caption", wrap=True)
        self.caption.setVisible(bool(caption))
        text.addWidget(self.caption)
        lay.addLayout(text, 1)
        lay.addWidget(editor, 0, Qt.AlignmentFlag.AlignVCenter)
        self.editor = editor


def section_label(text: str) -> QLabel:
    return label(text.upper(), "section")


def elide(text: str, font: QFont, width: int) -> str:
    return QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, width)


class ClickFilter(QObject):
    """Calls ``callback`` on mouse release over the watched widget."""

    def __init__(self, callback: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cb = callback

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonRelease:
            self._cb()
            return True
        return False


def standard_icon_size() -> int:
    return QStyle.PixelMetric.PM_SmallIconSize.value
