"""Location view: the input form (left) with a live preview (right)."""

from __future__ import annotations

import ipaddress
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core.models import ADDON_KEYS, SiteResult
from ...core.presets import load_presets
from ...core.sizing import addon_enabled, ap_model_for
from ...exporters.diagram import SiteDiagram, style_from_tokens
from ...i18n import current, tr
from .. import icons
from ..state import AppState
from ..theme import theme_manager, tokens
from ..widgets.controls import (
    Card,
    Chip,
    DoubleSpinBox,
    FieldRow,
    FlowLayout,
    OptionalIntEdit,
    SegmentedControl,
    SpinBox,
    Stepper,
    ToggleRow,
    button,
    clear_layout,
    hline,
    icon_button,
    label,
    px,
)
from ..widgets.diagram_view import DiagramPreview
from ..widgets.feedback import ChecksPanel

FORTIOS_CHOICES = ["7.2.11", "7.4.8", "7.6.0", "7.6.4", "8.0.0"]


class ZoneRow(QFrame):
    """One Wi-Fi zone: placement type, AP model, quantity, optional name."""

    changed = Signal(int, dict)
    removed = Signal(int)

    def __init__(self, index: int, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.index = index
        self.state = state
        self.setObjectName("SoftCard")
        grid = QGridLayout(self)
        grid.setContentsMargins(px(10), px(8), px(8), px(8))
        grid.setHorizontalSpacing(px(8))
        grid.setVerticalSpacing(px(6))
        t = current()
        self.zone = QComboBox()
        self.zone.setAccessibleName(tr("ui.zone_type"))
        for key, zone in state.catalog.ap_zones.items():
            self.zone.addItem(t.pick(zone.label), key)
        self.zone.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.zone.setMinimumWidth(px(150))
        self.zone.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.zone.setMinimumContentsLength(14)
        self.qty = Stepper(0, 10_000, width=118)
        self.qty.setToolTip(tr("ui.zone_qty"))
        self.remove = icon_button("trash-2", tr("ui.zone_remove"), size=16)
        self.model = QComboBox()
        self.model.setAccessibleName(tr("ui.ap_model"))
        self.model.setToolTip(tr("ui.ap_model_tip"))
        self.model.addItem("", None)
        for key, dev in state.catalog.aps().items():
            self.model.addItem(f"{key} — {t.pick(dev.name)}", key)
        self.model.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.model.setMinimumWidth(px(150))
        self.model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model.setMinimumContentsLength(14)
        self.model.view().setMinimumWidth(px(420))
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("ui.zone_name_ph"))
        self.name.setMaximumWidth(px(220))
        grid.addWidget(self.zone, 0, 0)
        grid.addWidget(self.qty, 0, 1)
        grid.addWidget(self.remove, 0, 2)
        grid.addWidget(self.model, 1, 0)
        grid.addWidget(self.name, 1, 1, 1, 2)
        grid.setColumnStretch(0, 1)
        self.zone.currentIndexChanged.connect(self._emit)
        self.model.currentIndexChanged.connect(self._emit)
        self.qty.valueChanged.connect(self._emit_qty)
        self.name.editingFinished.connect(self._emit)
        self.remove.clicked.connect(lambda: self.removed.emit(self.index))
        self._loading = False

    def load(self, group: dict[str, Any]) -> None:
        self._loading = True
        i = self.zone.findData(group.get("zone"))
        self.zone.setCurrentIndex(max(i, 0))
        zone = self.state.catalog.ap_zones.get(group.get("zone", ""))
        auto_model = zone.model if zone else ""
        self.model.setItemText(0, tr("ui.ap_model_auto", model=auto_model))
        j = self.model.findData(group.get("model"))
        self.model.setCurrentIndex(j if j > 0 else 0)
        self.qty.setValue(int(group.get("qty", 0)))
        if not self.name.hasFocus():
            self.name.setText(group.get("name", ""))
        self._loading = False

    def data(self) -> dict[str, Any]:
        return {
            "zone": self.zone.currentData(),
            "model": self.model.currentData(),
            "qty": self.qty.value(),
            "name": self.name.text().strip(),
        }

    def _emit(self) -> None:
        if not self._loading:
            self.changed.emit(self.index, self.data())

    def _emit_qty(self, _v: int) -> None:
        if not self._loading:
            self.changed.emit(self.index, self.data())


class MetricTile(QFrame):
    def __init__(self, title: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SoftCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(12), px(10), px(12), px(10))
        lay.setSpacing(px(2))
        head = QHBoxLayout()
        head.setSpacing(px(6))
        self.icon = QLabel()
        self.icon_name = icon_name
        head.addWidget(self.icon)
        head.addWidget(label(title, "caption"))
        head.addStretch(1)
        lay.addLayout(head)
        self.value = label("—", "metric")
        self.sub = label("", "faint")
        lay.addWidget(self.value)
        lay.addWidget(self.sub)
        self.retint()

    def retint(self) -> None:
        self.icon.setPixmap(icons.pixmap(self.icon_name, px(14), "text_muted"))

    def set(self, value: str, sub: str = "") -> None:
        self.value.setText(value)
        self.sub.setText(sub)


class LocationView(QWidget):
    navigate = Signal(str)

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._loading = False
        self._zone_rows: list[ZoneRow] = []
        self._single_column = False

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(px(20))
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.form = QWidget()
        self.form.setObjectName("FormColumn")
        self.form_lay = QVBoxLayout(self.form)
        self.form_lay.setContentsMargins(px(4), px(4), px(12), px(24))
        self.form_lay.setSpacing(px(16))
        self.form_scroll.setWidget(self.form)
        self.form_scroll.setMinimumWidth(px(430))

        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview = QWidget()
        self.preview_lay = QVBoxLayout(self.preview)
        self.preview_lay.setContentsMargins(px(4), px(4), px(12), px(24))
        self.preview_lay.setSpacing(px(16))
        self.preview_scroll.setWidget(self.preview)

        root.addWidget(self.form_scroll, 11)
        root.addWidget(self.preview_scroll, 10)

        self._build_form()
        self._build_preview()
        self.load_from_site()
        state.siteChanged.connect(self.load_from_site)
        state.resultChanged.connect(self.on_result)
        state.catalogChanged.connect(self._rebuild_zone_rows)
        theme_manager.changed.connect(self._on_theme)

    # =====================================================================================
    # form
    # =====================================================================================
    def _build_form(self) -> None:
        f = self.form_lay
        # --- title + presets ------------------------------------------------------------
        head = QVBoxLayout()
        head.setSpacing(px(6))
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("TitleEdit")
        self.name_edit.setPlaceholderText(tr("ui.site_name_ph"))
        self.name_edit.setAccessibleName(tr("ui.site_name"))
        self.name_edit.textEdited.connect(lambda v: self.state.set_field("name", v, tr("ui.site_name")))
        head.addWidget(self.name_edit)
        presets_row = QHBoxLayout()
        presets_row.setSpacing(px(8))
        presets_row.addWidget(label(tr("ui.presets"), "caption"))
        presets_row.addStretch(1)
        head.addLayout(presets_row)
        self.preset_box = QWidget()
        flow = FlowLayout(self.preset_box, spacing=6)
        icon_map = {
            "branch": "building-2",
            "warehouse": "warehouse",
            "office": "briefcase",
            "printer": "printer",
            "hq": "landmark",
        }
        t = current()
        self.preset_chips: list[Chip] = []
        for p in load_presets():
            chip = Chip(t.pick(p.label), icon_name=icon_map.get(p.icon, "sparkles"))
            chip.setToolTip(t.pick(p.description))
            chip.clicked.connect(lambda _=False, pid=p.id: self.apply_preset(pid))
            flow.addWidget(chip)
            self.preset_chips.append(chip)
        head.addWidget(self.preset_box)
        f.addLayout(head)

        # --- site identity ----------------------------------------------------------------
        ident = Card(tr("ui.identity"), tr("ui.identity_sub"))
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("BO123")
        self.code_edit.setMaxLength(32)
        self.code_edit.setFixedWidth(px(140))
        self.code_edit.textEdited.connect(
            lambda v: self.state.set_field("location_code", v.strip(), tr("ui.location_code"))
        )
        ident.add(
            FieldRow(tr("ui.location_code"), self.code_edit, tr("ui.location_code_caption"), tr("help.location_code"))
        )
        self.loc_id = OptionalIntEdit(0, 255, tr("ui.ip_id_none"))
        self.loc_id.setFixedWidth(px(140))
        self.loc_id.valueChanged.connect(self._on_loc_id)
        ident.add(FieldRow(tr("ui.location_id"), self.loc_id, tr("ui.ip_id_caption"), tr("help.location_id")))
        self.floor_no = Stepper(-10, 300, width=140)
        self.floor_no.valueChanged.connect(self._on_floor_no)
        ident.add(FieldRow(tr("ui.floor_no"), self.floor_no, tr("ui.floor_no_caption"), tr("help.floor_no")))
        self.fw_here = ToggleRow(tr("ui.fw_here"), tr("ui.fw_here_caption"))
        self.fw_here.toggled.connect(self._on_fw_here)
        ident.add(self.fw_here)
        f.addWidget(ident)
        self.ident_card = ident

        # --- endpoints ------------------------------------------------------------------
        ep = Card(tr("ui.endpoints"), tr("ui.endpoints_sub"))
        self.sockets = Stepper(0, 1_000_000, width=140)
        self.sockets.valueChanged.connect(lambda v: self.state.set_field("sockets", v, tr("ui.sockets")))
        self.sockets_row = FieldRow(tr("ui.sockets"), self.sockets, "", tr("help.sockets"))
        self.cameras = Stepper(0, 1_000_000, width=140)
        self.cameras.valueChanged.connect(lambda v: self.state.set_field("cameras", v, tr("ui.cameras")))
        self.cameras_row = FieldRow(tr("ui.cameras"), self.cameras, "", tr("help.cameras"))
        ep.add(self.sockets_row)
        ep.add(self.cameras_row)
        f.addWidget(ep)

        # --- Wi-Fi zones ----------------------------------------------------------------
        self.wifi_card = Card(tr("ui.wifi"), tr("ui.wifi_sub"))
        add_zone = button(tr("ui.zone_add"), "ghost", "plus")
        add_zone.clicked.connect(self.add_zone)
        assert self.wifi_card.header is not None
        self.wifi_card.header.addWidget(add_zone, 0, Qt.AlignmentFlag.AlignTop)
        self.zones_box = QVBoxLayout()
        self.zones_box.setSpacing(px(8))
        self.wifi_card.add(self.zones_box)
        self.zones_empty = label(tr("ui.zones_empty"), "caption", wrap=True)
        self.wifi_card.add(self.zones_empty)
        self.zones_summary = label("", "caption", wrap=True)
        self.wifi_card.add(self.zones_summary)
        f.addWidget(self.wifi_card)

        # --- criticality ------------------------------------------------------------------
        tier_card = Card(tr("ui.tier"), tr("ui.tier_sub"))
        cat = self.state.catalog
        opts = [(int(k), f"{k} · {t.pick(v.label)}") for k, v in sorted(cat.tiers.items())]
        tips = [t.pick(v.description) for _, v in sorted(cat.tiers.items())]
        self.tier = SegmentedControl(opts, tips, compact=True)
        self.tier.setAccessibleName(tr("ui.tier"))
        self.tier.valueChanged.connect(lambda v: self.state.set_field("tier", v, tr("ui.tier"), merge=False))
        tier_card.add(self.tier)
        self.tier_desc = label("", "muted", wrap=True)
        tier_card.add(self.tier_desc)
        self.tier_effects = QGridLayout()
        self.tier_effects.setHorizontalSpacing(px(16))
        self.tier_effects.setVerticalSpacing(px(6))
        tier_card.add(self.tier_effects)
        tier_card.add(hline())
        self.psu = ToggleRow(tr("ui.psu"), tr("ui.psu_caption"), tr("help.psu"))
        self.psu.toggled.connect(lambda v: self.state.set_field("redundant_psu", v, tr("ui.psu"), merge=False))
        tier_card.add(self.psu)
        self.psu_confirm = QFrame()
        self.psu_confirm.setProperty("callout", "info")
        pc = QHBoxLayout(self.psu_confirm)
        pc.setContentsMargins(px(12), px(8), px(10), px(8))
        pc.setSpacing(px(8))
        self.psu_confirm_text = label("", wrap=True)
        pc.addWidget(self.psu_confirm_text, 1)
        yes = button(tr("ui.psu_confirm_yes"), "primary")
        no = button(tr("ui.psu_confirm_no"))
        yes.clicked.connect(lambda: self.state.set_field("redundant_psu", True, tr("ui.psu"), merge=False))
        no.clicked.connect(lambda: self.state.set_field("redundant_psu", False, tr("ui.psu"), merge=False))
        pc.addWidget(yes)
        pc.addWidget(no)
        tier_card.add(self.psu_confirm)
        f.addWidget(tier_card)
        self.tier_card = tier_card

        # --- architecture ----------------------------------------------------------------
        arch = Card(tr("ui.architecture"), tr("ui.architecture_sub"))
        self.agg = SegmentedControl(
            [("auto", tr("ui.agg_auto")), ("yes", tr("ui.agg_yes")), ("no", tr("ui.agg_no"))],
            [tr("ui.agg_auto_tip"), tr("ui.agg_yes_tip"), tr("ui.agg_no_tip")],
            expand=False,
            compact=True,
        )
        self.agg.valueChanged.connect(lambda v: self.state.set_field("aggregation", v, tr("ui.agg"), merge=False))
        self.agg_row = FieldRow(tr("ui.agg"), self.agg, "", tr("help.agg"))
        arch.add(self.agg_row)
        arch.add(hline())
        self.reserve = ToggleRow(tr("ui.reserve"), tr("ui.reserve_caption", pct=20), tr("help.reserve"))
        self.reserve.toggled.connect(lambda v: self.state.set_field("reserve", v, tr("ui.reserve"), merge=False))
        arch.add(self.reserve)
        self.reserve_pct = SpinBox()
        self.reserve_pct.setRange(1, 300)
        self.reserve_pct.setSuffix(" %")
        self.reserve_pct.setFixedWidth(px(96))
        self.reserve_pct.valueChanged.connect(
            lambda v: self.state.set_field("reserve_percent", float(v), tr("ui.reserve_pct"))
        )
        self.reserve_pct_row = FieldRow(tr("ui.reserve_pct"), self.reserve_pct)
        arch.add(self.reserve_pct_row)
        arch.add(hline())
        self.rack_size = SegmentedControl(
            [("0", tr("ui.rack_size_auto")), ("24", "24U"), ("42", "42U")],
            [tr("ui.rack_size_tip"), "", ""],
            expand=False,
            compact=True,
        )
        self.rack_size.valueChanged.connect(
            lambda v: self.state.set_field("rack_size_u", int(v), tr("ui.rack_size"), merge=False)
        )
        arch.add(FieldRow(tr("ui.rack_size"), self.rack_size, "", tr("help.rack_size")))
        self.closets = Stepper(0, 50, width=140)
        self.closets.field.setSpecialValueText(tr("ui.rack_size_auto"))
        self.closets.valueChanged.connect(lambda v: self.state.set_field("closets", int(v), tr("rk.closets")))
        arch.add(FieldRow(tr("rk.closets"), self.closets, tr("rk.closets_caption"), tr("rk.closets_tip")))
        f.addWidget(arch)

        # --- mode -----------------------------------------------------------------------
        mode_card = Card(tr("ui.mode"), tr("ui.mode_sub"))
        self.mode = SegmentedControl(
            [("quick", tr("ui.mode_quick")), ("extended", tr("ui.mode_extended"))],
            [tr("ui.mode_quick_tip"), tr("ui.mode_extended_tip")],
        )
        self.mode.setAccessibleName(tr("ui.mode"))
        self.mode.valueChanged.connect(lambda v: self.state.set_field("mode", v, tr("ui.mode"), merge=False))
        self.mode_caption = label("", "caption", wrap=True)
        mode_card.add(self.mode)
        mode_card.add(self.mode_caption)
        f.addWidget(mode_card)

        # --- extended ---------------------------------------------------------------------
        self.ext_card = Card(tr("ui.extended"), tr("ui.extended_sub"))
        e = self.ext_card
        self.clients = Stepper(0, 1_000_000, width=140)
        self.clients.valueChanged.connect(
            lambda v: self.state.set_field("wifi_clients_expected", v or None, tr("ui.clients"))
        )
        e.add(FieldRow(tr("ui.clients"), self.clients, tr("ui.clients_caption"), tr("help.clients")))
        self.guest = Stepper(0, 100_000, width=140)
        self.guest.valueChanged.connect(lambda v: self.state.set_field("guest_clients", v, tr("ui.guest")))
        e.add(FieldRow(tr("ui.guest"), self.guest, "", tr("help.guest")))
        self.iot = Stepper(0, 100_000, width=140)
        self.iot.valueChanged.connect(lambda v: self.state.set_field("iot_devices", v, tr("ui.iot")))
        e.add(FieldRow(tr("ui.iot"), self.iot, "", tr("help.iot")))
        e.add(hline())
        self.cam_w = DoubleSpinBox()
        self.cam_w.setRange(1, 90)
        self.cam_w.setDecimals(1)
        self.cam_w.setSingleStep(0.5)
        self.cam_w.setSuffix(tr("ui.unit_w"))
        self.cam_w.setFixedWidth(px(110))
        self.cam_w.valueChanged.connect(lambda v: self.state.set_field("camera_watts", float(v), tr("ui.cam_w")))
        e.add(FieldRow(tr("ui.cam_w"), self.cam_w, tr("ui.cam_w_caption"), tr("help.cam_w")))
        self.inspected = Stepper(0, 1_000_000, step=50, width=140)
        self.inspected.valueChanged.connect(
            lambda v: self.state.set_field("inspected_mbps", v or None, tr("ui.inspected"))
        )
        e.add(FieldRow(tr("ui.inspected"), self.inspected, tr("ui.inspected_caption"), tr("help.inspected")))
        self.fortios = QComboBox()
        self.fortios.setEditable(True)
        self.fortios.addItems(FORTIOS_CHOICES)
        self.fortios.setFixedWidth(px(120))
        self.fortios.currentTextChanged.connect(
            lambda v: self.state.set_field("fortios_version", v.strip() or None, tr("ui.fortios"))
        )
        e.add(FieldRow(tr("ui.fortios"), self.fortios, tr("ui.fortios_caption"), tr("help.fortios")))
        e.add(hline())
        self.max_run = Stepper(0, 10_000, step=10, suffix=tr("ui.unit_m"), width=140)
        self.max_run.valueChanged.connect(
            lambda v: self.state.set_field("max_cable_run_m", v or None, tr("ui.max_run"))
        )
        e.add(FieldRow(tr("ui.max_run"), self.max_run, tr("ui.max_run_caption"), tr("help.max_run")))
        self.avg_run = Stepper(1, 1000, step=5, suffix=tr("ui.unit_m"), width=140)
        self.avg_run.valueChanged.connect(lambda v: self.state.set_field("avg_cable_run_m", v, tr("ui.avg_run")))
        e.add(FieldRow(tr("ui.avg_run"), self.avg_run, "", tr("help.avg_run")))
        self.fiber = SegmentedControl(
            [("auto", tr("ui.fiber_auto")), ("om4", "OM4"), ("os2", "OS2")],
            ["", "", ""],
            expand=False,
            compact=True,
        )
        self.fiber.valueChanged.connect(
            lambda v: self.state.set_field("fiber_type", v, tr("ui.fiber_type"), merge=False)
        )
        e.add(FieldRow(tr("ui.fiber_type"), self.fiber, tr("ui.fiber_caption"), tr("help.fiber")))
        self.backbone = Stepper(0, 100_000, step=10, suffix=tr("ui.unit_m"), width=140)
        self.backbone.valueChanged.connect(
            lambda v: self.state.set_field("fiber_backbone_m", v or None, tr("ui.fiber_backbone"))
        )
        e.add(FieldRow(tr("ui.fiber_backbone"), self.backbone, tr("ui.fiber_backbone_caption"), tr("help.fiber")))
        e.add(hline())
        self.base_net = QLineEdit()
        self.base_net.setPlaceholderText("10.50.0.0/16")
        self.base_net.setFixedWidth(px(170))
        self.base_net.textEdited.connect(self._on_base_net)
        e.add(FieldRow(tr("ui.base_net"), self.base_net, tr("ui.base_net_caption"), tr("help.base_net")))
        f.addWidget(self.ext_card)

        # --- add-ons ----------------------------------------------------------------------
        self.addons_card = Card(tr("ui.addons"), tr("ui.addons_sub"))
        reset = button(tr("ui.addons_reset"), "ghost", "refresh-ccw")
        reset.clicked.connect(lambda: self.state.edit(tr("ui.addons_reset"), lambda d: d.update(addons={})))
        assert self.addons_card.header is not None
        self.addons_card.header.addWidget(reset, 0, Qt.AlignmentFlag.AlignTop)
        self.addon_rows: dict[str, ToggleRow] = {}
        for key in ADDON_KEYS:
            row = ToggleRow(tr(f"addon.{key}"), tr(f"addon.{key}.caption"))
            row.toggled.connect(lambda v, k=key: self._set_addon(k, v))
            self.addons_card.add(row)
            self.addon_rows[key] = row
        f.addWidget(self.addons_card)

        self.quick_hint = Card(soft=True, margins=14)
        qh = QHBoxLayout()
        qh.addWidget(label(tr("ui.quick_hint"), "muted", wrap=True), 1)
        go = button(tr("ui.switch_extended"), None, "sliders-horizontal")
        go.clicked.connect(lambda: self.state.set_field("mode", "extended", tr("ui.mode"), merge=False))
        qh.addWidget(go, 0, Qt.AlignmentFlag.AlignVCenter)
        self.quick_hint.add(qh)
        f.addWidget(self.quick_hint)

        notes_card = Card(tr("ui.notes"), tr("ui.notes_sub"))
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText(tr("ui.notes_ph"))
        self.notes.setFixedHeight(px(80))
        self.notes.textChanged.connect(self._on_notes)
        notes_card.add(self.notes)
        f.addWidget(notes_card)
        f.addStretch(1)

    # =====================================================================================
    # preview
    # =====================================================================================
    def _build_preview(self) -> None:
        p = self.preview_lay
        res = Card(tr("ui.result"), tr("ui.result_sub"))
        grid = QGridLayout()
        grid.setSpacing(px(10))
        self.m_fw = MetricTile(tr("ui.m_firewall"), "shield")
        self.m_sw = MetricTile(tr("ui.m_switches"), "network")
        self.m_ap = MetricTile(tr("ui.m_aps"), "wifi")
        self.m_poe = MetricTile(tr("ui.m_poe"), "plug")
        self.m_power = MetricTile(tr("ui.m_power"), "zap")
        self.m_rack = MetricTile(tr("ui.m_rack"), "server")
        tiles = [self.m_fw, self.m_sw, self.m_ap, self.m_poe, self.m_power, self.m_rack]
        for i, tile in enumerate(tiles):
            grid.addWidget(tile, i // 3, i % 3)
        res.add(grid)
        self.cat_list = QVBoxLayout()
        self.cat_list.setSpacing(px(4))
        res.add(self.cat_list)
        open_bom = button(tr("ui.open_bom"), "ghost", "arrow-right")
        open_bom.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        open_bom.clicked.connect(lambda: self.navigate.emit("bom"))
        res.add(open_bom)
        p.addWidget(res)

        dia = Card(tr("ui.diagram"), tr("ui.diagram_sub"))
        self.mini = DiagramPreview(max_height=px(380))
        self.mini.setToolTip(tr("ui.diagram_open"))
        self.mini.clicked.connect(lambda: self.navigate.emit("topology"))
        dia.add(self.mini)
        p.addWidget(dia)

        checks = Card(tr("ui.checks"), tr("ui.checks_sub"))
        self.checks = ChecksPanel()
        self.checks.action.connect(self._on_check_action)
        checks.add(self.checks)
        p.addWidget(checks)
        p.addStretch(1)

    # =====================================================================================
    # state → widgets
    # =====================================================================================
    def _on_floor_no(self, value: int) -> None:
        if self._loading:
            return
        old = self.state.site.floor

        def mutate(d: dict[str, Any]) -> None:
            d["floor"] = int(value)
            if d.get("name") in ("", tr("ui.floor_name", n=old)):
                d["name"] = tr("ui.floor_name", n=int(value))

        self.state.edit(tr("ui.floor_no"), mutate, merge_key="floor")

    def _on_fw_here(self, on: bool) -> None:
        if not self._loading and on:
            self.state.set_hub(self.state.current_id)

    def load_from_site(self) -> None:
        s = self.state.site
        cat = self.state.catalog
        self._loading = True
        try:
            if not self.name_edit.hasFocus():
                self.name_edit.setText(s.name)
            if not self.code_edit.hasFocus():
                self.code_edit.setText(s.location_code)
            self.loc_id.blockSignals(True)
            self.loc_id.setValue(-1 if s.location_id is None else s.location_id)
            self.loc_id.blockSignals(False)
            self.floor_no.setValue(s.floor)
            entry = self.state.project.site(self.state.current_id)
            is_fw = bool(entry and entry.is_hub) or len(self.state.project.sites) <= 1
            self.fw_here.switch.blockSignals(True)
            self.fw_here.switch.setChecked(is_fw)
            self.fw_here.switch.blockSignals(False)
            self.fw_here.setEnabled(len(self.state.project.sites) > 1 and not is_fw)
            self.mode.setValue(s.mode, animate=True)
            self.mode_caption.setText(tr("ui.mode_quick_tip") if s.mode == "quick" else tr("ui.mode_extended_tip"))
            self.sockets.setValue(s.sockets)
            self.cameras.setValue(s.cameras)
            self._sync_zones([g.model_dump() for g in s.ap_groups])
            self.tier.setValue(s.tier, animate=True)
            tier = cat.tier(s.tier)
            t = current()
            self.tier_desc.setText(f"{t.pick(tier.description)}. {tr('ui.sla')}: {t.pick(tier.sla)}")
            self._fill_tier_effects()
            suggested = tier.dual_psu
            effective = s.redundant_psu if s.redundant_psu is not None else suggested
            self.psu.setChecked(bool(effective))
            self.psu.set_badge(tr("ui.recommended") if suggested else "")
            needs = cat.rules.variant_mode == "dual_psu" and s.redundant_psu is None and suggested
            self.psu_confirm.setVisible(needs)
            self.psu_confirm_text.setText(tr("ui.psu_confirm_text", tier=t.pick(tier.label)))
            self.psu.setEnabled(cat.rules.variant_mode == "dual_psu")
            self.agg.setValue(s.aggregation, animate=True)
            self.reserve.setChecked(s.reserve)
            pct = s.reserve_percent if s.reserve_percent is not None else cat.rules.reserve_percent_default
            self.reserve.caption.setText(tr("ui.reserve_caption", pct=f"{pct:g}"))
            self.reserve_pct.blockSignals(True)
            self.reserve_pct.setValue(round(pct))
            self.reserve_pct.blockSignals(False)
            self.reserve_pct_row.setVisible(s.reserve)
            extended = s.mode == "extended"
            self.ext_card.setVisible(extended)
            self.addons_card.setVisible(extended)
            self.quick_hint.setVisible(not extended)
            self.clients.setValue(s.wifi_clients_expected or 0)
            self.guest.setValue(s.guest_clients)
            self.iot.setValue(s.iot_devices)
            self.cam_w.blockSignals(True)
            self.cam_w.setValue(s.camera_watts if s.camera_watts is not None else cat.rules.camera_watts_default)
            self.cam_w.blockSignals(False)
            self.inspected.setValue(s.inspected_mbps or 0)
            self.fortios.blockSignals(True)
            if not self.fortios.lineEdit().hasFocus():
                self.fortios.setCurrentText(s.fortios_version or cat.rules.fortios_default)
            self.fortios.blockSignals(False)
            self.max_run.setValue(s.max_cable_run_m or 0)
            self.avg_run.setValue(s.avg_cable_run_m or cat.rules.avg_cable_run_m_default)
            self.rack_size.setValue(str(s.rack_size_u if s.rack_size_u in (24, 42) else 0), animate=True)
            self.closets.setValue(s.closets)
            self.fiber.setValue(s.fiber_type, animate=True)
            self.backbone.setValue(s.fiber_backbone_m or 0)
            if not self.base_net.hasFocus():
                self.base_net.setText(s.ip.base_network)
                self._validate_net(s.ip.base_network)
            for key, row in self.addon_rows.items():
                row.setChecked(addon_enabled(s, key, tier))
                explicit = s.addons.get(key)
                row.set_badge("" if explicit is None else tr("ui.addon_manual"))
            if not self.notes.hasFocus() and self.notes.toPlainText() != s.notes:
                self.notes.blockSignals(True)
                self.notes.setPlainText(s.notes)
                self.notes.blockSignals(False)
        finally:
            self._loading = False

    def _fill_tier_effects(self) -> None:
        clear_layout(self.tier_effects)
        tier = self.state.catalog.tier(self.state.site.tier)
        t = current()
        effects = [
            (tier.fw_ha, tr("effect.fw_ha")),
            (tier.core_redundant, tr("effect.core")),
            (tier.ups, tr("effect.ups")),
            (tier.oob, tr("effect.oob")),
            (tier.dual_psu, tr("effect.psu")),
            (tier.dual_uplinks, tr("effect.uplinks")),
            (tier.dual_wan, tr("effect.dual_wan")),
            (tier.spare_percent > 0, tr("effect.spares", pct=f"{tier.spare_percent:g}")),
            (tier.fortiguard != "none", tr("effect.bundle", bundle=t.t(f"bundle.{tier.fortiguard}"))),
            (tier.forticare != "none", tr("effect.support", level=t.t(f"forticare.{tier.forticare}"))),
        ]
        tk = tokens()
        for i, (on, text) in enumerate(effects):
            row = QHBoxLayout()
            row.setSpacing(px(6))
            ic = QLabel()
            ic.setPixmap(icons.pixmap("check" if on else "minus", px(14), tk.success if on else tk.text_faint))
            lb = label(text, None if on else "faint")
            if not on:
                lb.setStyleSheet(f"color: {tk.text_faint};")
            w = QWidget()
            wl = QHBoxLayout(w)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(px(6))
            wl.addWidget(ic)
            wl.addWidget(lb, 1)
            self.tier_effects.addWidget(w, i // 2, i % 2)
            del row

    def _sync_zones(self, groups: list[dict[str, Any]]) -> None:
        while len(self._zone_rows) > len(groups):
            row = self._zone_rows.pop()
            row.deleteLater()
        while len(self._zone_rows) < len(groups):
            row = ZoneRow(len(self._zone_rows), self.state)
            row.changed.connect(self._on_zone_changed)
            row.removed.connect(self._on_zone_removed)
            self.zones_box.addWidget(row)
            self._zone_rows.append(row)
        for row, g in zip(self._zone_rows, groups, strict=True):
            row.load(g)
        self.zones_empty.setVisible(not groups)
        total = sum(int(g.get("qty", 0)) for g in groups)
        bt = 0
        for group in self.state.site.ap_groups:
            dev = self.state.catalog.models.get(ap_model_for(group, self.state.catalog))
            if dev and dev.ap and dev.ap.poe_class == "bt":
                bt += group.qty
        self.zones_summary.setVisible(bool(groups))
        self.zones_summary.setText(tr("ui.zones_summary", total=total, bt=bt))

    def _rebuild_zone_rows(self) -> None:
        for row in self._zone_rows:
            row.deleteLater()
        self._zone_rows.clear()
        self.load_from_site()

    # =====================================================================================
    # widgets → state
    # =====================================================================================
    def add_zone(self) -> None:
        zone = "low_density"
        if self.state.site.ap_groups:
            zone = self.state.site.ap_groups[-1].zone
        self.state.edit(tr("ui.zone_add"), lambda d: d["ap_groups"].append({"zone": zone, "qty": 1}))
        if self._zone_rows:
            self._zone_rows[-1].qty.setFocus()
            self._zone_rows[-1].qty.field.selectAll()

    def _on_zone_changed(self, index: int, data: dict[str, Any]) -> None:
        if self._loading:
            return

        def mutate(d: dict[str, Any]) -> None:
            if index < len(d["ap_groups"]):
                g = d["ap_groups"][index]
                if g.get("zone") != data["zone"]:
                    data["model"] = None  # new zone → its default model
                g.update(data)

        self.state.edit(tr("ui.wifi"), mutate, merge_key=f"zone{index}")

    def _on_zone_removed(self, index: int) -> None:
        self.state.edit(tr("ui.zone_remove"), lambda d: d["ap_groups"].pop(index))

    def _set_addon(self, key: str, value: bool) -> None:
        if self._loading:
            return

        def mutate(d: dict[str, Any]) -> None:
            d.setdefault("addons", {})[key] = value

        self.state.edit(tr(f"addon.{key}"), mutate)

    def _validate_net(self, text: str) -> bool:
        ok = True
        if text.strip():
            try:
                ipaddress.IPv4Network(text.strip(), strict=False)
            except ValueError:
                ok = False
        self.base_net.setProperty("invalid", "false" if ok else "true")
        self.base_net.style().unpolish(self.base_net)
        self.base_net.style().polish(self.base_net)
        self.base_net.setToolTip("" if ok else tr("ip.bad_network"))
        return ok

    def _on_base_net(self, text: str) -> None:
        if self._validate_net(text):

            def mutate(d: dict[str, Any]) -> None:
                d.setdefault("ip", {})["base_network"] = text.strip()

            self.state.edit(tr("ui.base_net"), mutate, merge_key="base_net")

    def _on_loc_id(self, value: int) -> None:
        if not self._loading:
            self.state.set_field("location_id", None if value < 0 else value, tr("ui.location_id"))

    def _on_notes(self) -> None:
        if not self._loading:
            self.state.set_field("notes", self.notes.toPlainText(), tr("ui.notes"))

    def apply_preset(self, preset_id: str) -> None:
        p = next((x for x in load_presets() if x.id == preset_id), None)
        if p is None:
            return
        current_name = self.state.site.name
        new = p.build()
        default_names = {"Локація", "Location"} | {f"Локація {i}" for i in range(1, 50)}
        new.name = current_name if current_name not in default_names else current().pick(p.label)
        new.fortios_version = self.state.site.fortios_version
        self.state.replace_site(new, tr("ui.preset_applied", name=current().pick(p.label)))

    def _on_check_action(self, action: str, choice: str) -> None:
        if action == "confirm_psu":
            self.state.set_field("redundant_psu", choice == "yes", tr("ui.psu"), merge=False)

    # =====================================================================================
    # result
    # =====================================================================================
    def on_result(self, result: SiteResult) -> None:
        t = current()
        fw = result.firewall
        if fw:
            self.m_fw.set(
                f"{fw.model if fw.fits else '—'}", tr("ui.m_fw_sub", n=fw.count) if fw.fits else tr("ui.m_fw_none")
            )
        elif (elsewhere := self.state.firewall_elsewhere()) is not None:
            self.m_fw.set(elsewhere[0], tr("ui.m_fw_on", floor=elsewhere[1]))
        else:
            self.m_fw.set("—", tr("ui.m_none"))
        self.m_sw.set(
            str(result.total_switches), tr("ui.m_sw_sub", edge=result.edge_switch_count, core=result.core.count)
        )
        self.m_ap.set(str(result.counts.aps), tr("ui.m_ap_sub", n=len(result.counts.ap_groups)))
        poe = result.power.poe_w
        self.m_poe.set(f"{poe:,.0f}".replace(",", " ") + tr("ui.unit_w"), tr("ui.m_poe_sub"))
        self.m_power.set(
            f"{result.power.total_w:,.0f}".replace(",", " ") + tr("ui.unit_w"),
            tr("ui.m_power_sub", va=result.power.ups_va),
        )
        self.m_rack.set(f"{result.rack.units_with_spare} U", tr("ui.m_rack_sub", used=result.rack.units_total))

        clear_layout(self.cat_list)
        tk = tokens()
        for key, c in [*result.categories.items(), ("core_switch", result.core)]:
            if not c.count:
                continue
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, px(2), 0, px(2))
            rl.setSpacing(px(8))
            dot = QLabel()
            dot.setFixedSize(px(8), px(8))
            dot.setStyleSheet(f"background: {tk.categories.get(key, tk.text_faint)}; border-radius: {px(4)}px;")
            rl.addWidget(dot)
            rl.addWidget(label(t.pick(self.state.catalog.categories[key].label), "muted"), 1)
            tags = []
            if c.premium:
                tags.append(tr("ui.tag_psu"))
            if c.upgraded_for_bt:
                tags.append("802.3bt")
            if tags:
                rl.addWidget(label(" · ".join(tags), "faint"))
            m = label(f"{c.model} × {c.count}")
            m.setStyleSheet("font-weight: 600;")
            rl.addWidget(m)
            self.cat_list.addWidget(row)

        self.mini.set_diagram(
            SiteDiagram(result, self.state.catalog, self.state.settings.language, style_from_tokens(tokens()))
        )
        self.checks.set_checks(result.checks)

    def _on_theme(self) -> None:
        for tile in (self.m_fw, self.m_sw, self.m_ap, self.m_poe, self.m_power, self.m_rack):
            tile.retint()
        if self.state.result:
            self.on_result(self.state.result)
        self._fill_tier_effects()

    # =====================================================================================
    # responsive: single column below ~1000 px
    # =====================================================================================
    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        single = self.width() < px(980)
        if single != self._single_column:
            self._single_column = single
            lay = self.layout()
            assert isinstance(lay, QHBoxLayout)
            if single:
                self.preview_scroll.takeWidget()
                self.preview_scroll.hide()
                self.form_lay.insertWidget(1, self.preview)
            else:
                self.form_lay.removeWidget(self.preview)
                self.preview_scroll.setWidget(self.preview)
                self.preview_scroll.show()
            self.preview.show()

    def sizeHint(self) -> QSize:
        return QSize(px(1100), px(800))

    def focus_first(self) -> None:
        self.sockets.setFocus()
        self.sockets.field.selectAll()
