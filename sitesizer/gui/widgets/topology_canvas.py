"""Editable network topology: pan/zoom like the diagram canvas, plus devices that can be dragged.

* drag a device (Ctrl+click selects several) — it moves, its links follow live; the drop is one
  undoable edit;
* click a link / a stack title to select it; double-click renames; right click opens the menu;
* "connect" mode (from the menu or the toolbar): click the second device to draw a new link;
* Delete removes the selected link(s); Esc cancels the connect mode / clears the selection;
* dragging the empty background pans, the wheel zooms.

The widget never edits the project itself: it emits signals the topology page turns into edits of
``SiteInput.topology``.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QGraphicsView, QWidget

from ...core.topology import route_links
from ...exporters.topology import TopologyDiagram
from .diagram_view import DiagramCanvas

DRAG_THRESHOLD = 4


class TopologyCanvas(DiagramCanvas):
    nodesMoved = Signal(list)
    """[(node id, x, y), …] in topology coordinates."""
    contextRequested = Signal(str, str, QPoint)
    """(kind: node | link | group | "", id, global position)"""
    renameRequested = Signal(str, str)
    """(kind, id)"""
    linkRequested = Signal(str, str)
    """(node a, node b) — a new link drawn in connect mode."""
    deleteRequested = Signal(list)
    """Link ids."""
    selectionChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.selected: list[tuple[str, str]] = []
        """(kind, id) of every selected thing."""
        self.connect_from = ""
        self._press: QPointF | None = None
        self._drag_start: dict[str, tuple[float, float]] | None = None
        self._dragging = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ---- data -------------------------------------------------------------------------------
    @property
    def diagram(self) -> TopologyDiagram | None:
        d = self._item.diagram
        return d if isinstance(d, TopologyDiagram) else None

    def set_diagram(self, diagram) -> None:  # type: ignore[no-untyped-def, override]
        super().set_diagram(diagram)
        if isinstance(diagram, TopologyDiagram):
            topo = diagram.topo
            ids = {("node", n.id) for n in topo.nodes} | {("link", lk.id) for lk in topo.links}
            ids |= {("group", g.id) for g in topo.groups}
            self.selected = [s for s in self.selected if s in ids]
            if self.connect_from and topo.node(self.connect_from) is None:
                self.connect_from = ""
            self._sync()
        else:
            self.selected = []
            self.connect_from = ""

    def _sync(self) -> None:
        d = self.diagram
        if d is not None:
            d.selected = {i for _k, i in self.selected} | ({self.connect_from} if self.connect_from else set())
        self._item.update()
        self.selectionChanged.emit()

    def select(self, kind: str, item_id: str, add: bool = False) -> None:
        if not kind:
            self.selected = [] if not add else self.selected
        elif add:
            key = (kind, item_id)
            if key in self.selected:
                self.selected.remove(key)
            else:
                self.selected.append(key)
        else:
            self.selected = [(kind, item_id)]
        self._sync()

    def selected_ids(self, kind: str) -> list[str]:
        return [i for k, i in self.selected if k == kind]

    def start_connect(self, node_id: str) -> None:
        self.connect_from = node_id
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._sync()

    def cancel_connect(self) -> None:
        self.connect_from = ""
        self.unsetCursor()
        self._sync()

    # ---- mouse --------------------------------------------------------------------------------
    def _hit(self, pos: QPoint) -> tuple[str, str, QPointF]:
        d = self.diagram
        scene = self.mapToScene(pos)
        if d is None:
            return "", "", scene
        kind, item_id = d.hit(scene)
        return kind, item_id, scene

    def mousePressEvent(self, e: QMouseEvent) -> None:
        d = self.diagram
        if d is None:
            super().mousePressEvent(e)
            return
        self.setFocus()
        kind, item_id, scene = self._hit(e.position().toPoint())
        if e.button() == Qt.MouseButton.RightButton:
            if kind and (kind, item_id) not in self.selected:
                self.select(kind, item_id)
            self.contextRequested.emit(kind, item_id, e.globalPosition().toPoint())
            return
        if e.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(e)
            return
        if self.connect_from:
            if kind == "node" and item_id != self.connect_from:
                a = self.connect_from
                self.cancel_connect()
                self.linkRequested.emit(a, item_id)
            elif not kind:
                self.cancel_connect()
            return
        ctrl = bool(e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
        if not kind:
            if not ctrl:
                self.select("", "")
            super().mousePressEvent(e)  # pan
            return
        if not (kind == "node" and (kind, item_id) in self.selected and not ctrl):
            self.select(kind, item_id, add=ctrl)
        if kind == "node" and ("node", item_id) in self.selected:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self._press = scene
            self._drag_start = {n.id: (n.rect.x, n.rect.y) for n in d.topo.nodes if ("node", n.id) in self.selected}
            self._dragging = False

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        d = self.diagram
        if d is not None and self._press is not None and self._drag_start is not None:
            scene = self.mapToScene(e.position().toPoint())
            delta = scene - self._press
            if not self._dragging and delta.manhattanLength() * self._zoom < DRAG_THRESHOLD:
                return
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            for n in d.topo.nodes:
                start = self._drag_start.get(n.id)
                if start is not None:
                    n.rect.x = round((start[0] + delta.x()) / 2) * 2
                    n.rect.y = round((start[1] + delta.y()) / 2) * 2
                    n.manual = True
            route_links(d.topo)
            self._item.update()
            return
        if d is not None and not self.connect_from and self.dragMode() != QGraphicsView.DragMode.NoDrag:
            kind, _i, _s = self._hit(e.position().toPoint())
            self.viewport().setCursor(
                Qt.CursorShape.OpenHandCursor
                if kind == "node"
                else Qt.CursorShape.PointingHandCursor
                if kind
                else Qt.CursorShape.ArrowCursor
            )
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        d = self.diagram
        if self._press is not None:
            moved = self._dragging
            starts = self._drag_start or {}
            self._press = None
            self._drag_start = None
            self._dragging = False
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.unsetCursor()
            if moved and d is not None:
                self.nodesMoved.emit([(n.id, n.rect.x, n.rect.y) for n in d.topo.nodes if n.id in starts])
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        kind, item_id, _s = self._hit(e.position().toPoint())
        if kind:
            self.renameRequested.emit(kind, item_id)
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            if self.connect_from:
                self.cancel_connect()
            else:
                self.select("", "")
            return
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            links = self.selected_ids("link")
            if links:
                self.deleteRequested.emit(links)
            return
        if e.key() == Qt.Key.Key_F2 and len(self.selected) == 1:
            self.renameRequested.emit(*self.selected[0])
            return
        super().keyPressEvent(e)

    def node_point(self, node_id: str) -> QPointF | None:
        """Centre of a device in viewport coordinates (tests)."""
        d = self.diagram
        n = d.topo.node(node_id) if d else None
        if d is None or n is None:
            return None
        scene = QPointF(n.rect.cx, n.rect.cy) + d.offset
        return QPointF(self.mapFromScene(scene))
