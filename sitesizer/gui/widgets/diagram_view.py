"""Widgets that show the topology diagram: a fit-to-width preview and a pan/zoom canvas."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPaintEvent, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QSizePolicy,
    QStyleOptionGraphicsItem,
    QWidget,
)

from ...exporters.diagram import Diagram, fit_rect
from ..theme import tokens


class DiagramPreview(QWidget):
    """Scales the diagram to fit; height follows the aspect ratio (capped)."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None, max_height: int = 420) -> None:
        super().__init__(parent)
        self.diagram: Diagram | None = None
        self.max_height = max_height
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(160)

    def set_diagram(self, diagram: Diagram | None) -> None:
        self.diagram = diagram
        self.updateGeometry()
        self.update()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        if not self.diagram:
            return 180
        size = self.diagram.size()
        return min(self.max_height, max(160, int(w * size.height() / max(size.width(), 1))))

    def sizeHint(self) -> QSize:
        return QSize(520, self.heightForWidth(520))

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        self.updateGeometry()

    def paintEvent(self, _e: QPaintEvent) -> None:
        if not self.diagram:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale, offset = fit_rect(self.diagram.size(), QRectF(self.rect()))
        scale = min(scale, 1.0)
        size = self.diagram.size()
        offset = QPointF((self.width() - size.width() * scale) / 2, (self.height() - size.height() * scale) / 2)
        p.translate(offset)
        p.scale(scale, scale)
        self.diagram.paint(p, background=False)
        p.end()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class _DiagramItem(QGraphicsItem):
    def __init__(self) -> None:
        super().__init__()
        self.diagram: Diagram | None = None

    def set_diagram(self, diagram: Diagram | None) -> None:
        self.prepareGeometryChange()
        self.diagram = diagram
        self.update()

    def boundingRect(self) -> QRectF:
        if not self.diagram:
            return QRectF(0, 0, 1, 1)
        s = self.diagram.size()
        return QRectF(0, 0, s.width(), s.height())

    def paint(self, painter: QPainter, _o: QStyleOptionGraphicsItem, _w: QWidget | None = None) -> None:
        if self.diagram:
            self.diagram.paint(painter, background=True)


class DiagramCanvas(QGraphicsView):
    """Pan (drag) and zoom (wheel / Ctrl+wheel / buttons) around the diagram."""

    zoomChanged = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._item = _DiagramItem()
        self._scene.addItem(self._item)
        self.setScene(self._scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self._zoom = 1.0
        self._auto_fit = True
        self.apply_theme()

    def apply_theme(self) -> None:
        self.setBackgroundBrush(tokens().q("surface_sunken"))

    def set_diagram(self, diagram: Diagram | None) -> None:
        self._item.set_diagram(diagram)
        rect = self._item.boundingRect().adjusted(-200, -200, 200, 200)
        self._scene.setSceneRect(rect)
        if self._auto_fit:
            self.fit()

    def fit(self) -> None:
        if not self._item.diagram:
            return
        self._auto_fit = True
        br = self._item.boundingRect()
        self.resetTransform()
        view = QRectF(self.viewport().rect()).adjusted(24, 24, -24, -24)
        scale = min(view.width() / br.width(), view.height() / br.height(), 1.6)
        self.scale(scale, scale)
        self._zoom = scale
        self.centerOn(br.center())
        self.zoomChanged.emit(self._zoom)

    def zoom_by(self, factor: float) -> None:
        new = max(0.2, min(4.0, self._zoom * factor))
        factor = new / self._zoom
        self._zoom = new
        self._auto_fit = False
        self.scale(factor, factor)
        self.zoomChanged.emit(self._zoom)

    def set_zoom(self, value: float) -> None:
        self.zoom_by(value / self._zoom)

    def wheelEvent(self, e: QWheelEvent) -> None:
        delta = e.angleDelta().y()
        if delta:
            self.zoom_by(1.12 if delta > 0 else 1 / 1.12)
        e.accept()

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        if self._auto_fit:
            self.fit()
