"""Interactive cabinet editor: drag devices between units and cabinets.

Paints the same :class:`~sitesizer.exporters.rack.RackDiagram` as the exports and adds selection,
a drag ghost (green = fits, red = blocked) and keyboard moves. It never edits the layout itself;
it emits signals that the racks page turns into undoable edits of ``SiteInput.layout``.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...core.models import RackItem, RackPlan
from ...exporters.rack import RackDiagram
from ..theme import tokens

DRAG_THRESHOLD = 4


class RackEditor(QWidget):
    moved = Signal(str, str, int)
    """(item id, cabinet key, new lowest unit)"""
    selectionChanged = Signal(str, str)
    """(cabinet key, item id or "")"""
    contextRequested = Signal(str, str, int, QPoint)
    """(cabinet key, item id or "", unit, global position)"""
    deleteRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.diagram: RackDiagram | None = None
        self.zoom = 1.0
        self.sel_rack = ""
        self.sel_item = ""
        self._press: QPointF | None = None
        self._drag: RackItem | None = None
        self._grab_offset = 0
        self._ghost: tuple[RackPlan, int, bool] | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    # ---- data ----------------------------------------------------------------------------
    def set_diagram(self, diagram: RackDiagram | None) -> None:
        self.diagram = diagram
        if diagram is not None:
            diagram.mark_manual = True
            keys = {p.key for p in diagram.plans}
            if self.sel_rack not in keys:
                self.sel_rack = diagram.plans[0].key if diagram.plans else ""
            if self.sel_item and self.item(self.sel_item) is None:
                self.sel_item = ""
        self._drag = None
        self._ghost = None
        self.updateGeometry()
        self.adjustSize()
        self.update()

    def set_zoom(self, zoom: float) -> None:
        self.zoom = max(0.4, min(2.5, zoom))
        self.updateGeometry()
        self.adjustSize()
        self.update()

    def sizeHint(self) -> QSize:
        if not self.diagram:
            return QSize(400, 300)
        s = self.diagram.size()
        return QSize(int(s.width() * self.zoom) + 1, int(s.height() * self.zoom) + 1)

    def plan(self, key: str) -> RackPlan | None:
        if not self.diagram:
            return None
        return next((p for p in self.diagram.plans if p.key == key), None)

    def item(self, item_id: str) -> RackItem | None:
        if not self.diagram:
            return None
        return next((it for p in self.diagram.plans for it in p.items if it.id == item_id), None)

    def selected(self) -> tuple[RackPlan | None, RackItem | None]:
        return self.plan(self.sel_rack), self.item(self.sel_item) if self.sel_item else None

    def select(self, rack: str, item: str = "") -> None:
        self.sel_rack, self.sel_item = rack, item
        self.selectionChanged.emit(rack, item)
        self.update()

    # ---- geometry ------------------------------------------------------------------------
    def _to_diagram(self, pos: QPointF) -> QPointF:
        return QPointF(pos.x() / self.zoom, pos.y() / self.zoom)

    def _origin(self, plan: RackPlan) -> tuple[float, float]:
        assert self.diagram is not None
        for p, x, y in self.diagram.origins():
            if p is plan:
                return x, y
        return 0.0, 0.0

    def fits(self, plan: RackPlan, it: RackItem, u: int) -> bool:
        if u < 1 or u + it.height - 1 > plan.size_u:
            return False
        return not any(o is not it and o.u <= u + it.height - 1 and u <= o.top for o in plan.items)

    # ---- painting ------------------------------------------------------------------------
    def paintEvent(self, _e: QPaintEvent) -> None:
        if not self.diagram:
            return
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(self.zoom, self.zoom)
        self.diagram.paint(p, background=False)
        plan = self.plan(self.sel_rack)
        if plan is not None:
            x, y = self._origin(plan)
            p.setPen(QPen(QColor(t.primary), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(self.diagram.header_rect(x, y).adjusted(-4, -2, 4, 0), 6, 6)
        sel = self.item(self.sel_item) if self.sel_item else None
        if sel is not None and plan is not None:
            x, y = self._origin(plan)
            p.setPen(QPen(QColor(t.primary), 2.2))
            p.drawRoundedRect(self.diagram.unit_rect(plan, x, y, sel.u, sel.height).adjusted(0, 0, 0, 0), 3, 3)
        if self._drag is not None and self._ghost is not None:
            gplan, gu, ok = self._ghost
            x, y = self._origin(gplan)
            rect = self.diagram.unit_rect(gplan, x, y, gu, self._drag.height)
            color = QColor(t.success if ok else t.error)
            fill = QColor(color)
            fill.setAlphaF(0.22)
            p.setPen(QPen(color, 2, Qt.PenStyle.DashLine))
            p.setBrush(fill)
            p.drawRoundedRect(rect, 3, 3)
            p.setPen(color)
            p.drawText(
                rect.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"U{gu}"
            )
        p.end()

    # ---- mouse ---------------------------------------------------------------------------
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if not self.diagram:
            return
        self.setFocus()
        pos = self._to_diagram(e.position())
        plan, item, u, _header = self.diagram.hit(pos)
        if e.button() == Qt.MouseButton.RightButton:
            if plan is not None:
                self.select(plan.key, item.id if item else "")
                self.contextRequested.emit(plan.key, item.id if item else "", u, e.globalPosition().toPoint())
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if plan is None:
            return
        self.select(plan.key, item.id if item else "")
        if item is not None:
            self._press = pos
            self._drag = None
            self._grab_offset = u - item.u

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if not self.diagram:
            return
        pos = self._to_diagram(e.position())
        if (
            self._press is not None
            and self._drag is None
            and self.sel_item
            and (pos - self._press).manhattanLength() * self.zoom >= DRAG_THRESHOLD
        ):
            self._drag = self.item(self.sel_item)
        if self._drag is not None:
            plan, _item, u, header = self.diagram.hit(pos)
            if plan is not None and not header:
                target = max(1, u - self._grab_offset)
                self._ghost = (plan, target, self.fits(plan, self._drag, target))
            else:
                self._ghost = None
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()
            return
        _plan, item, _u, header = self.diagram.hit(pos)
        if item is not None:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif header:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        drag, ghost = self._drag, self._ghost
        self._press = None
        self._drag = None
        self._ghost = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if drag is not None and ghost is not None:
            plan, u, ok = ghost
            if ok and (plan.key != self.sel_rack or u != drag.u):
                self.sel_rack = plan.key
                self.moved.emit(drag.id, plan.key, u)
        self.update()

    # ---- keyboard ------------------------------------------------------------------------
    def keyPressEvent(self, e: QKeyEvent) -> None:
        plan, it = self.selected()
        if plan is not None and it is not None:
            if e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                step = 1 if e.key() == Qt.Key.Key_Up else -1
                target = it.u + step
                while 1 <= target <= plan.size_u and not self.fits(plan, it, target):
                    target += step  # jump over neighbours
                if self.fits(plan, it, target):
                    self.moved.emit(it.id, plan.key, target)
                return
            if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.deleteRequested.emit(it.id)
                return
        super().keyPressEvent(e)

    def item_rect_in_widget(self, item_id: str) -> QRectF | None:
        """Where an item is drawn (widget coordinates) — used by tests and tooltips."""
        if not self.diagram:
            return None
        for plan, x, y in self.diagram.origins():
            for it in plan.items:
                if it.id == item_id:
                    r = self.diagram.unit_rect(plan, x, y, it.u, it.height)
                    return QRectF(r.x() * self.zoom, r.y() * self.zoom, r.width() * self.zoom, r.height() * self.zoom)
        return None

    def unit_point(self, rack: str, u: int) -> QPointF | None:
        plan = self.plan(rack)
        if plan is None or not self.diagram:
            return None
        x, y = self._origin(plan)
        r = self.diagram.unit_rect(plan, x, y, u, 1)
        return QPointF(r.center().x() * self.zoom, r.center().y() * self.zoom)
