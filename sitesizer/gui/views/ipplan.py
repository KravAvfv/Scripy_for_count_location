"""IP plan view: location ID (second octet) → VLAN table with editable IDs, names and masks."""

from __future__ import annotations

import ipaddress
import uuid
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, QPoint, Qt
from PySide6.QtGui import QAction, QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLineEdit,
    QMenu,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...core.models import IpSegmentResult, SiteResult
from ...i18n import current, tr
from .. import icons
from ..lazy import when_shown
from ..state import AppState
from ..theme import tokens
from ..widgets.controls import Callout, Card, Chip, FieldRow, FlowLayout, OptionalIntEdit, button, label, px

COL_VLAN, COL_NAME, COL_HOSTS, COL_PREFIX, COL_NET, COL_MASK, COL_GW, COL_DHCP, COL_NOTE = range(9)


class IpModel(QAbstractTableModel):
    COLS = (
        "col.vlan",
        "col.segment",
        "col.hosts",
        "col.prefix",
        "col.network",
        "col.mask",
        "col.gateway",
        "col.dhcp",
        "col.note",
    )

    def __init__(self, view: IpPlanView) -> None:
        super().__init__()
        self.view = view
        self.rows: list[IpSegmentResult] = []

    def set_rows(self, rows: list[IpSegmentResult]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return len(self.COLS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(self.COLS[section])
        return None

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        s = self.rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.EditRole:
            return {COL_VLAN: s.vlan, COL_NAME: s.name, COL_HOSTS: s.hosts, COL_PREFIX: s.prefix}.get(col)
        if role == Qt.ItemDataRole.DisplayRole:
            return [
                s.vlan,
                s.name,
                s.hosts,
                f"/{s.prefix}  ({s.capacity})",
                s.network or "—",
                s.mask or "—",
                s.gateway or "—",
                s.dhcp_range or "—",
                s.note,
            ][col]
        if role == Qt.ItemDataRole.ToolTipRole:
            if col in (COL_VLAN, COL_NAME, COL_PREFIX) or (col == COL_HOSTS and s.custom):
                return tr("ui.ip_edit_tip")
            if col == COL_NOTE:
                return s.note
        if role == Qt.ItemDataRole.ForegroundRole and col == COL_NOTE and s.too_small:
            return QColor(tokens().warning)
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (COL_VLAN, COL_HOSTS, COL_PREFIX):
            return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        s = self.rows[index.row()]
        if index.column() in (COL_VLAN, COL_NAME, COL_PREFIX) or (index.column() == COL_HOSTS and s.custom):
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    def setData(
        self, index: QModelIndex | QPersistentModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        seg = self.rows[index.row()]
        col = index.column()
        text = str(value).strip()
        try:
            if col == COL_VLAN:
                new: Any = int(text)
                if not 1 <= new <= 4094:
                    return False
            elif col == COL_HOSTS:
                new = max(0, int(text))
            elif col == COL_PREFIX:
                new = int(text.lstrip("/"))
                if not 8 <= new <= 30:
                    return False
            else:
                new = text
        except ValueError:
            return False
        self.view.edit_segment(seg, col, new)
        return True


class IpPlanView(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._loading = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(px(12))

        top = Card(tr("ui.ip_settings"), tr("ui.ip_settings_sub"))
        self.loc_id = OptionalIntEdit(0, 255, tr("ui.ip_id_none"))
        self.loc_id.setFixedWidth(px(130))
        self.loc_id.valueChanged.connect(self._on_id)
        self.template_caption = label("", "caption", wrap=True)
        top.add(FieldRow(tr("ui.location_id"), self.loc_id, tr("ui.ip_id_caption"), tr("help.location_id")))
        top.add(self.template_caption)
        self.base = QLineEdit()
        self.base.setPlaceholderText("10.50.0.0/16")
        self.base.setFixedWidth(px(200))
        self.base.textEdited.connect(self._on_base)
        self.base_row = FieldRow(tr("ui.base_net"), self.base, tr("ui.ip_base_caption"), tr("help.base_net"))
        top.add(self.base_row)
        root.addWidget(top)

        seg_card = Card(tr("ui.ip_segments"), tr("ui.ip_segments_caption"))
        assert seg_card.header is not None
        add = button(tr("ui.ip_add"), None, "plus")
        add.clicked.connect(self.add_vlan)
        seg_card.header.addWidget(add, 0, Qt.AlignmentFlag.AlignTop)
        self.reset_btn = button(tr("ui.ip_reset"), "ghost", "refresh-ccw", tr("ui.ip_reset_tip"))
        self.reset_btn.clicked.connect(self.reset_edits)
        seg_card.header.addWidget(self.reset_btn, 0, Qt.AlignmentFlag.AlignTop)
        chips_box = QWidget()
        self.chips_flow = FlowLayout(chips_box, spacing=6)
        self.chips: dict[str, Chip] = {}
        t = current()
        for seg in state.catalog.rules.ip.segments:
            if seg.source == "voice":
                continue
            chip = Chip(t.pick(seg.label), checkable=True)
            chip.toggled.connect(lambda on, sid=seg.id: self._toggle_segment(sid, on))
            self.chips_flow.addWidget(chip)
            self.chips[seg.id] = chip
        seg_card.add(chips_box)
        root.addWidget(seg_card)

        self.error = Callout("error")
        root.addWidget(self.error)
        self.model = IpModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        copy_sc = QShortcut(QKeySequence.StandardKey.Copy, self.table)
        copy_sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        copy_sc.activated.connect(self.copy_selection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Interactive)
        hh.resizeSection(COL_NAME, px(260))
        hh.setSectionResizeMode(COL_NOTE, QHeaderView.ResizeMode.Stretch)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(px(36))
        root.addWidget(self.table, 1)
        self.footer = label("", "caption", wrap=True)
        root.addWidget(self.footer)

        state.resultChanged.connect(when_shown(self, self.on_result))
        state.siteChanged.connect(self._load_inputs)
        state.catalogChanged.connect(self._load_inputs)
        self._load_inputs()
        if state.result:
            self.on_result(state.result)

    # ---- inputs --------------------------------------------------------------------------
    def _load_inputs(self) -> None:
        s = self.state.site
        self._loading = True
        try:
            self.loc_id.setValue(-1 if s.location_id is None else s.location_id)
            if not self.base.hasFocus():
                self.base.setText(s.ip.base_network)
            self.base_row.setVisible(s.location_id is None)
            template = self.state.catalog.rules.ip.id_template
            example = template.format(id=s.location_id if s.location_id is not None else "ID", vlan="VLAN")
            self.template_caption.setText(tr("ui.ip_template", template=template, example=example))
        finally:
            self._loading = False

    def _on_id(self, value: int) -> None:
        if self._loading:
            return
        self.state.set_field("location_id", None if value < 0 else value, tr("ui.location_id"))

    def _on_base(self, text: str) -> None:
        ok = True
        if text.strip():
            try:
                ipaddress.IPv4Network(text.strip(), strict=False)
            except ValueError:
                ok = False
        self.base.setProperty("invalid", "false" if ok else "true")
        self.base.style().unpolish(self.base)
        self.base.style().polish(self.base)
        if ok:

            def mutate(d: dict[str, Any]) -> None:
                d.setdefault("ip", {})["base_network"] = text.strip()

            self.state.edit(tr("ui.base_net"), mutate, merge_key="base_net")

    def _toggle_segment(self, seg_id: str, on: bool) -> None:
        def mutate(d: dict[str, Any]) -> None:
            d.setdefault("ip", {}).setdefault("segments", {})[seg_id] = on

        self.state.edit(tr("ui.ip_segments"), mutate)

    # ---- table edits ---------------------------------------------------------------------
    def edit_segment(self, seg: IpSegmentResult, col: int, value: Any) -> None:
        def mutate(d: dict[str, Any]) -> None:
            ip = d.setdefault("ip", {})
            if col == COL_PREFIX:
                ip.setdefault("prefix_overrides", {})[seg.id] = value
                return
            if seg.custom:
                field = {COL_VLAN: "vlan", COL_NAME: "name", COL_HOSTS: "hosts"}[col]
                for c in ip.setdefault("custom", []):
                    if c["id"] == seg.id:
                        c[field] = value
                return
            if col == COL_VLAN:
                ip.setdefault("vlan_overrides", {})[seg.id] = value
            elif col == COL_NAME:
                names = ip.setdefault("name_overrides", {})
                if value:
                    names[seg.id] = value
                else:
                    names.pop(seg.id, None)

        self.state.edit(tr(IpModel.COLS[col]), mutate)

    def add_vlan(self) -> None:
        used = {
            s.vlan
            for s in (
                self.state.location_result.ip_plan.segments
                if self.state.location_result and self.state.location_result.ip_plan
                else []
            )
        }
        vlan = next(v for v in range(100, 4095) if v not in used)
        new = {
            "id": f"vlan-{uuid.uuid4().hex[:6]}",
            "name": tr("ui.ip_new_vlan"),
            "vlan": vlan,
            "hosts": 20,
            "dhcp": True,
        }
        self.state.edit(tr("ui.ip_add"), lambda d: d.setdefault("ip", {}).setdefault("custom", []).append(new))

    def remove_vlan(self, seg: IpSegmentResult) -> None:
        def mutate(d: dict[str, Any]) -> None:
            ip = d.setdefault("ip", {})
            if seg.custom:
                ip["custom"] = [c for c in ip.get("custom", []) if c["id"] != seg.id]
            else:
                ip.setdefault("segments", {})[seg.id] = False
            ip.get("prefix_overrides", {}).pop(seg.id, None)

        self.state.edit(tr("ui.ip_remove"), mutate)

    def reset_edits(self) -> None:
        def mutate(d: dict[str, Any]) -> None:
            ip = d.setdefault("ip", {})
            for key in ("vlan_overrides", "name_overrides", "prefix_overrides"):
                ip[key] = {}

        self.state.edit(tr("ui.ip_reset"), mutate)

    def _context(self, pos: QPoint) -> None:
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        seg = self.model.rows[idx.row()]
        menu = QMenu(self)
        for col, key in ((COL_VLAN, "ui.ip_edit_vlan"), (COL_NAME, "ui.ip_edit_name"), (COL_PREFIX, "ui.ip_edit_mask")):
            a = QAction(icons.icon("pencil", size=16), tr(key), menu)
            a.triggered.connect(lambda _=False, c=col: self.table.edit(idx.siblingAtColumn(c)))
            menu.addAction(a)
        menu.addSeparator()
        a = QAction(icons.icon("copy", size=16), tr("bom.copy_cell"), menu)
        a.triggered.connect(lambda: self.copy_selection(idx))
        menu.addAction(a)
        a = QAction(icons.icon("trash-2", "error", 16), tr("ui.ip_remove"), menu)
        a.triggered.connect(lambda: self.remove_vlan(seg))
        menu.addAction(a)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def copy_selection(self, fallback: QModelIndex | None = None) -> None:
        """Selected rows to the clipboard as tab-separated text (pastes into Excel as a table)."""
        rows = sorted({i.row() for i in self.table.selectionModel().selectedIndexes()})
        if not rows and fallback is not None and fallback.isValid():
            rows = [fallback.row()]
        cols = [c for c in range(self.model.columnCount()) if not self.table.isColumnHidden(c)]
        lines = [
            "\t".join(str(self.model.index(r, c).data(Qt.ItemDataRole.DisplayRole) or "") for c in cols) for r in rows
        ]
        if not lines:
            return
        if len(rows) == 1 and fallback is not None and fallback.isValid():
            text = str(fallback.data(Qt.ItemDataRole.DisplayRole) or "")
        else:
            text = "\n".join(lines)
        QGuiApplication.clipboard().setText(text)
        self.state.message.emit("success", tr("ui.copied_n", n=len(lines) if len(lines) > 1 else text[:60]))

    # ---- result --------------------------------------------------------------------------
    def on_result(self, result: SiteResult) -> None:
        # one IP plan for the whole location (planned on the firewall floor)
        result = self.state.location_result or result
        plan = result.ip_plan
        if plan is None:
            self.model.set_rows([])
            return
        active = {s.id for s in plan.segments}
        explicit = result.input.ip.segments
        for sid, chip in self.chips.items():
            chip.blockSignals(True)
            chip.setChecked(sid in active)
            chip.blockSignals(False)
            chip.setToolTip(tr("ui.ip_chip_manual") if sid in explicit else tr("ui.ip_chip_auto"))
        self.model.set_rows(plan.segments)
        self.error.setVisible(bool(plan.error))
        self.error.text.setText(plan.error)
        has_addr = plan.has_addresses
        for c in (COL_NET, COL_MASK, COL_GW, COL_DHCP):
            self.table.setColumnHidden(c, not has_addr)
        ip = result.input.ip
        self.reset_btn.setEnabled(bool(ip.vlan_overrides or ip.name_overrides or ip.prefix_overrides))
        if has_addr:
            self.footer.setText(
                tr(
                    "ui.ip_footer",
                    used=plan.used_addresses,
                    base=plan.base_network,
                    reserve=self.state.catalog.rules.ip.static_reserve,
                )
            )
        else:
            self.footer.setText(tr("ui.ip_footer_sizes", pct=round(self.state.catalog.rules.ip.buffer * 100)))
