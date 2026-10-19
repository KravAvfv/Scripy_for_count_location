"""Connection wizard of the topology page: asks, one item at a time, how everything is connected.

The user picks what to go through (the ODFs, the fibre links between cabinets, the other links, the copper
patch panels) and answers as much as they like: the name, duplex ports and cable of every ODF and the
port each link takes in it; the medium, ports, modules, cable and ODF ports of every link; the switch
ports of every patch panel. Every field is pre-filled with what the drawing shows (grey = automatic) —
an empty field stays automatic, and "Skip" leaves an item as it is. "Finish" applies the answers given
so far as one undoable edit and the drawing is rebuilt from them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.catalog import Catalog
from ...core.models import TopologyEdits
from ...core.topology import ODF_MAX_PORTS, TopoLinkResult, Topology, TopoNode, link_title
from ...i18n import tr
from .connection_panel import module_models
from .controls import SpinBox, button, clear_layout, hline, label, px

MEDIA = ("", "dac", "fiber", "copper")


@dataclass
class WizardChanges:
    """The answers, as edits of the raw ``topology`` dict (``""`` / ``0`` = automatic again)."""

    labels: dict[str, str] = field(default_factory=dict)
    odf_ports: dict[str, int] = field(default_factory=dict)
    odf_map: dict[str, int] = field(default_factory=dict)
    ports: dict[str, str] = field(default_factory=dict)
    modules: dict[str, str] = field(default_factory=dict)
    media: dict[str, str] = field(default_factory=dict)
    panel_map: dict[str, str] = field(default_factory=dict)
    user_links: dict[str, str] = field(default_factory=dict)
    """User-drawn link id -> its cable label (kept on the link itself)."""
    uplinks: dict[str, str] = field(default_factory=dict)
    """ODF -> cabinet key its cable goes to (``""`` = automatic); kept in the cabinets, not by :meth:`apply`."""

    @property
    def drawing(self) -> bool:
        """Answers about the drawing itself (everything but :attr:`uplinks`)."""
        return any(
            (
                self.labels,
                self.odf_ports,
                self.odf_map,
                self.ports,
                self.modules,
                self.media,
                self.panel_map,
                self.user_links,
            )
        )

    def __bool__(self) -> bool:
        return self.drawing or bool(self.uplinks)

    def apply(self, t: dict[str, Any]) -> None:
        def put(store: dict[str, Any], key: str, value: Any) -> None:
            if value:
                store[key] = value
            else:
                store.pop(key, None)

        for name in ("labels", "odf_ports", "odf_map", "ports", "modules", "media", "panel_map"):
            store = t.setdefault(name, {})
            for key, value in getattr(self, name).items():
                put(store, key, value)
        for ul in t.get("links", []):
            if ul.get("id") in self.user_links:
                ul["label"] = self.user_links[ul["id"]]


@dataclass
class _Item:
    kind: str
    """``odf``, ``link`` or ``panel``."""
    id: str


class ConnectionWizard(QDialog):
    def __init__(
        self,
        topo: Topology,
        edits: TopologyEdits,
        catalog: Catalog,
        parent: QWidget | None = None,
        uplinks: Callable[[str], tuple[list[tuple[str, str]], str, str]] | None = None,
    ) -> None:
        """``uplinks(odf)``: where the ODF's cable may go (see ``TopologyView.uplink_options``)."""
        super().__init__(parent)
        self.topo = topo
        self.uplinks = uplinks
        self.edits = edits
        self.changes = WizardChanges()
        self.items: list[_Item] = []
        self.at = -1
        self._modules = module_models(catalog)
        self._fields: dict[str, Any] = {}
        self.setWindowTitle(tr("wiz.title"))
        self.resize(px(620), px(600))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(20), px(16), px(20), px(16))
        lay.setSpacing(px(10))
        self.heading = label(tr("wiz.title"), "subtitle")
        lay.addWidget(self.heading)
        self.step = label("", "muted")
        lay.addWidget(self.step)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)

        # ---- what to go through -------------------------------------------------------------------
        intro = QWidget()
        il = QVBoxLayout(intro)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(px(8))
        il.addWidget(label(tr("wiz.intro"), None, wrap=True))
        odfs = [n for n in topo.nodes if n.kind == "odf"]
        panels = [n for n in topo.nodes if n.kind == "panel"]
        fibre = [lk for lk in topo.links if lk.via or self._on_odf(lk)]
        other = [lk for lk in topo.links if lk not in fibre and lk.kind != "patch"]
        self.scopes: dict[str, tuple[QCheckBox, list[_Item]]] = {}
        for key, items, on in (
            ("odf", [_Item("odf", n.id) for n in odfs], True),
            ("fibre", [_Item("link", lk.id) for lk in fibre], True),
            ("other", [_Item("link", lk.id) for lk in other], False),
            ("panel", [_Item("panel", n.id) for n in panels], False),
        ):
            box = QCheckBox(tr(f"wiz.scope_{key}", n=len(items)))
            box.setChecked(on and bool(items))
            box.setEnabled(bool(items))
            il.addWidget(box)
            self.scopes[key] = (box, items)
        if not odfs:
            il.addWidget(label(tr("wiz.no_odf"), "faint", wrap=True))
        il.addStretch(1)
        self.stack.addWidget(intro)

        # ---- one item ----------------------------------------------------------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        page = QWidget()
        self.page_lay = QVBoxLayout(page)
        self.page_lay.setContentsMargins(0, 0, px(6), 0)
        self.page_lay.setSpacing(px(8))
        scroll.setWidget(page)
        self.stack.addWidget(scroll)

        row = QHBoxLayout()
        row.setSpacing(px(8))
        self.back = button(tr("wiz.back"), "ghost", "chevron-left")
        self.back.clicked.connect(lambda: self._go(-1))
        row.addWidget(self.back)
        row.addStretch(1)
        self.skip = button(tr("wiz.skip"), "ghost")
        self.skip.setToolTip(tr("wiz.skip_tip"))
        self.skip.clicked.connect(self._skip)
        row.addWidget(self.skip)
        self.finish = button(tr("wiz.finish"), None, "check")
        self.finish.setToolTip(tr("wiz.finish_tip"))
        self.finish.clicked.connect(self._finish)
        row.addWidget(self.finish)
        self.next = button(tr("wiz.start"), "primary", "chevron-right")
        self.next.clicked.connect(lambda: self._go(1))
        row.addWidget(self.next)
        lay.addLayout(row)
        self._sync()

    # ---- navigation ------------------------------------------------------------------------------
    def _on_odf(self, lk: TopoLinkResult) -> bool:
        return any((n := self.topo.node(i)) is not None and n.kind == "odf" for i in (lk.a, lk.b))

    def _go(self, step: int) -> None:
        if self.at < 0:
            self.items = [it for box, items in self.scopes.values() if box.isChecked() for it in items]
            if not self.items:
                self._finish()
                return
            self.at = 0
            self._load()
            return
        self._save()
        new = self.at + step
        if new >= len(self.items):
            self._finish()
            return
        if new < 0:
            self.at = -1
            self._sync()
            return
        self.at = new
        self._load()

    def _skip(self) -> None:
        """Leave the item as it is (forget what was typed on this page)."""
        self._fields = {}
        new = self.at + 1
        if new >= len(self.items):
            self._finish()
            return
        self.at = new
        self._load()

    def _finish(self) -> None:
        if self.at >= 0:
            self._save()
        self.accept()

    def _sync(self) -> None:
        intro = self.at < 0
        self.stack.setCurrentIndex(0 if intro else 1)
        self.back.setVisible(not intro)
        self.skip.setVisible(not intro)
        self.finish.setVisible(not intro)
        last = not intro and self.at == len(self.items) - 1
        self.next.setText(tr("wiz.start") if intro else tr("wiz.done") if last else tr("wiz.next"))
        self.step.setText(tr("wiz.intro_sub") if intro else tr("wiz.step", n=self.at + 1, total=len(self.items)))

    # ---- a page --------------------------------------------------------------------------------------
    def _line(self, current: str, auto: str, tip: str = "") -> QLineEdit:
        edit = QLineEdit(current)
        edit.setPlaceholderText(auto or tr("topo.auto"))
        if tip:
            edit.setToolTip(tip)
        return edit

    def _module(self, current: str, auto: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItem("")
        combo.addItems(self._modules)
        combo.setCurrentText(current)
        if combo.lineEdit() is not None:
            combo.lineEdit().setPlaceholderText(auto or tr("topo.no_module"))
        return combo

    def _port_spin(self, value: int) -> SpinBox:
        spin = SpinBox()
        spin.setRange(1, ODF_MAX_PORTS)
        spin.setValue(max(1, value))
        spin.setFixedWidth(px(80))
        return spin

    def _load(self) -> None:
        clear_layout(self.page_lay)
        self._fields = {}
        item = self.items[self.at]
        if item.kind == "odf":
            node = self.topo.node(item.id)
            if node is not None:
                self._load_odf(node)
        elif item.kind == "panel":
            node = self.topo.node(item.id)
            if node is not None:
                self._load_panel(node)
        else:
            lk = self.topo.link(item.id)
            if lk is not None:
                self._load_link(lk)
        self.page_lay.addStretch(1)
        self._sync()

    def _typed(self, key: str, store: dict[str, Any], value: str) -> str:
        """The value of a field the user set before (an automatic one stays empty, shown in grey)."""
        return value if key in store else ""

    def _load_odf(self, n: TopoNode) -> None:
        ed = self.edits
        self.heading.setText(f"{n.label} {n.caption}")
        self.page_lay.addWidget(label(tr("wiz.odf_sub", cabinet=n.cabinet, model=n.model or "—"), "muted", wrap=True))
        form = QFormLayout()
        name = self._line(self._pending("labels", n.id, self._typed(n.id, ed.labels, n.label)), n.label)
        form.addRow(tr("topo.odf_name"), name)
        ports = SpinBox()
        ports.setRange(1, ODF_MAX_PORTS)
        ports.setValue(self.changes.odf_ports.get(n.id) or n.ports)
        ports.setToolTip(tr("topo.odf_ports_tip"))
        form.addRow(tr("topo.odf_ports"), ports)
        key = n.id + "#cable"
        cable = self._line(
            self._pending("labels", key, self._typed(key, ed.labels, n.cable)), n.cable, tr("topo.odf_cable_tip")
        )
        form.addRow(tr("topo.odf_cable"), cable)
        goes: QComboBox | None = None
        if self.uplinks is not None:
            options, chosen, tip = self.uplinks(n.id)
            goes = QComboBox()
            for key_, text in options:
                goes.addItem(text, key_)
            if not options:
                goes.addItem(n.caption.removeprefix("→ ").strip() or "—", None)
            current = self.changes.uplinks.get(n.id, chosen)
            goes.setCurrentIndex(max(0, goes.findData(current)) if options else 0)
            goes.setEnabled(bool(options))
            goes.setToolTip(tip or tr("topo.odf_goes_tip"))
            goes.setProperty("was", chosen)
            form.addRow(tr("topo.odf_goes"), goes)
        self.page_lay.addLayout(form)
        self._fields = {"name": name, "ports": ports, "cable": cable, "goes": goes, "links": {}}
        through = self.topo.links_through(n.id)
        self.page_lay.addWidget(hline())
        if not through:
            self.page_lay.addWidget(label(tr("wiz.odf_empty"), "faint", wrap=True))
            return
        self.page_lay.addWidget(label(tr("wiz.odf_links"), "caption"))
        links = QFormLayout()
        for lk in through:
            port = next((p for p, lid in n.cells.items() if lid == lk.id), 0)
            pending = self.changes.odf_map.get(f"{lk.id}@{n.id}")
            spin = self._port_spin(pending or port)
            links.addRow(label(link_title(self.topo, lk), None), spin)
            self._fields["links"][lk.id] = (spin, port)
        self.page_lay.addLayout(links)

    def _load_panel(self, n: TopoNode) -> None:
        ed = self.edits
        sw = self.topo.node(n.switch)
        self.heading.setText(n.label)
        self.page_lay.addWidget(
            label(tr("wiz.panel_sub", switch=sw.label if sw else "—", cabinet=n.cabinet), "muted", wrap=True)
        )
        form = QFormLayout()
        name = self._line(self._pending("labels", n.id, self._typed(n.id, ed.labels, n.label)), n.label)
        form.addRow(tr("topo.odf_name"), name)
        auto = n.caption.split("→")[-1].strip()
        mapping = self._line(
            self.changes.panel_map.get(n.id, ed.panel_map.get(n.id, "")), auto, tr("topo.panel_map_tip")
        )
        form.addRow(tr("topo.panel_map"), mapping)
        self.page_lay.addLayout(form)
        self._fields = {"name": name, "map": mapping}

    def _pending(self, store: str, key: str, current: str) -> str:
        """What was typed for this field earlier in the wizard, else its current value."""
        return getattr(self.changes, store).get(key, current)

    def _load_link(self, lk: TopoLinkResult) -> None:
        ed = self.edits
        self.heading.setText(link_title(self.topo, lk))
        names = {n.id: n.label for n in self.topo.nodes}
        self.page_lay.addWidget(label(tr(f"topo.kind.{lk.kind}") if lk.kind != "patch" else "", "muted"))
        form = QFormLayout()
        medium = QComboBox()
        for m in MEDIA:
            medium.addItem(
                tr("topo.auto")
                if not m
                else {"dac": "DAC", "fiber": tr("topo.medium_fiber")}.get(m, tr("topo.medium_copper")),
                m,
            )
        medium.setCurrentIndex(max(0, medium.findData(self.changes.media.get(lk.id, ed.media.get(lk.id, "")))))
        form.addRow(tr("topo.medium"), medium)
        self._fields = {"medium": medium, "ends": {}, "via": {}}
        for nid, port, module in ((lk.a, lk.port_a, lk.module_a), (lk.b, lk.port_b, lk.module_b)):
            key = lk.end(nid)
            node = self.topo.node(nid)
            title = label(names.get(nid, nid), None)
            title.setStyleSheet("font-weight: 600;")
            form.addRow(title)
            p_edit = self._line(
                self._pending("ports", key, self._typed(key, ed.ports, port)), port, tr("topo.port_tip")
            )
            form.addRow(tr("topo.port"), p_edit)
            m_box = None
            if node is None or node.kind not in ("odf", "panel"):
                m_box = self._module(self._pending("modules", key, self._typed(key, ed.modules, module)), module)
                form.addRow(tr("topo.module"), m_box)
            self._fields["ends"][key] = (p_edit, m_box)
        typed = lk.label if (lk.id in ed.labels or not lk.auto) else ""
        cable = self._line(self.changes.user_links.get(lk.id, self.changes.labels.get(lk.id, typed)), lk.label)
        cable.setToolTip(tr("topo.cable_tip"))
        form.addRow(tr("topo.cable"), cable)
        self._fields["cable"] = cable
        self.page_lay.addLayout(form)
        if lk.via:
            self.page_lay.addWidget(hline())
            self.page_lay.addWidget(label(tr("wiz.link_via"), "caption"))
            via = QFormLayout()
            for odf_id, port in lk.via:
                o = self.topo.node(odf_id)
                spin = self._port_spin(self.changes.odf_map.get(f"{lk.id}@{odf_id}") or port)
                via.addRow(f"{o.label} {o.caption}" if o else odf_id, spin)
                self._fields["via"][odf_id] = (spin, port)
            self.page_lay.addLayout(via)

    # ---- answers --------------------------------------------------------------------------------------
    def _save(self) -> None:
        if not self._fields or self.at < 0:
            return
        item = self.items[self.at]
        f = self._fields
        c = self.changes
        if item.kind in ("odf", "panel"):
            node = self.topo.node(item.id)
            name = f["name"].text().strip()
            if name != self._typed(item.id, self.edits.labels, node.label if node else ""):
                c.labels[item.id] = name
            if item.kind == "panel":
                text = f["map"].text().strip()
                if text != self.edits.panel_map.get(item.id, ""):
                    c.panel_map[item.id] = text
                return
            if node is not None:
                ports = f["ports"].value()
                if ports != node.ports:
                    c.odf_ports[item.id] = 0 if ports == node.auto_ports else ports
                key = item.id + "#cable"
                cable = f["cable"].text().strip()
                if cable != self._typed(key, self.edits.labels, node.cable):
                    c.labels[key] = cable
            goes = f.get("goes")
            if goes is not None and goes.isEnabled():
                to = goes.currentData()
                if to is not None and to != goes.property("was"):
                    c.uplinks[item.id] = to
                else:
                    c.uplinks.pop(item.id, None)
            for lid, (spin, was) in f["links"].items():
                if spin.value() != was:
                    lk = self.topo.link(lid)
                    if lk is not None and item.id in (lk.a, lk.b):
                        c.ports[lk.end(item.id)] = str(spin.value())
                    else:
                        c.odf_map[f"{lid}@{item.id}"] = spin.value()
            return
        lk = self.topo.link(item.id)
        if lk is None:
            return
        ed = self.edits
        medium = f["medium"].currentData() or ""
        if medium != ed.media.get(lk.id, ""):
            c.media[lk.id] = medium
        for key, (p_edit, m_box) in f["ends"].items():
            port = p_edit.text().strip()
            if port != (ed.ports.get(key, "")):
                c.ports[key] = port
            if m_box is not None:
                module = m_box.currentText().strip()
                if module != ed.modules.get(key, ""):
                    c.modules[key] = module
        cable = f["cable"].text().strip()
        typed = lk.label if (lk.id in ed.labels or not lk.auto) else ""
        if cable != typed:
            if lk.auto:
                c.labels[lk.id] = cable
            else:
                c.user_links[lk.id] = cable
        for odf_id, (spin, was) in f["via"].items():
            if spin.value() != was:
                c.odf_map[f"{lk.id}@{odf_id}"] = spin.value()


__all__ = ["ConnectionWizard", "WizardChanges"]
