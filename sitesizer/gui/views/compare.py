"""Scenario comparison: the current location vs. another location or a "what-if" variant."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...core.compare import DiffRow, cost_delta, diff_results
from ...core.models import SiteInput, SiteResult
from ...core.pricing import format_money
from ...core.sizing import size_site
from ...i18n import current, tr
from ..state import AppState
from ..theme import theme_manager, tokens
from ..widgets.controls import Card, FieldRow, SegmentedControl, ToggleRow, label, px


class DiffModel(QAbstractTableModel):
    COLS = ("cmp.col_item", "cmp.col_a", "cmp.col_b", "cmp.col_delta", "cmp.col_cost")

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[DiffRow] = []
        self.currency = "UAH"
        self.only_changes = False
        self._visible: list[DiffRow] = []

    def set_rows(self, rows: list[DiffRow], currency: str, only_changes: bool) -> None:
        self.beginResetModel()
        self.rows, self.currency, self.only_changes = rows, currency, only_changes
        self._visible = [r for r in rows if not only_changes or r.status != "same"]
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._visible)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return len(self.COLS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(self.COLS[section])
        return None

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        r = self._visible[index.row()]
        c = index.column()
        t = tokens()
        if role == Qt.ItemDataRole.DisplayRole:
            if c == 0:
                return f"{r.model}   ·   {r.category}"
            if c == 1:
                return str(r.qty_a) if r.qty_a else "—"
            if c == 2:
                return str(r.qty_b) if r.qty_b else "—"
            if c == 3:
                return "0" if not r.delta else f"{r.delta:+d}"
            if c == 4:
                return "" if r.cost_delta is None else format_money(r.cost_delta, self.currency)
        if role == Qt.ItemDataRole.ForegroundRole:
            if c in (3, 4) or c == 0:
                color = {"added": t.success, "removed": t.error, "changed": t.warning}.get(r.status)
                if color and (c != 0 or r.status in ("added", "removed")):
                    return QColor(color)
            if r.status == "same":
                return QColor(t.text_muted)
        if role == Qt.ItemDataRole.FontRole and c == 3 and r.delta:
            f = theme_manager.font(None, QFont.Weight.DemiBold)
            return f
        if role == Qt.ItemDataRole.TextAlignmentRole and c in (1, 2, 3):
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.TextAlignmentRole and c == 4:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            return tr(f"cmp.status.{r.status}")
        return None


class CompareView(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(px(14))

        top = QHBoxLayout()
        top.setSpacing(px(14))
        self.card_a = Card(tr("cmp.a"), tr("cmp.a_sub"))
        self.a_combo = QComboBox()
        self.a_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        self.card_a.add(self.a_combo)
        self.a_summary = label("", "muted", wrap=True)
        self.card_a.add(self.a_summary)
        self.card_a.body.addStretch(1)
        top.addWidget(self.card_a, 1)

        self.card_b = Card(tr("cmp.b"), tr("cmp.b_sub"))
        self.b_combo = QComboBox()
        self.b_combo.currentIndexChanged.connect(self._on_b_changed)
        self.card_b.add(self.b_combo)
        self.variant = QWidget()
        vg = QGridLayout(self.variant)
        vg.setContentsMargins(0, 0, 0, 0)
        vg.setVerticalSpacing(px(8))
        t = current()
        tiers = [(0, tr("cmp.same"))] + [
            (int(k), f"{k} · {t.pick(v.label)}") for k, v in sorted(state.catalog.tiers.items())
        ]
        self.v_tier = SegmentedControl(tiers, compact=True)
        self.v_tier.valueChanged.connect(lambda _v: self.refresh())
        vg.addWidget(label(tr("ui.tier")), 0, 0)
        vg.addWidget(self.v_tier, 1, 0)
        self.v_reserve = SegmentedControl(
            [("same", tr("cmp.same")), ("on", tr("cmp.on")), ("off", tr("cmp.off"))], compact=True, expand=False
        )
        self.v_reserve.valueChanged.connect(lambda _v: self.refresh())
        vg.addWidget(FieldRow(tr("ui.reserve"), self.v_reserve), 2, 0)
        self.v_psu = SegmentedControl(
            [("same", tr("cmp.same")), ("on", tr("cmp.on")), ("off", tr("cmp.off"))], compact=True, expand=False
        )
        self.v_psu.valueChanged.connect(lambda _v: self.refresh())
        vg.addWidget(FieldRow(tr("ui.psu"), self.v_psu), 3, 0)
        self.v_mode = SegmentedControl(
            [("same", tr("cmp.same")), ("quick", tr("ui.mode_quick")), ("extended", tr("ui.mode_extended"))],
            compact=True,
            expand=False,
        )
        self.v_mode.valueChanged.connect(lambda _v: self.refresh())
        vg.addWidget(FieldRow(tr("ui.mode"), self.v_mode), 4, 0)
        self.card_b.add(self.variant)
        self.b_summary = label("", "muted", wrap=True)
        self.card_b.add(self.b_summary)
        top.addWidget(self.card_b, 1)
        root.addLayout(top)

        bar = QHBoxLayout()
        self.totals = label("", "subtitle")
        bar.addWidget(self.totals, 1)
        self.only_changes = ToggleRow(tr("cmp.only_changes"))
        self.only_changes.setMaximumWidth(px(300))
        self.only_changes.toggled.connect(lambda _v: self.refresh())
        bar.addWidget(self.only_changes)
        root.addLayout(bar)

        self.model = DiffModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            hh.resizeSection(c, px(110))
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setDefaultSectionSize(px(34))
        root.addWidget(self.table, 1)
        root.addWidget(label(tr("cmp.legend"), "caption"))

        state.projectChanged.connect(self._fill_combos)
        state.resultChanged.connect(lambda _r: self.refresh())
        state.siteChanged.connect(self._fill_combos)
        self._fill_combos()
        self._default_variant()
        self.refresh()

    def _fill_combos(self) -> None:
        sites = self.state.project.sites
        for combo, keep_variant in ((self.a_combo, False), (self.b_combo, True)):
            current_id = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            if keep_variant:
                combo.addItem(tr("cmp.variant_of_a"), "__variant__")
            for entry in sites:
                combo.addItem(entry.input.name or tr("ui.untitled"), entry.id)
            idx = combo.findData(current_id)
            if idx < 0:
                idx = combo.findData(self.state.current_id) if combo is self.a_combo else 0
            combo.setCurrentIndex(max(idx, 0))
            combo.blockSignals(False)
        self._on_b_changed()

    def _default_variant(self) -> None:
        """Start with a useful "what if": the neighbouring criticality tier."""
        entry = self.state.project.site(self.a_combo.currentData() or "")
        if entry is not None and self.v_tier.value() == 0:
            self.v_tier.setValue(1 if entry.input.tier != 1 else 2)

    def _on_b_changed(self) -> None:
        self.variant.setVisible(self.b_combo.currentData() == "__variant__")
        self.refresh()

    def _variant_input(self, base: SiteInput) -> SiteInput:
        data = base.model_dump(mode="json")
        tier = self.v_tier.value()
        if tier:
            data["tier"] = tier
        for ctrl, key in ((self.v_reserve, "reserve"), (self.v_psu, "redundant_psu")):
            v = ctrl.value()
            if v != "same":
                data[key] = v == "on"
        mode = self.v_mode.value()
        if mode != "same":
            data["mode"] = mode
        data["name"] = f"{base.name} ({tr('cmp.variant')})"
        return SiteInput.model_validate(data)

    def _summary(self, r: SiteResult) -> str:
        fw = r.firewall
        parts = [
            tr(
                "cmp.summary",
                tier=r.tier_id,
                fw=f"{fw.model} × {fw.count}" if fw else "—",
                sw=r.total_switches,
                ap=r.counts.aps,
                w=round(r.power.total_w),
            )
        ]
        return " · ".join(parts)

    def refresh(self) -> None:
        a_id = self.a_combo.currentData()
        a = self.state.result_for(a_id) if a_id else None
        if a is None:
            return
        b_id = self.b_combo.currentData()
        if b_id == "__variant__":
            entry = self.state.project.site(a_id)
            if entry is None:
                return
            b = size_site(self._variant_input(entry.input), self.state.catalog, lang=self.state.settings.language)
        else:
            b = self.state.result_for(b_id)
        if b is None:
            return
        rows = diff_results(a, b)
        self.model.set_rows(rows, self.state.catalog.meta.currency, self.only_changes.isChecked())
        self.a_summary.setText(self._summary(a))
        self.b_summary.setText(self._summary(b))
        changed = sum(1 for r in rows if r.status != "same")
        delta = cost_delta(rows)
        text = tr("cmp.totals", n=changed)
        if delta is not None:
            text += "  ·  " + tr("cmp.cost", delta=format_money(delta, self.state.catalog.meta.currency))
        self.totals.setText(text)
