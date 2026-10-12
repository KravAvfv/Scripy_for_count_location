"""Topology view: the location's network device by device, editable; or all locations at a glance.

"Ця локація" shows :class:`~sitesizer.exporters.topology.TopologyDiagram` of the whole location
(every floor): FortiGates, core, access stacks, with the names from the cabinets. Devices are dragged,
links added / removed / retyped / relabelled, switches moved to another stack, and the access design
switched between stacks (rings) and dual-homed — all undoable edits of ``SiteInput.topology``
(shared by every floor). Exports: PNG, SVG, draw.io.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QPoint, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFrame, QHBoxLayout, QInputDialog, QMenu, QVBoxLayout, QWidget

from ...core.models import TopologyEdits
from ...core.topology import LINK_KINDS
from ...exporters.diagram import Diagram, ProjectDiagram, style_from_tokens
from ...exporters.topology import TopologyDiagram
from ...i18n import tr
from .. import icons
from ..lazy import when_shown
from ..state import AppState
from ..theme import theme_manager, tokens
from ..widgets.controls import SegmentedControl, SpinBox, ToggleSwitch, button, icon_button, label, px, vline
from ..widgets.topology_canvas import TopologyCanvas


class TopologyView(QWidget):
    export_requested = Signal(str)  # "png" | "svg" | "drawio"

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._loading = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(px(10))
        bar = QHBoxLayout()
        bar.setSpacing(px(8))
        self.scope = SegmentedControl(
            [("site", tr("ui.topo_site")), ("project", tr("ui.topo_project"))],
            [tr("ui.topo_site_tip"), tr("ui.topo_project_tip")],
            expand=False,
        )
        self.scope.valueChanged.connect(lambda _v: self.refresh())
        bar.addWidget(self.scope)
        bar.addStretch(1)
        self.zoom_out = icon_button("zoom-out", tr("ui.zoom_out"))
        self.zoom_label = label("100%", "caption")
        self.zoom_label.setMinimumWidth(px(44))
        self.zoom_in = icon_button("zoom-in", tr("ui.zoom_in"))
        self.fit_btn = icon_button("maximize", tr("ui.zoom_fit"))
        for w in (self.zoom_out, self.zoom_label, self.zoom_in, self.fit_btn):
            bar.addWidget(w)
        bar.addWidget(vline())
        png = button("PNG", None, "image", tr("ui.export_png"))
        svg = button("SVG", None, "download", tr("ui.export_svg"))
        drawio = button("draw.io", None, "network", tr("topo.export_drawio"))
        png.clicked.connect(lambda: self.export_requested.emit("png"))
        svg.clicked.connect(lambda: self.export_requested.emit("svg"))
        drawio.clicked.connect(lambda: self.export_requested.emit("drawio"))
        bar.addWidget(png)
        bar.addWidget(svg)
        bar.addWidget(drawio)
        root.addLayout(bar)

        # ---- design of the location's network (editable scope only) --------------------------
        self.edit_bar = QWidget()
        eb = QHBoxLayout(self.edit_bar)
        eb.setContentsMargins(0, 0, 0, 0)
        eb.setSpacing(px(8))
        eb.addWidget(label(tr("topo.access"), "caption"))
        self.access = SegmentedControl(
            [("ring", tr("topo.access_ring")), ("dual", tr("topo.access_dual"))],
            [tr("topo.access_ring_tip"), tr("topo.access_dual_tip")],
            expand=False,
            compact=True,
        )
        self.access.valueChanged.connect(lambda v: self._edit(tr("topo.access"), lambda t: t.update(access=v)))
        eb.addWidget(self.access)
        eb.addSpacing(px(8))
        eb.addWidget(label(tr("topo.group_size"), "caption"))
        self.group_size = SpinBox()
        self.group_size.setRange(1, 48)
        self.group_size.setFixedWidth(px(70))
        self.group_size.setToolTip(tr("topo.group_size_tip"))
        self.group_size.valueChanged.connect(self._set_group_size)
        eb.addWidget(self.group_size)
        eb.addSpacing(px(8))
        self.wan = ToggleSwitch()
        self.wan.toggled.connect(lambda on: self._edit("WAN", lambda t: t.update(show_wan=bool(on))))
        eb.addWidget(self.wan)
        eb.addWidget(label("WAN", "caption"))
        eb.addStretch(1)
        self.connect_btn = button(tr("topo.connect"), None, "cable", tr("topo.connect_tip"))
        self.connect_btn.clicked.connect(self._connect_selected)
        eb.addWidget(self.connect_btn)
        self.reset_btn = button(tr("topo.reset"), None, "refresh-ccw", tr("topo.reset_tip"))
        self.reset_btn.clicked.connect(self.reset)
        eb.addWidget(self.reset_btn)
        root.addWidget(self.edit_bar)

        frame = QFrame()
        frame.setObjectName("Card")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(1, 1, 1, 1)
        self.canvas = TopologyCanvas()
        fl.addWidget(self.canvas)
        root.addWidget(frame, 1)
        self.hint = label(tr("topo.hint"), "caption", wrap=True)
        root.addWidget(self.hint)

        self.zoom_out.clicked.connect(lambda: self.canvas.zoom_by(1 / 1.2))
        self.zoom_in.clicked.connect(lambda: self.canvas.zoom_by(1.2))
        self.fit_btn.clicked.connect(self.canvas.fit)
        self.canvas.zoomChanged.connect(lambda z: self.zoom_label.setText(f"{round(z * 100)}%"))
        self.canvas.nodesMoved.connect(self.move_nodes)
        self.canvas.linkRequested.connect(self.add_link)
        self.canvas.deleteRequested.connect(self.delete_links)
        self.canvas.renameRequested.connect(self.rename)
        self.canvas.contextRequested.connect(self._context)
        self.canvas.selectionChanged.connect(self._sync_buttons)
        state.resultChanged.connect(when_shown(self, self._on_result))
        state.projectChanged.connect(when_shown(self, self.refresh))
        theme_manager.changed.connect(self._on_theme)

    # ---- building ------------------------------------------------------------------------------
    def _on_theme(self) -> None:
        self.canvas.apply_theme()
        self.refresh()

    @property
    def edits(self) -> TopologyEdits:
        return self.state.site.topology

    def build_diagram(self, export: bool = False, scope: str = "") -> Diagram | TopologyDiagram | None:
        style = None if export else style_from_tokens(tokens())
        kwargs = {"style": style} if style else {}
        lang = self.state.settings.language
        if (scope or self.scope.value()) == "project":
            entries = []
            for entry in self.state.project.sites:
                r = self.state.result_for(entry.id)
                if r is not None:
                    entries.append((entry.input.name, entry.is_hub, r))
            return ProjectDiagram(
                entries, self.state.catalog, lang, title=self.state.project.name if export else "", **kwargs
            )
        result = self.state.location_result
        if result is None:
            return None
        return TopologyDiagram(result, self.state.catalog, lang, title=export, edits=self.edits, **kwargs)

    def _on_result(self, _result: object) -> None:
        self.refresh()

    def refresh(self) -> None:
        editable = self.scope.value() == "site"
        self.edit_bar.setVisible(editable)
        self.hint.setText(tr("topo.hint") if editable else tr("ui.topo_hint"))
        self._loading = True
        try:
            ed = self.edits
            self.access.setValue(ed.access)
            self.group_size.setValue(ed.group_size or self.state.catalog.rules.topology_group_size)
            self.wan.setChecked(ed.show_wan)
        finally:
            self._loading = False
        self.canvas.set_diagram(self.build_diagram())
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.connect_btn.setEnabled(len(self.canvas.selected_ids("node")) >= 1)
        self.reset_btn.setEnabled(not self.edits.is_empty)

    # ---- edits (undoable, shared by every floor) ------------------------------------------------
    def _edit(self, text: str, fn: Callable[[dict[str, Any]], None]) -> None:
        if self._loading:
            return

        def mutate(d: dict[str, Any]) -> None:
            topo = d.setdefault("topology", {})
            for key, empty in (
                ("positions", {}),
                ("hidden", []),
                ("links", []),
                ("labels", {}),
                ("kinds", {}),
                ("groups", {}),
            ):
                topo.setdefault(key, empty)
            fn(topo)

        self.state.edit(text, mutate)

    def _set_group_size(self, value: int) -> None:
        default = self.state.catalog.rules.topology_group_size
        self._edit(tr("topo.group_size"), lambda t: t.update(group_size=None if value == default else int(value)))

    def move_nodes(self, moves: list[tuple[str, float, float]]) -> None:
        def fn(t: dict[str, Any]) -> None:
            for node_id, x, y in moves:
                t["positions"][node_id] = {"x": round(x, 1), "y": round(y, 1)}

        self._edit(tr("topo.moved"), fn)

    def add_link(self, a: str, b: str, kind: str = "custom") -> str:
        link_id = "u-" + uuid.uuid4().hex[:8]
        self._edit(tr("topo.link_added"), lambda t: t["links"].append({"id": link_id, "a": a, "b": b, "kind": kind}))
        return link_id

    def delete_links(self, ids: list[str]) -> None:
        def fn(t: dict[str, Any]) -> None:
            for link_id in ids:
                before = len(t["links"])
                t["links"] = [lk for lk in t["links"] if lk["id"] != link_id]
                if len(t["links"]) == before and link_id not in t["hidden"]:
                    t["hidden"].append(link_id)
                t["labels"].pop(link_id, None)
                t["kinds"].pop(link_id, None)

        self.canvas.select("", "")
        self._edit(tr("topo.link_removed", n=len(ids)), fn)

    def set_kind(self, link_id: str, kind: str) -> None:
        def fn(t: dict[str, Any]) -> None:
            for lk in t["links"]:
                if lk["id"] == link_id:
                    lk["kind"] = kind
                    return
            t["kinds"][link_id] = kind

        self._edit(tr("topo.link_kind"), fn)

    def set_label(self, item_id: str, text: str) -> None:
        def fn(t: dict[str, Any]) -> None:
            for lk in t["links"]:
                if lk["id"] == item_id:
                    lk["label"] = text
                    return
            if text:
                t["labels"][item_id] = text
            else:
                t["labels"].pop(item_id, None)

        self._edit(tr("topo.renamed"), fn)

    def move_to_group(self, node_ids: list[str], group_id: str) -> None:
        def fn(t: dict[str, Any]) -> None:
            for node_id in node_ids:
                t["groups"][node_id] = group_id
                t["positions"].pop(node_id, None)

        self._edit(tr("topo.regrouped"), fn)

    def reset_positions(self, node_ids: list[str] | None = None) -> None:
        def fn(t: dict[str, Any]) -> None:
            if node_ids is None:
                t["positions"] = {}
            for node_id in node_ids or []:
                t["positions"].pop(node_id, None)

        self._edit(tr("topo.auto_place"), fn)

    def restore_links(self) -> None:
        self._edit(tr("topo.links_restored"), lambda t: t.update(hidden=[]))

    def reset(self) -> None:
        def fn(t: dict[str, Any]) -> None:
            for key in ("positions", "labels", "kinds", "groups"):
                t[key] = {}
            t["hidden"], t["links"] = [], []

        self.canvas.select("", "")
        self._edit(tr("topo.reset"), fn)

    def _connect_selected(self) -> None:
        nodes = self.canvas.selected_ids("node")
        if len(nodes) >= 2:
            self.add_link(nodes[0], nodes[1])
        elif nodes:
            self.canvas.start_connect(nodes[0])
            self.state.message.emit("info", tr("topo.connect_hint"))

    def rename(self, kind: str, item_id: str) -> None:
        d = self.canvas.diagram
        if d is None:
            return
        topo = d.topo
        current = ""
        if kind == "node" and (n := topo.node(item_id)) is not None:
            current = n.label
        elif kind == "link" and (lk := topo.link(item_id)) is not None:
            current = lk.label
        elif kind == "group":
            current = next((g.title for g in topo.groups if g.id == item_id), "")
        else:
            return
        text, ok = QInputDialog.getText(self, tr("topo.rename"), tr("topo.rename_prompt"), text=current)
        if ok and text.strip() != current:
            self.set_label(item_id, text.strip())

    # ---- context menu ------------------------------------------------------------------------------
    def _context(self, kind: str, item_id: str, pos: QPoint) -> None:
        d = self.canvas.diagram
        if d is None or self.scope.value() != "site":
            return
        topo = d.topo
        menu = QMenu(self)

        def act(text: str, fn: Callable[[], None], icon: str | None = None, danger: bool = False) -> QAction:
            a = QAction(icons.icon(icon, "error" if danger else None, 16), text, menu) if icon else QAction(text, menu)
            a.triggered.connect(fn)
            menu.addAction(a)
            return a

        if kind == "node":
            nodes = self.canvas.selected_ids("node") or [item_id]
            act(tr("topo.rename"), lambda: self.rename("node", item_id), "pencil")
            if len(nodes) == 2:
                act(tr("topo.connect_two"), lambda: self.add_link(nodes[0], nodes[1]), "cable")
            else:
                act(tr("topo.connect"), lambda: self.canvas.start_connect(item_id), "cable")
            node = topo.node(item_id)
            if node is not None and node.group:
                sub = menu.addMenu(icons.icon("layers", size=16), tr("topo.move_to_group"))
                for g in topo.groups:
                    a = QAction(g.title, sub)
                    a.setEnabled(g.id != node.group)
                    a.triggered.connect(lambda _=False, gid=g.id: self.move_to_group(nodes, gid))
                    sub.addAction(a)
                sub.addSeparator()
                a = QAction(tr("topo.new_group"), sub)
                a.triggered.connect(lambda: self.move_to_group(nodes, "u-" + uuid.uuid4().hex[:6]))
                sub.addAction(a)
            if any((n := topo.node(i)) is not None and n.manual for i in nodes):
                act(tr("topo.auto_place"), lambda: self.reset_positions(nodes), "refresh-ccw")
        elif kind == "link":
            links = self.canvas.selected_ids("link") or [item_id]
            lk = topo.link(item_id)
            act(tr("topo.rename_link"), lambda: self.rename("link", item_id), "pencil")
            sub = menu.addMenu(icons.icon("sliders-horizontal", size=16), tr("topo.link_type"))
            for k in LINK_KINDS:
                a = QAction(tr(f"topo.kind.{k}"), sub)
                a.setCheckable(True)
                a.setChecked(lk is not None and lk.kind == k)
                a.triggered.connect(lambda _=False, kk=k: self.set_kind(item_id, kk))
                sub.addAction(a)
            menu.addSeparator()
            text = tr("topo.link_remove_n", n=len(links)) if len(links) > 1 else tr("topo.link_remove")
            act(text, lambda: self.delete_links(links), "trash-2", danger=True)
        elif kind == "group":
            act(tr("topo.rename_group"), lambda: self.rename("group", item_id), "pencil")
        if self.edits.hidden:
            act(tr("topo.links_restore", n=len(self.edits.hidden)), self.restore_links, "refresh-ccw")
        if self.edits.positions:
            act(tr("topo.auto_place_all"), lambda: self.reset_positions(None), "refresh-ccw")
        if not menu.actions():
            return
        menu.exec(pos)
