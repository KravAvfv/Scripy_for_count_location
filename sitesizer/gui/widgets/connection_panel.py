"""Side panel of the topology page: the selected link for the installers — how it is made (DAC, modules +
fibre, copper), the port and the module at each end, the cable — or what a selected switch's ports serve.

A selected ODF shows its name, duplex ports and cable, and a row per port: the link that goes through it
(pick another one — the two swap) and a note on what is plugged in there. A copper patch panel shows the
switch ports its ports go to and a note per port.

Every field is applied when it is left (Enter / focus out / a choice in a list); an empty field is
automatic again, and the grey placeholder shows what the app puts there.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.catalog import Catalog
from ...core.models import TopologyEdits
from ...core.topology import ODF_MAX_PORTS, TopoLinkResult, Topology, TopoNode, link_title, port_range
from ...i18n import tr
from .controls import SegmentedControl, SpinBox, button, hline, label, px


def module_models(catalog: Catalog) -> list[str]:
    """Transceivers and DAC cables of the catalog (offered for a port; any text may be typed)."""
    fibre = [f.transceiver for f in catalog.passive.fiber.values() if f.transceiver]
    others = [m for m in catalog.models if any(k in m.upper() for k in ("TRAN", "SFP", "QSFP", "CABLE", "DAC"))]
    return list(dict.fromkeys([*fibre, *others]))


class ConnectionPanel(QFrame):
    connectionChanged = Signal(str, dict)
    """(link id, {port_a, port_b, module_a, module_b, cable}) — ``""`` = automatic."""
    mediumChanged = Signal(str, object)
    """(link id, ``dac`` / ``fiber`` / ``copper`` or ``None`` = automatic)"""
    endpointsChanged = Signal(str, str)
    """(switch node id, text; ``""`` = automatic)"""
    passiveChanged = Signal(str, dict)
    """(ODF / panel node id, {name, ports, cable, map}) — only the fields shown; ``""`` / 0 = automatic."""
    odfPortChanged = Signal(str, int, str)
    """(ODF node id, port, link id put into it; ``""`` = the port is freed)"""
    noteChanged = Signal(str, int, str)
    """(ODF, cabinet key its cable goes to; ``""`` = automatic)"""
    uplinkChanged = Signal(str, str)
    """(ODF / panel node id, port, note)"""

    def __init__(self, catalog: Catalog, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFixedWidth(px(330))
        self.link: TopoLinkResult | None = None
        self.node_id = ""
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(16), px(14), px(16), px(14))
        lay.setSpacing(px(8))
        self.title = label(tr("topo.connection"), "subtitle")
        lay.addWidget(self.title)
        self.hint = label(tr("topo.panel_hint"), "muted", wrap=True)
        lay.addWidget(self.hint)

        # ---- a link ------------------------------------------------------------------------
        self.link_box = QWidget()
        lb = QVBoxLayout(self.link_box)
        lb.setContentsMargins(0, 0, 0, 0)
        lb.setSpacing(px(8))
        lb.addWidget(label(tr("topo.medium"), "caption"))
        self.medium = SegmentedControl(
            [
                ("auto", tr("topo.auto")),
                ("dac", "DAC"),
                ("fiber", tr("topo.medium_fiber")),
                ("copper", tr("topo.medium_copper")),
            ],
            [
                tr("topo.medium_auto_tip"),
                tr("topo.medium_dac_tip"),
                tr("topo.medium_fiber_tip"),
                tr("topo.medium_copper_tip"),
            ],
            compact=True,
        )
        self.medium.valueChanged.connect(self._on_medium)
        lb.addWidget(self.medium)
        lb.addWidget(hline())
        self.ends: dict[str, tuple[Any, QLineEdit, QComboBox]] = {}
        modules = module_models(catalog)
        for end in ("a", "b"):
            grid = QGridLayout()
            grid.setHorizontalSpacing(px(8))
            grid.setVerticalSpacing(px(4))
            name = label("", None)
            name.setStyleSheet("font-weight: 600;")
            grid.addWidget(name, 0, 0, 1, 2)
            grid.addWidget(label(tr("topo.port"), "caption"), 1, 0)
            port = QLineEdit()
            port.setToolTip(tr("topo.port_tip"))
            port.editingFinished.connect(self._emit)
            grid.addWidget(port, 1, 1)
            grid.addWidget(label(tr("topo.module"), "caption"), 2, 0)
            module = QComboBox()
            module.setEditable(True)
            module.addItem("")
            module.addItems(modules)
            module.setToolTip(tr("topo.module_tip"))
            module.view().setMinimumWidth(px(260))
            module.activated.connect(lambda _i: self._emit())
            if module.lineEdit() is not None:
                module.lineEdit().editingFinished.connect(self._emit)
            grid.addWidget(module, 2, 1)
            grid.setColumnStretch(1, 1)
            lb.addLayout(grid)
            self.ends[end] = (name, port, module)
        lb.addWidget(hline())
        lb.addWidget(label(tr("topo.cable"), "caption"))
        self.cable = QComboBox()
        self.cable.setEditable(True)
        self.cable.setToolTip(tr("topo.cable_tip"))
        self.cable.activated.connect(lambda _i: self._emit())
        if self.cable.lineEdit() is not None:
            self.cable.lineEdit().editingFinished.connect(self._emit)
        lb.addWidget(self.cable)
        self.via = label("", "muted", wrap=True)
        self.via.setToolTip(tr("topo.via_tip"))
        lb.addWidget(self.via)
        self.reset = button(tr("topo.connection_auto"), "ghost", "refresh-ccw")
        self.reset.clicked.connect(self._reset)
        lb.addWidget(self.reset)
        lb.addWidget(label(tr("topo.connection_note"), "faint", wrap=True))
        lay.addWidget(self.link_box)

        # ---- a switch ------------------------------------------------------------------------
        self.node_box = QWidget()
        nb = QVBoxLayout(self.node_box)
        nb.setContentsMargins(0, 0, 0, 0)
        nb.setSpacing(px(6))
        self.node_name = label("", None)
        self.node_name.setStyleSheet("font-weight: 600;")
        nb.addWidget(self.node_name)
        self.node_model = label("", "muted")
        nb.addWidget(self.node_model)
        nb.addWidget(label(tr("topo.endpoints"), "caption"))
        self.endpoints = QLineEdit()
        self.endpoints.setToolTip(tr("topo.endpoints_prompt"))
        self.endpoints.editingFinished.connect(self._emit_endpoints)
        nb.addWidget(self.endpoints)
        lay.addWidget(self.node_box)

        # ---- an ODF or a copper patch panel -------------------------------------------------------
        self.passive_id = ""
        self.passive_kind = ""
        self._links: list[str] = []
        self.pas_box = QWidget()
        pb = QVBoxLayout(self.pas_box)
        pb.setContentsMargins(0, 0, 0, 0)
        pb.setSpacing(px(6))
        self.pas_where = label("", "muted", wrap=True)
        pb.addWidget(self.pas_where)
        grid = QGridLayout()
        grid.setHorizontalSpacing(px(8))
        grid.setVerticalSpacing(px(4))
        grid.addWidget(label(tr("topo.odf_name"), "caption"), 0, 0)
        self.pas_name = QLineEdit()
        self.pas_name.editingFinished.connect(self._emit_passive)
        grid.addWidget(self.pas_name, 0, 1)
        self.ports_label = label(tr("topo.odf_ports"), "caption")
        grid.addWidget(self.ports_label, 1, 0)
        self.pas_ports = SpinBox()
        self.pas_ports.setRange(1, ODF_MAX_PORTS)
        self.pas_ports.setToolTip(tr("topo.odf_ports_tip"))
        self.pas_ports.editingFinished.connect(self._emit_passive)
        grid.addWidget(self.pas_ports, 1, 1)
        self.cable_label = label(tr("topo.odf_cable"), "caption")
        grid.addWidget(self.cable_label, 2, 0)
        self.pas_cable = QLineEdit()
        self.pas_cable.setToolTip(tr("topo.odf_cable_tip"))
        self.pas_cable.editingFinished.connect(self._emit_passive)
        grid.addWidget(self.pas_cable, 2, 1)
        self.map_label = label(tr("topo.panel_map"), "caption")
        grid.addWidget(self.map_label, 3, 0)
        self.pas_map = QLineEdit()
        self.pas_map.setToolTip(tr("topo.panel_map_tip"))
        self.pas_map.editingFinished.connect(self._emit_passive)
        grid.addWidget(self.pas_map, 3, 1)
        self.goes_label = label(tr("topo.odf_goes"), "caption")
        grid.addWidget(self.goes_label, 4, 0)
        self.pas_goes = QComboBox()
        self.pas_goes.setToolTip(tr("topo.odf_goes_tip"))
        self.pas_goes.activated.connect(self._emit_uplink)
        grid.addWidget(self.pas_goes, 4, 1)
        grid.setColumnStretch(1, 1)
        pb.addLayout(grid)
        self.table_hint = label(tr("topo.odf_table_hint"), "faint", wrap=True)
        pb.addWidget(self.table_hint)
        self.table = QTableWidget(0, 3)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        head.resizeSection(2, px(86))
        self.table.itemChanged.connect(self._on_note)
        pb.addWidget(self.table, 1)
        self.pas_reset = button(tr("topo.odf_auto"), "ghost", "refresh-ccw")
        self.pas_reset.clicked.connect(self._reset_passive)
        pb.addWidget(self.pas_reset)
        lay.addWidget(self.pas_box, 1)
        self.stretch = QWidget()
        lay.addWidget(self.stretch, 1)
        self.show_item(None, "", "", None)

    # ---- loading ---------------------------------------------------------------------------------
    def show_item(self, topo: Topology | None, kind: str, item_id: str, edits: TopologyEdits | None) -> None:
        """Show the selected link or switch (anything else: the hint)."""
        self._loading = True
        try:
            self.link = topo.link(item_id) if topo is not None and kind == "link" else None
            node = topo.node(item_id) if topo is not None and kind == "node" else None
            self.node_id = (
                node.id if node is not None and node.kind.endswith("_switch") and node.kind != "core_switch" else ""
            )
            passive = node if node is not None and node.kind in ("odf", "panel") and edits is not None else None
            self.passive_id = passive.id if passive is not None else ""
            self.passive_kind = passive.kind if passive is not None else ""
            self.link_box.setVisible(self.link is not None and edits is not None)
            self.node_box.setVisible(bool(self.node_id))
            self.pas_box.setVisible(passive is not None)
            self.stretch.setVisible(passive is None)
            self.hint.setVisible(self.link is None and not self.node_id and passive is None)
            if passive is not None and topo is not None and edits is not None:
                self._load_passive(topo, passive, edits)
            elif self.link is not None and edits is not None and topo is not None:
                self._load_link(topo, self.link, edits)
                self.title.setText(tr("topo.connection"))
            elif node is not None and self.node_id and edits is not None:
                self.title.setText(tr("topo.endpoints"))
                self.node_name.setText(node.label)
                self.node_model.setText(node.model)
                if not self.endpoints.hasFocus():
                    self.endpoints.setText(node.endpoints if node.id in edits.endpoints else "")
                    self.endpoints.setPlaceholderText(node.endpoints or tr("topo.auto"))
            else:
                self.title.setText(tr("topo.connection"))
        finally:
            self._loading = False

    def _load_passive(self, topo: Topology, n: TopoNode, edits: TopologyEdits) -> None:
        odf = n.kind == "odf"
        self.title.setText(tr("topo.odf") if odf else tr("topo.panel"))
        where = f"{tr('topo.cabinet', tag=n.cabinet)} · {n.model}" if n.model else tr("topo.cabinet", tag=n.cabinet)
        self.pas_where.setText(f"{where}\n{n.caption}" if odf else where)
        for w in (self.ports_label, self.pas_ports, self.cable_label, self.pas_cable):
            w.setVisible(odf)
        self.map_label.setVisible(not odf)
        self.pas_map.setVisible(not odf)
        self.goes_label.setVisible(False)  # shown by set_uplinks()
        self.pas_goes.setVisible(False)
        if not self.pas_name.hasFocus():
            self.pas_name.setText(n.label if n.id in edits.labels else "")
            self.pas_name.setPlaceholderText(n.label)
        if odf:
            if not self.pas_ports.hasFocus():
                self.pas_ports.setValue(n.ports)
            if not self.pas_cable.hasFocus():
                key = n.id + "#cable"
                self.pas_cable.setText(n.cable if key in edits.labels else "")
                self.pas_cable.setPlaceholderText(n.cable or tr("topo.auto"))
        elif not self.pas_map.hasFocus():
            self.pas_map.setText(edits.panel_map.get(n.id, ""))
            self.pas_map.setPlaceholderText(n.caption.split("→")[-1].strip() or tr("topo.auto"))
        self.table_hint.setText(tr("topo.odf_table_hint" if odf else "topo.panel_table_hint"))
        self.pas_reset.setVisible(odf)
        # one row per port: the link through it (ODF) or the switch port it goes to (panel), and a note
        self.table.blockSignals(True)
        try:
            self.table.clearContents()
            self.table.setHorizontalHeaderLabels(
                ["№", tr("topo.odf_link") if odf else tr("topo.panel_to"), tr("topo.port_note")]
            )
            rows = n.shown_ports if odf else n.ports
            self.table.setRowCount(rows)
            through = topo.links_through(n.id) if odf else []
            self._links = [lk.id for lk in through]
            titles = [link_title(topo, lk) for lk in through]
            to = port_range(n.caption.split("→")[-1]) if not odf else []
            for row in range(rows):
                port = row + 1
                num = QTableWidgetItem(str(port))
                num.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if odf and port > n.ports:
                    num.setForeground(Qt.GlobalColor.red)
                self.table.setItem(row, 0, num)
                if odf:
                    combo = QComboBox()
                    combo.addItem(tr("topo.odf_free"), "")
                    for lid, title in zip(self._links, titles, strict=True):
                        combo.addItem(title, lid)
                    combo.setCurrentIndex(max(0, combo.findData(n.cells.get(port, ""))))
                    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                    combo.setMinimumContentsLength(8)
                    combo.view().setMinimumWidth(px(300))
                    combo.setToolTip(combo.currentText())
                    combo.activated.connect(lambda _i, p=port, c=combo: self._on_port(p, c))
                    self.table.setCellWidget(row, 1, combo)
                else:
                    item = QTableWidgetItem(str(to[row]) if row < len(to) else "—")
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    self.table.setItem(row, 1, item)
                note = QTableWidgetItem(n.notes.get(port, ""))
                note.setToolTip(tr("topo.port_note_tip"))
                self.table.setItem(row, 2, note)
        finally:
            self.table.blockSignals(False)

    def set_uplinks(self, options: list[tuple[str, str]], current: str, tip: str = "") -> None:
        """Where the selected ODF's cable may go: (cabinet key, text); ``""`` = automatic.

        No options: the row is read-only (the cable is changed at its other end, or it is the
        provider's / another floor's) and ``tip`` says why.
        """
        odf = self.passive_kind == "odf"
        self.goes_label.setVisible(odf)
        self.pas_goes.setVisible(odf)
        self.pas_goes.blockSignals(True)
        try:
            self.pas_goes.clear()
            for key, text in options:
                self.pas_goes.addItem(text, key)
            if not options:
                caption = self.pas_where.text().split("\n")[-1]
                self.pas_goes.addItem(caption.removeprefix("→ ").strip() or "—", None)
            i = self.pas_goes.findData(current) if options else 0
            self.pas_goes.setCurrentIndex(max(0, i))
            self.pas_goes.setEnabled(bool(options))
            self.pas_goes.setToolTip(tip or tr("topo.odf_goes_tip"))
        finally:
            self.pas_goes.blockSignals(False)

    def _emit_uplink(self, _index: int) -> None:
        key = self.pas_goes.currentData()
        if not self._loading and self.passive_id and key is not None:
            self.uplinkChanged.emit(self.passive_id, key)

    def _emit_passive(self) -> None:
        if self._loading or not self.passive_id:
            return
        values: dict[str, Any] = {"name": self.pas_name.text().strip()}
        if self.passive_kind == "odf":
            values["ports"] = self.pas_ports.value()
            values["cable"] = self.pas_cable.text().strip()
        else:
            values["map"] = self.pas_map.text().strip()
        self.passiveChanged.emit(self.passive_id, values)

    def _on_port(self, port: int, combo: QComboBox) -> None:
        if not self._loading and self.passive_id:
            self.odfPortChanged.emit(self.passive_id, port, combo.currentData() or "")

    def _on_note(self, item: QTableWidgetItem) -> None:
        if self._loading or not self.passive_id or item.column() != 2:
            return
        self.noteChanged.emit(self.passive_id, item.row() + 1, item.text().strip())

    def _reset_passive(self) -> None:
        if self.passive_id:
            self.passiveChanged.emit(self.passive_id, {"reset": True})

    def _load_link(self, topo: Topology, lk: TopoLinkResult, edits: TopologyEdits) -> None:
        names = {n.id: n.label for n in topo.nodes}
        self.medium.setValue(edits.media.get(lk.id, "auto"))
        for end, node, port, module in (("a", lk.a, lk.port_a, lk.module_a), ("b", lk.b, lk.port_b, lk.module_b)):
            name, port_edit, combo = self.ends[end]
            name.setText(names.get(node, node))
            key = lk.end(node)
            if not port_edit.hasFocus():
                port_edit.setText(port if key in edits.ports else "")
                port_edit.setPlaceholderText(port or tr("topo.auto"))
            line = combo.lineEdit()
            if line is not None and not line.hasFocus():
                combo.setCurrentText(module if key in edits.modules else "")
                line.setPlaceholderText(module or tr("topo.no_module"))
        hops = []
        for odf_id, port in lk.via:
            o = topo.node(odf_id)
            hops.append(f"{o.label if o else odf_id} · {tr('topo.port').lower()} {port}")
        self.via.setText(tr("topo.via", path=" → ".join(hops)) if hops else "")
        self.via.setVisible(bool(hops))
        typed = lk.id in edits.labels or not lk.auto
        suggestions = [
            f"10G DAC {tr('topo.metres', n=1)}",
            f"10G DAC {tr('topo.metres', n=3)}",
            "10G OM4",
            "10G OS2",
            "1G Cat6A",
            "LC-LC OM4",
            "LC-LC OS2",
        ]
        line = self.cable.lineEdit()
        if line is not None and not line.hasFocus():
            self.cable.clear()
            self.cable.addItem("")
            self.cable.addItems([s for s in suggestions if s != lk.label])
            self.cable.setCurrentText(lk.label if typed else "")
            line.setPlaceholderText(lk.label or tr("topo.auto"))

    # ---- edits -----------------------------------------------------------------------------------
    def values(self) -> dict[str, str]:
        out = {}
        for end, (_n, port, module) in self.ends.items():
            out[f"port_{end}"] = port.text().strip()
            out[f"module_{end}"] = module.currentText().strip()
        out["cable"] = self.cable.currentText().strip()
        return out

    def _emit(self) -> None:
        if not self._loading and self.link is not None:
            self.connectionChanged.emit(self.link.id, self.values())

    def _on_medium(self, value: object) -> None:
        if not self._loading and self.link is not None:
            self.mediumChanged.emit(self.link.id, None if value == "auto" else value)

    def _reset(self) -> None:
        if self.link is None:
            return
        link_id = self.link.id
        self.mediumChanged.emit(link_id, None)
        self.connectionChanged.emit(link_id, dict.fromkeys(("port_a", "port_b", "module_a", "module_b", "cable"), ""))

    def _emit_endpoints(self) -> None:
        if not self._loading and self.node_id:
            self.endpointsChanged.emit(self.node_id, self.endpoints.text().strip())
