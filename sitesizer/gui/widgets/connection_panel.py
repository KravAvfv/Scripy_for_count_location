"""Side panel of the topology page: the selected link for the installers — how it is made (DAC, modules +
fibre, copper), the port and the module at each end, the cable — or what a selected switch's ports serve.

Every field is applied when it is left (Enter / focus out / a choice in a list); an empty field is
automatic again, and the grey placeholder shows what the app puts there.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFrame, QGridLayout, QLineEdit, QVBoxLayout, QWidget

from ...core.catalog import Catalog
from ...core.models import TopologyEdits
from ...core.topology import TopoLinkResult, Topology
from ...i18n import tr
from .controls import SegmentedControl, button, hline, label, px


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
        lay.addStretch(1)
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
            self.link_box.setVisible(self.link is not None and edits is not None)
            self.node_box.setVisible(bool(self.node_id))
            self.hint.setVisible(self.link is None and not self.node_id)
            if self.link is not None and edits is not None and topo is not None:
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
