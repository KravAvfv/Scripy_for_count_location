"""Interactive cabinet editor: drag devices between units and cabinets.

Paints the same :class:`~sitesizer.exporters.rack.RackDiagram` as the exports and adds selection
(Ctrl+click adds devices of the same cabinet, Ctrl+A selects the whole cabinet), a drag ghost
(green = fits, red = blocked) that moves every selected device together, swapping two devices by
dropping one onto the other (blue ⇄), and keyboard moves.
It never edits the layout itself; it emits signals that the racks page turns into undoable edits
of ``SiteInput.layout``.
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
    movedMany = Signal(list)
    """[(item id, cabinet key, new lowest unit), …] — a selection moved together."""
    selectionChanged = Signal(str, str)
    """(cabinet key, item id or "")"""
    contextRequested = Signal(str, str, int, QPoint)
    """(cabinet key, item id or "", unit, global position)"""
    deleteRequested = Signal(str)
    deleteManyRequested = Signal(list)
    copyRequested = Signal(list)
    """Item ids whose names should go to the clipboard."""
    swapped = Signal(list)
    """[(item id, cabinet key, new lowest unit), (other id, …)] — a device dropped onto another one."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.diagram: RackDiagram | None = None
        self.zoom = 1.0
        self.sel_rack = ""
        self.sel_item = ""
        self.sel_items: list[str] = []
        """Every selected item (all in ``sel_rack``); ``sel_item`` is the last one clicked."""
        self._press: QPointF | None = None
        self._press_plain = False
        self._drag: RackItem | None = None
        self._grab_offset = 0
        self._ghost: tuple[RackPlan, int, bool] | None = None
        self._swap: tuple[RackItem, RackPlan, int, int] | None = None
        """(the other device, its cabinet, new unit of the dragged one, new unit of the other one)."""
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
            self.sel_items = [i for i in self.sel_items if self.item(i) is not None]
            if self.sel_item and self.sel_item not in self.sel_items:
                self.sel_items = [self.sel_item]
            if not self.sel_item:
                self.sel_items = []
        self._drag = None
        self._ghost = None
        self._swap = None
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

    def select(self, rack: str, item: str = "", add: bool = False) -> None:
        """Select a cabinet / an item; ``add`` (Ctrl) toggles the item in a multi-selection."""
        if add and item and rack == self.sel_rack:
            if item in self.sel_items:
                self.sel_items.remove(item)
                item = self.sel_items[-1] if self.sel_items else ""
            else:
                self.sel_items.append(item)
        else:
            self.sel_items = [item] if item else []
        self.sel_rack, self.sel_item = rack, item
        self.selectionChanged.emit(rack, item)
        self.update()

    def select_all(self) -> None:
        plan = self.plan(self.sel_rack)
        if plan is None:
            return
        ids = [it.id for it in sorted(plan.items, key=lambda i: -i.u)]
        self.sel_items = ids
        self.sel_item = ids[-1] if ids else ""
        self.selectionChanged.emit(self.sel_rack, self.sel_item)
        self.update()

    def selected_items(self) -> list[RackItem]:
        """Selected items, top-down."""
        items = [it for i in self.sel_items if (it := self.item(i)) is not None]
        return sorted(items, key=lambda it: -it.u)

    def group_fits(self, plan: RackPlan, items: list[RackItem], delta: int, target: RackPlan | None = None) -> bool:
        """Can ``items`` of ``plan`` move ``delta`` units (into ``target``, default the same cabinet)?"""
        target = target or plan
        moving = {id(it) for it in items}
        for it in items:
            u = it.u + delta
            if u < 1 or u + it.height - 1 > target.size_u:
                return False
            for o in target.items:
                if id(o) in moving:
                    continue
                if o.u <= u + it.height - 1 and u <= o.top:
                    return False
        return True

    def swap_units(self, src: RackPlan, a: RackItem, dst: RackPlan, b: RackItem) -> tuple[int, int] | None:
        """Where ``a`` and ``b`` go when they trade places (tops aligned, else bottoms), or ``None``."""
        if a is b:
            return None

        def free(plan: RackPlan, u: int, h: int) -> bool:
            if u < 1 or u + h - 1 > plan.size_u:
                return False
            return not any(o is not a and o is not b and o.u <= u + h - 1 and u <= o.top for o in plan.items)

        for ua, ub in ((b.top - a.height + 1, a.top - b.height + 1), (b.u, a.u)):
            # in one cabinet the two must not land on each other either
            if src is dst and ua <= ub + b.height - 1 and ub <= ua + a.height - 1:
                continue
            if free(dst, ua, a.height) and free(src, ub, b.height):
                return ua, ub
        return None

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
        if plan is not None:
            x, y = self._origin(plan)
            for sel in self.selected_items():
                p.setPen(QPen(QColor(t.primary), 2.2))
                fill = QColor(t.primary)
                fill.setAlphaF(0.12 if len(self.sel_items) > 1 else 0.0)
                p.setBrush(fill)
                p.drawRoundedRect(self.diagram.unit_rect(plan, x, y, sel.u, sel.height), 3, 3)
            p.setBrush(Qt.BrushStyle.NoBrush)
        if self._drag is not None and self._swap is not None:
            other, oplan, ua, ub = self._swap
            src = self.plan(self.sel_rack)
            color = QColor(t.primary)
            fill = QColor(color)
            fill.setAlphaF(0.20)
            for plan_, u_, h_ in ((oplan, ua, self._drag.height), (src, ub, other.height)):
                if plan_ is None:
                    continue
                x, y = self._origin(plan_)
                rect = self.diagram.unit_rect(plan_, x, y, u_, h_)
                p.setPen(QPen(color, 2, Qt.PenStyle.DashLine))
                p.setBrush(fill)
                p.drawRoundedRect(rect, 3, 3)
                p.setPen(color)
                p.drawText(rect.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "⇄")
        elif self._drag is not None and self._ghost is not None:
            gplan, gu, ok = self._ghost
            x, y = self._origin(gplan)
            color = QColor(t.success if ok else t.error)
            fill = QColor(color)
            fill.setAlphaF(0.22)
            delta = gu - self._drag.u
            for it in self._drag_group():
                rect = self.diagram.unit_rect(gplan, x, y, it.u + delta, it.height)
                p.setPen(QPen(color, 2, Qt.PenStyle.DashLine))
                p.setBrush(fill)
                p.drawRoundedRect(rect, 3, 3)
                if it is self._drag:
                    p.setPen(color)
                    p.drawText(
                        rect.adjusted(8, 0, -8, 0),
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                        f"U{gu}",
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
                if item is not None and item.id in self.sel_items:
                    self.sel_item = item.id  # keep the multi-selection for the menu
                else:
                    self.select(plan.key, item.id if item else "")
                self.contextRequested.emit(plan.key, item.id if item else "", u, e.globalPosition().toPoint())
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if plan is None:
            return
        ctrl = bool(e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
        if item is not None and not ctrl and item.id in self.sel_items and len(self.sel_items) > 1:
            # pressing on a selected device keeps the selection, so that the group can be dragged
            self.sel_item = item.id
            self._press_plain = True
        else:
            self.select(plan.key, item.id if item else "", add=ctrl)
            self._press_plain = False
        if item is not None and item.id in self.sel_items:
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
            plan, under, u, header = self.diagram.hit(pos)
            src = self.plan(self.sel_rack)
            self._swap = None
            if (
                plan is not None
                and under is not None
                and src is not None
                and under is not self._drag
                and len(self._drag_group()) == 1
            ):
                units = self.swap_units(src, self._drag, plan, under)
                if units is not None:
                    self._swap = (under, plan, *units)
                    self._ghost = None
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
                    self.update()
                    return
            if plan is not None and not header and src is not None:
                target = max(1, u - self._grab_offset)
                ok = self.group_fits(src, self._drag_group(), target - self._drag.u, plan)
                self._ghost = (plan, target, ok)
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

    def _drag_group(self) -> list[RackItem]:
        if self._drag is None:
            return []
        group = self.selected_items()
        return group if any(it is self._drag for it in group) else [self._drag]

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        drag, ghost, swap = self._drag, self._ghost, self._swap
        group = self._drag_group()
        self._press = None
        self._drag = None
        self._ghost = None
        self._swap = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if drag is not None and swap is not None:
            other, oplan, ua, ub = swap
            src = self.sel_rack
            self.sel_rack = oplan.key
            self.swapped.emit([(drag.id, oplan.key, ua), (other.id, src, ub)])
        elif drag is not None and ghost is not None:
            plan, u, ok = ghost
            if ok and (plan.key != self.sel_rack or u != drag.u):
                self.sel_rack = plan.key
                delta = u - drag.u
                if len(group) > 1:
                    self.movedMany.emit([(it.id, plan.key, it.u + delta) for it in group])
                else:
                    self.moved.emit(drag.id, plan.key, u)
        elif drag is None and self._press_plain and self.sel_item:
            # a plain click on a device of a multi-selection selects just that device
            self.select(self.sel_rack, self.sel_item)
        self._press_plain = False
        self.update()

    # ---- keyboard ------------------------------------------------------------------------
    def keyPressEvent(self, e: QKeyEvent) -> None:
        plan, it = self.selected()
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if ctrl and e.key() == Qt.Key.Key_A:
            self.select_all()
            return
        if ctrl and e.key() == Qt.Key.Key_C:
            self.copyRequested.emit([i.id for i in self.selected_items()])
            return
        group = self.selected_items()
        if plan is not None and len(group) > 1:
            if e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                step = 1 if e.key() == Qt.Key.Key_Up else -1
                if self.group_fits(plan, group, step):
                    self.movedMany.emit([(g.id, plan.key, g.u + step) for g in group])
                return
            if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.deleteManyRequested.emit([g.id for g in group])
                return
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
