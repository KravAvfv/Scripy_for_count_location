"""IP plan view: base network, segment toggles and the carved VLAN table (editable VLAN IDs)."""

from __future__ import annotations

import ipaddress
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...core.models import IpSegmentResult, SiteResult
from ...i18n import current, tr
from ..state import AppState
from ..widgets.controls import Callout, Card, Chip, FieldRow, FlowLayout, button, label, px

HOST_SOURCE_KEYS = {
    "sockets": "ui.sockets",
    "voice": "ui.voice",
    "wifi": "ui.clients",
    "guest": "ui.guest",
    "cameras": "ui.cameras",
    "iot": "ui.iot",
    "mgmt": "ui.mgmt_devices",
}


class IpModel(QAbstractTableModel):
    COLS = (
        "col.segment",
        "col.vlan",
        "col.hosts",
        "col.prefix",
        "col.capacity",
        "col.network",
        "col.gateway",
        "col.dhcp",
    )

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
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
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return [
                s.name,
                s.vlan,
                s.hosts,
                f"/{s.prefix}",
                s.capacity,
                s.network or "—",
                s.gateway or "—",
                s.dhcp_range or "—",
            ][col]
        if role == Qt.ItemDataRole.ToolTipRole and col == 1:
            return tr("ui.vlan_edit_tip")
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (1, 2, 3, 4):
            return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 1:
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    def setData(
        self, index: QModelIndex | QPersistentModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        if index.column() != 1:
            return False
        try:
            vlan = int(value)
        except (TypeError, ValueError):
            return False
        if not 1 <= vlan <= 4094:
            return False
        seg_id = self.rows[index.row()].id

        def mutate(d: dict[str, Any]) -> None:
            d.setdefault("ip", {}).setdefault("vlan_overrides", {})[seg_id] = vlan

        self.state.edit(tr("col.vlan"), mutate)
        return True


class IpPlanView(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        # empty state (quick mode)
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(1)
        card = Card(tr("ui.ip_empty_title"), tr("ui.ip_empty_text"), margins=24)
        card.setMaximumWidth(px(520))
        go = button(tr("ui.switch_extended"), "primary", "sliders-horizontal")
        go.clicked.connect(lambda: state.set_field("mode", "extended", tr("ui.mode"), merge=False))
        card.add(go)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        el.addLayout(row)
        el.addStretch(2)
        self.stack.addWidget(empty)

        # plan
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(px(12))
        top = Card(tr("ui.ip_settings"), tr("ui.ip_settings_sub"))
        self.base = QLineEdit()
        self.base.setPlaceholderText("10.50.0.0/16")
        self.base.setFixedWidth(px(200))
        self.base.textEdited.connect(self._on_base)
        top.add(FieldRow(tr("ui.base_net"), self.base, tr("ui.base_net_caption"), tr("help.base_net")))
        top.add(label(tr("ui.ip_segments").upper(), "section"))
        chips_box = QWidget()
        self.chips_flow = FlowLayout(chips_box, spacing=6)
        self.chips: dict[str, Chip] = {}
        t = current()
        for seg in state.catalog.rules.ip.segments:
            chip = Chip(f"VLAN {seg.vlan} · {t.pick(seg.label)}", checkable=True)
            chip.toggled.connect(lambda on, sid=seg.id: self._toggle_segment(sid, on))
            self.chips_flow.addWidget(chip)
            self.chips[seg.id] = chip
        top.add(chips_box)
        self.chips_caption = label(tr("ui.ip_segments_caption"), "caption", wrap=True)
        top.add(self.chips_caption)
        pl.addWidget(top)
        self.error = Callout("error")
        pl.addWidget(self.error)
        self.model = IpModel(state)
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
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(px(36))
        pl.addWidget(self.table, 1)
        self.footer = label("", "caption", wrap=True)
        pl.addWidget(self.footer)
        self.stack.addWidget(page)

        state.resultChanged.connect(self.on_result)
        state.siteChanged.connect(self._load_inputs)
        self._load_inputs()
        if state.result:
            self.on_result(state.result)

    def _load_inputs(self) -> None:
        s = self.state.site
        if not self.base.hasFocus():
            self.base.setText(s.ip.base_network)

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

    def on_result(self, result: SiteResult) -> None:
        if result.ip_plan is None:
            self.stack.setCurrentIndex(0)
            return
        self.stack.setCurrentIndex(1)
        plan = result.ip_plan
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
        has_addr = bool(plan.base_network) and not plan.error
        for c in (5, 6, 7):
            self.table.setColumnHidden(c, not has_addr)
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
