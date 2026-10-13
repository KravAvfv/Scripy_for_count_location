"""One link of the topology for the installers: the port and module at each end and the cable."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QGridLayout, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from ...core.catalog import Catalog
from ...core.models import TopologyEdits
from ...core.topology import TopoLinkResult
from ...i18n import tr
from .controls import button, label, px


def module_models(catalog: Catalog) -> list[str]:
    """Transceivers and DAC cables of the catalog (offered for a port; any text may be typed)."""
    fibre = [f.transceiver for f in catalog.passive.fiber.values() if f.transceiver]
    others = [m for m in catalog.models if any(k in m.upper() for k in ("TRAN", "SFP", "QSFP", "CABLE", "DAC"))]
    return list(dict.fromkeys([*fibre, *others]))


class ConnectionDialog(QDialog):
    def __init__(
        self,
        lk: TopoLinkResult,
        name_a: str,
        name_b: str,
        catalog: Catalog,
        edits: TopologyEdits,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("topo.connection"))
        self.setMinimumWidth(px(560))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(22), px(18), px(22), px(16))
        lay.setSpacing(px(10))
        lay.addWidget(label(tr("topo.connection"), "subtitle"))
        lay.addWidget(label(tr("topo.connection_sub"), "muted", wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(px(10))
        grid.setVerticalSpacing(px(6))
        grid.addWidget(label(tr("topo.device"), "caption"), 0, 0)
        grid.addWidget(label(tr("topo.port"), "caption"), 0, 1)
        grid.addWidget(label(tr("topo.module"), "caption"), 0, 2)
        modules = module_models(catalog)
        self.fields: dict[str, QLineEdit | QComboBox] = {}
        for row, (end, name, node, port, module) in enumerate(
            (("a", name_a, lk.a, lk.port_a, lk.module_a), ("b", name_b, lk.b, lk.port_b, lk.module_b)), start=1
        ):
            grid.addWidget(label(name), row, 0)
            port_edit = QLineEdit(port if lk.end(node) in edits.ports else "")
            port_edit.setPlaceholderText(port or tr("topo.auto"))
            port_edit.setToolTip(tr("topo.port_tip"))
            port_edit.setFixedWidth(px(110))
            grid.addWidget(port_edit, row, 1)
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItem("")
            combo.addItems(modules)
            combo.setCurrentText(module if lk.end(node) in edits.modules else "")
            line = combo.lineEdit()
            if line is not None:
                line.setPlaceholderText(module or tr("topo.no_module"))
            combo.setToolTip(tr("topo.module_tip"))
            combo.setMinimumWidth(px(220))
            grid.addWidget(combo, row, 2)
            self.fields[f"port_{end}"] = port_edit
            self.fields[f"module_{end}"] = combo
        grid.setColumnStretch(2, 1)
        lay.addLayout(grid)
        cable_row = QHBoxLayout()
        cable_row.addWidget(label(tr("topo.cable"), "caption"))
        self.cable = QComboBox()
        self.cable.setEditable(True)
        self.cable.addItems(
            list(
                dict.fromkeys(
                    [
                        lk.label,
                        f"10G DAC {tr('topo.metres', n=1)}",
                        f"10G DAC {tr('topo.metres', n=3)}",
                        "10G OM4",
                        "10G OS2",
                        "1G Cat6A",
                    ]
                )
            )
        )
        self.cable.setCurrentText(lk.label)
        self.cable.setToolTip(tr("topo.cable_tip"))
        cable_row.addWidget(self.cable, 1)
        lay.addLayout(cable_row)
        lay.addWidget(label(tr("topo.connection_note"), "faint", wrap=True))
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button(tr("ui.cancel"))
        cancel.clicked.connect(self.reject)
        ok = button(tr("ui.save"), "primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def values(self) -> dict[str, str]:
        out = {}
        for key, w in self.fields.items():
            out[key] = w.currentText() if isinstance(w, QComboBox) else w.text()
        out["cable"] = self.cable.currentText()
        return out
