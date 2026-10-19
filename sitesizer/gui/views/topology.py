"""Topology view: the location's network device by device, editable; or all locations at a glance.

"Ця локація" shows :class:`~sitesizer.exporters.topology.TopologyDiagram` of the whole location
(every floor): FortiGates, core, access stacks, with the names from the cabinets. Devices are dragged,
links added / removed / retyped / relabelled, switches moved to another stack, and the access design
switched between stacks (rings) and dual-homed — all undoable edits of ``SiteInput.topology``
(shared by every floor). The cabinets' ODFs (and, when switched on, the copper patch panels) are on the
drawing: a click on one shows its ports in the side panel, where links are put into other ports and
notes written; the connection wizard asks about every ODF / link in turn. Exports: PNG, SVG, draw.io.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPoint, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFrame, QHBoxLayout, QInputDialog, QMenu, QVBoxLayout, QWidget

from ...core.models import RackPlan, TopologyEdits
from ...core.passive import uplink_choices
from ...core.topology import LINK_KINDS
from ...exporters.diagram import Diagram, ProjectDiagram, style_from_tokens
from ...exporters.topology import TopologyDiagram
from ...i18n import tr
from .. import icons
from ..lazy import when_shown
from ..state import AppState
from ..theme import theme_manager, tokens
from ..widgets.connection_panel import ConnectionPanel
from ..widgets.controls import SegmentedControl, SpinBox, ToggleSwitch, button, icon_button, label, px, vline
from ..widgets.topology_canvas import TopologyCanvas

if TYPE_CHECKING:
    from ..widgets.connection_wizard import WizardChanges


class TopologyView(QWidget):
    export_requested = Signal(str)  # "png" | "svg" | "drawio"

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._loading = False
        self._reselect = ""
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
        bar.addWidget(vline())
        apw = button(tr("apw.open"), None, "wifi", tr("apw.open_tip"))
        apw.clicked.connect(self.open_ap_wiring)
        bar.addWidget(apw)
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
        eb.addSpacing(px(8))
        self.odf = ToggleSwitch()
        self.odf.setToolTip(tr("topo.show_odf_tip"))
        self.odf.toggled.connect(lambda on: self._edit("ODF", lambda t: t.update(show_odf=bool(on))))
        eb.addWidget(self.odf)
        eb.addWidget(label("ODF", "caption"))
        eb.addSpacing(px(8))
        self.panels = ToggleSwitch()
        self.panels.setToolTip(tr("topo.show_panels_tip"))
        self.panels.toggled.connect(
            lambda on: self._edit(tr("topo.show_panels"), lambda t: t.update(show_panels=bool(on)))
        )
        eb.addWidget(self.panels)
        eb.addWidget(label(tr("topo.show_panels"), "caption"))
        eb.addStretch(1)
        self.wizard_btn = button(tr("wiz.open"), None, "clipboard-list", tr("wiz.open_tip"))
        self.wizard_btn.clicked.connect(self.open_wizard)
        eb.addWidget(self.wizard_btn)
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
        body = QHBoxLayout()
        body.setSpacing(px(12))
        body.addWidget(frame, 1)
        # the selected link for the installers: medium, ports, modules, cable
        self.panel = ConnectionPanel(state.catalog)
        self.panel.connectionChanged.connect(self.set_connection)
        self.panel.mediumChanged.connect(self.set_medium)
        self.panel.endpointsChanged.connect(self.set_endpoints)
        self.panel.passiveChanged.connect(self.set_passive)
        self.panel.odfPortChanged.connect(self.set_odf_port)
        self.panel.noteChanged.connect(self.set_note)
        self.panel.uplinkChanged.connect(self.set_uplink)
        body.addWidget(self.panel)
        root.addLayout(body, 1)
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
        self.canvas.selectionChanged.connect(self._sync_panel)
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

    def open_ap_wiring(self) -> None:
        """The scheme of which switch port every access point goes to (for the installers)."""
        from ..widgets.ap_wiring_dialog import ApWiringDialog

        dlg = ApWiringDialog(self.state, self.export_requested.emit, self)
        dlg.exec()

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
            self.odf.setChecked(ed.show_odf)
            self.panels.setChecked(ed.show_panels)
        finally:
            self._loading = False
        self.canvas.set_diagram(self.build_diagram())
        d = self.canvas.diagram
        if self._reselect and isinstance(d, TopologyDiagram) and d.topo.node(self._reselect) is not None:
            self.canvas.select("node", self._reselect)
        self._reselect = ""
        self._sync_buttons()
        self._sync_panel()

    def _sync_panel(self) -> None:
        """The panel shows the one link or switch selected (several / none: its hint)."""
        d = self.canvas.diagram
        links, nodes = self.canvas.selected_ids("link"), self.canvas.selected_ids("node")
        editable = self.scope.value() == "site" and isinstance(d, TopologyDiagram)
        self.panel.setVisible(editable)
        if not editable:
            return
        assert isinstance(d, TopologyDiagram)
        if len(links) == 1 and not nodes:
            self.panel.show_item(d.topo, "link", links[0], self.edits)
        elif len(nodes) == 1 and not links:
            self.panel.show_item(d.topo, "node", nodes[0], self.edits)
            if self.panel.passive_kind == "odf":
                self._sync_uplinks(nodes[0])
        else:
            self.panel.show_item(None, "", "", None)

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
                ("ports", {}),
                ("modules", {}),
                ("endpoints", {}),
                ("media", {}),
                ("odf_ports", {}),
                ("odf_map", {}),
                ("port_notes", {}),
                ("panel_map", {}),
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
            for key in (
                "positions",
                "labels",
                "kinds",
                "groups",
                "ports",
                "modules",
                "endpoints",
                "media",
                "odf_ports",
                "odf_map",
                "port_notes",
                "panel_map",
            ):
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

    def set_connection(self, link_id: str, values: dict[str, str]) -> None:
        """Ports / modules of both ends and the cable of a link (``""`` = automatic again)."""
        lk = self.canvas.diagram.topo.link(link_id) if self.canvas.diagram else None
        if lk is None:
            return

        def fn(t: dict[str, Any]) -> None:
            for field, node in (("port_a", lk.a), ("port_b", lk.b), ("module_a", lk.a), ("module_b", lk.b)):
                store = t["ports" if field.startswith("port") else "modules"]
                text = values.get(field, "").strip()
                if text:
                    store[lk.end(node)] = text
                else:
                    store.pop(lk.end(node), None)
            cable = values.get("cable", "").strip()  # empty: the automatic cable (DAC length, fibre type)
            for ul in t["links"]:
                if ul["id"] == link_id:
                    ul["label"] = cable
                    return
            if cable:
                t["labels"][link_id] = cable
            else:
                t["labels"].pop(link_id, None)

        self._edit(tr("topo.connection"), fn)

    def set_medium(self, link_id: str, medium: str | None) -> None:
        """How a link is made: ``dac``, ``fiber`` (modules + fibre), ``copper``; ``None`` = automatic."""

        def fn(t: dict[str, Any]) -> None:
            if medium:
                t["media"][link_id] = medium
            else:
                t["media"].pop(link_id, None)

        self._edit(tr("topo.medium"), fn)

    def edit_connection(self, link_id: str) -> None:
        """Select the link and put the cursor into the panel's first port field."""
        self.canvas.select("link", link_id)
        self._sync_panel()
        self.panel.ends["a"][1].setFocus()

    def set_endpoints(self, node_id: str, text: str) -> None:
        def fn(t: dict[str, Any]) -> None:
            if text:
                t["endpoints"][node_id] = text
            else:
                t["endpoints"].pop(node_id, None)

        self._edit(tr("topo.endpoints"), fn)

    # ---- ODFs and copper patch panels ------------------------------------------------------------
    def set_passive(self, node_id: str, values: dict[str, Any]) -> None:
        """Name, duplex ports and cable of an ODF / the switch ports of a copper panel (``""`` = automatic);
        ``{"reset": True}``: the ODF's ports automatic again."""
        d = self.canvas.diagram
        n = d.topo.node(node_id) if d is not None else None
        if n is None:
            return

        def put(store: dict[str, Any], key: str, value: Any) -> None:
            if value:
                store[key] = value
            else:
                store.pop(key, None)

        def fn(t: dict[str, Any]) -> None:
            if values.get("reset"):
                for key in [k for k in t["odf_map"] if k.endswith("@" + node_id)]:
                    del t["odf_map"][key]
                for lk in d.topo.links:
                    if node_id in (lk.a, lk.b):
                        t["ports"].pop(lk.end(node_id), None)
                return
            if "name" in values:
                put(t["labels"], node_id, values["name"])
            if "cable" in values:
                put(t["labels"], node_id + "#cable", values["cable"])
            if "ports" in values:
                ports = int(values["ports"] or 0)
                put(t["odf_ports"], node_id, ports if ports and ports != n.auto_ports else 0)
            if "map" in values:
                put(t["panel_map"], node_id, values["map"])

        self._edit(tr("topo.odf") if n.kind == "odf" else tr("topo.panel"), fn)

    def _odf_floor(self, node_id: str) -> tuple[str, list[RackPlan], list[tuple[str, str]], str, str] | None:
        """(floor entry, its cabinets, its fibre links, cabinet, peer) of an automatic ODF."""
        tag, _s, item = node_id.partition("/")
        if not item.startswith("odf:"):
            return None
        _o, key, peer = item.split(":", 2)
        loc = self.state.location
        floors = loc.floors if loc is not None and loc.floors else []
        if not floors and self.state.result is not None:
            floors = [(self.state.current_id, self.state.result)]
        for eid, r in floors:
            plans = r.rack.plans
            if any(p.tag == tag and p.key == key for p in plans):
                return eid, plans, r.rack.fiber_pairs, key, peer
        return None

    def uplink_options(self, node_id: str) -> tuple[list[tuple[str, str]], str, str]:
        """Where an ODF's cable may go: (cabinet key, text) options (``""`` = automatic), the current
        choice, and why it is read-only when there are no options (it is changed from the cabinet
        whose cable it is; the provider's fibre and the backbone between floors not at all)."""
        found = self._odf_floor(node_id)
        if found is None:
            return [], "", tr("topo.odf_goes_fixed")
        eid, plans, pairs, key, peer = found
        tags = {p.key: p.tag for p in plans}
        if peer not in tags or (key, peer) not in pairs:
            tip = tr("topo.odf_goes_parent", tag=tags[peer]) if peer in tags else tr("topo.odf_goes_fixed")
            return [], "", tip
        entry = self.state.project.site(eid)
        chosen = entry.input.layout.uplinks.get(key, "") if entry is not None else ""
        auto = tr("topo.auto") if chosen else tr("topo.odf_goes_auto", tag=tags[peer])
        options = [("", auto)] + [(k, "→ " + tags[k]) for k in uplink_choices(pairs, list(tags), key) if k in tags]
        return options, chosen, ""

    def _sync_uplinks(self, node_id: str) -> None:
        self.panel.set_uplinks(*self.uplink_options(node_id))

    def set_uplink(self, node_id: str, to: str, select: bool = True) -> None:
        """Send the cable of the ODF's cabinet to another cabinet (``""``: the automatic one).

        The cabinets are the source of truth: the choice is stored in the floor's cabinet layout,
        so the ODFs, organizers, fibre cables and the specification follow it.
        """
        found = self._odf_floor(node_id)
        if found is None:
            return
        eid, plans, _pairs, key, peer = found
        if to == peer:
            return

        def fn(d: dict[str, Any]) -> None:
            ups = d.setdefault("layout", {}).setdefault("uplinks", {})
            if to:
                ups[key] = to
            else:
                ups.pop(key, None)

        tag = next(p.tag for p in plans if p.key == key)
        if to and select:
            self._reselect = f"{tag}/odf:{key}:{to}"
        self.state.edit_floor(eid, tr("topo.odf_goes"), fn)

    def set_odf_port(self, odf_id: str, port: int, link: str) -> None:
        """Put a link into a port of an ODF: the link that was there moves to the port it leaves (swap);
        ``link == ""`` frees the port (its link goes to the first free one)."""
        d = self.canvas.diagram
        o = d.topo.node(odf_id) if d is not None else None
        if d is None or o is None:
            return
        topo = d.topo

        def where(lid: str) -> int:
            return next((p for p, other in o.cells.items() if other == lid), 0)

        def assign(t: dict[str, Any], lid: str, n: int) -> None:
            lk = topo.link(lid)
            if lk is None:
                return
            if odf_id in (lk.a, lk.b):
                t["ports"][lk.end(odf_id)] = str(n)
            else:
                t["odf_map"][f"{lid}@{odf_id}"] = n

        current = o.cells.get(port, "")
        if link == current:
            return

        def fn(t: dict[str, Any]) -> None:
            if link:
                old = where(link)
                assign(t, link, port)
                if current and old:
                    assign(t, current, old)  # the two swap
                elif current:
                    assign(t, current, self._free_port(o, {port}))
            elif current:
                assign(t, current, self._free_port(o, {port}))

        self._edit(tr("topo.odf_port"), fn)

    @staticmethod
    def _free_port(o: Any, busy: set[int]) -> int:
        n = 1
        while n in o.cells or n in busy:
            n += 1
        return n

    def set_note(self, node_id: str, port: int, text: str) -> None:
        def fn(t: dict[str, Any]) -> None:
            key = f"{node_id}#{port}"
            if text:
                t["port_notes"][key] = text
            else:
                t["port_notes"].pop(key, None)

        self._edit(tr("topo.port_note"), fn)

    def open_wizard(self) -> None:
        """Ask about every ODF / link / patch panel in turn; the answers rebuild the drawing (one undo step)."""
        from ..widgets.connection_wizard import ConnectionWizard

        d = self.canvas.diagram
        if d is None or self.scope.value() != "site":
            return
        dlg = ConnectionWizard(d.topo, self.edits, self.state.catalog, self, uplinks=self.uplink_options)
        if dlg.exec() and dlg.changes:
            self.apply_wizard(dlg.changes)

    def apply_wizard(self, changes: WizardChanges) -> None:
        """One undo step: the drawing's answers and where the ODFs go (kept in the cabinets)."""
        self.state.undo.beginMacro(tr("wiz.title"))
        try:
            if changes.drawing:
                self._edit(tr("wiz.title"), lambda t: changes.apply(t))
            for odf_id, to in changes.uplinks.items():
                self.set_uplink(odf_id, to, select=False)
        finally:
            self.state.undo.endMacro()

    def edit_endpoints(self, node_id: str) -> None:
        d = self.canvas.diagram
        n = d.topo.node(node_id) if d is not None else None
        if n is None:
            return
        text, ok = QInputDialog.getText(self, tr("topo.endpoints"), tr("topo.endpoints_prompt"), text=n.endpoints)
        if ok and text.strip() != n.endpoints:
            self.set_endpoints(node_id, text.strip())

    def _focus_passive(self, node_id: str) -> None:
        self.canvas.select("node", node_id)
        self._sync_panel()
        self.panel.pas_name.setFocus()

    def rename(self, kind: str, item_id: str) -> None:
        d = self.canvas.diagram
        if d is None:
            return
        if kind == "link":
            self.edit_connection(item_id)
            return
        n = d.topo.node(item_id) if kind == "node" else None
        if n is not None and n.kind in ("odf", "panel"):
            self._focus_passive(item_id)
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
            if node is not None and node.kind in ("wifi_switch", "camera_switch", "access_switch"):
                act(tr("topo.endpoints") + "…", lambda: self.edit_endpoints(item_id), "ethernet-port")
            if node is not None and node.kind == "odf":
                act(tr("topo.odf_setup"), lambda: self._focus_passive(item_id), "sliders-horizontal")
                act(tr("topo.odf_auto"), lambda: self.set_passive(item_id, {"reset": True}), "refresh-ccw")
            if node is not None and node.kind == "panel":
                act(tr("topo.panel_setup"), lambda: self._focus_passive(item_id), "sliders-horizontal")
            if any((n := topo.node(i)) is not None and n.manual for i in nodes):
                act(tr("topo.auto_place"), lambda: self.reset_positions(nodes), "refresh-ccw")
        elif kind == "link":
            links = self.canvas.selected_ids("link") or [item_id]
            lk = topo.link(item_id)
            act(tr("topo.connection") + "…", lambda: self.edit_connection(item_id), "cable")
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
