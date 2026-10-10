"""Power & rack view: power/UPS summary, PoE meters, uplinks, passive infrastructure and the rack layout."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ...core.models import SiteResult
from ...i18n import current, tr
from ..lazy import when_shown
from ..state import AppState
from ..theme import theme_manager, tokens
from ..widgets.controls import Card, clear_layout, hline, label, px


class Meter(QWidget):
    """Horizontal bar: value / budget with warn/error colouring."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.value = 0.0
        self.budget = 1.0
        self.warn = 0.8
        self.setFixedHeight(px(8))
        theme_manager.changed.connect(self.update)

    def set(self, value: float, budget: float, warn: float) -> None:
        self.value, self.budget, self.warn = value, max(budget, 1e-9), warn
        self.update()

    def paintEvent(self, _e: QPaintEvent) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.q("surface_alt"))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        ratio = self.value / self.budget
        color = t.success if ratio <= self.warn else (t.warning if ratio <= 1 else t.error)
        p.setBrush(QColor(color))
        w = r.width() * min(ratio, 1.0)
        if w > 0:
            p.drawRoundedRect(QRectF(r.left(), r.top(), max(w, r.height()), r.height()), r.height() / 2, r.height() / 2)
        p.end()


class PowerView(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        page = QVBoxLayout(body)
        page.setContentsMargins(px(4), px(4), px(12), px(24))
        page.setSpacing(px(16))
        root = QHBoxLayout()
        root.setSpacing(px(16))
        page.addLayout(root)
        left = QVBoxLayout()
        left.setSpacing(px(16))
        right = QVBoxLayout()
        right.setSpacing(px(16))
        root.addLayout(left, 3)
        root.addLayout(right, 2)

        self.power_card = Card(tr("ui.power_title"), tr("ui.power_sub"))
        self.power_grid = QGridLayout()
        self.power_grid.setHorizontalSpacing(px(24))
        self.power_grid.setVerticalSpacing(px(8))
        self.power_card.add(self.power_grid)
        self.power_note = label("", "caption", wrap=True)
        self.power_card.add(self.power_note)
        left.addWidget(self.power_card)

        self.poe_card = Card(tr("ui.poe_title"), tr("ui.poe_sub"))
        self.poe_box = QVBoxLayout()
        self.poe_box.setSpacing(px(12))
        self.poe_card.add(self.poe_box)
        left.addWidget(self.poe_card)
        left.addStretch(1)

        self.uplink_card = Card(tr("ui.uplink_title"), tr("ui.uplink_sub"))
        self.uplink_grid = QGridLayout()
        self.uplink_grid.setHorizontalSpacing(px(16))
        self.uplink_grid.setVerticalSpacing(px(6))
        self.uplink_card.add(self.uplink_grid)
        right.addWidget(self.uplink_card)

        self.passive_card = Card(tr("ui.passive_title"), tr("ui.passive_sub"))
        self.passive_text = label("", "muted", wrap=True)
        self.passive_card.add(self.passive_text)
        right.addWidget(self.passive_card)
        right.addStretch(1)

        page.addStretch(1)

        state.resultChanged.connect(when_shown(self, self.on_result))
        if state.result:
            self.on_result(state.result)

    @staticmethod
    def _clear(lay: QGridLayout | QVBoxLayout) -> None:
        clear_layout(lay)

    def on_result(self, r: SiteResult) -> None:
        t = current()
        cat = self.state.catalog
        rules = cat.rules
        pw = r.power
        self._clear(self.power_grid)
        rows = [
            (tr("ui.pw_equipment"), f"{pw.equipment_w:,.0f}".replace(",", " ") + tr("ui.unit_w")),
            (tr("ui.pw_poe"), f"{pw.poe_w:,.0f}".replace(",", " ") + tr("ui.unit_w")),
            (tr("ui.pw_total"), f"{pw.total_w:,.0f}".replace(",", " ") + tr("ui.unit_w")),
            (tr("ui.pw_heat"), f"{pw.heat_btu:,.0f}".replace(",", " ") + " BTU/h"),
            (tr("ui.pw_ups"), f"≥ {pw.ups_va} VA → {pw.ups_model or '—'}"),
        ]
        for i, (k, v) in enumerate(rows):
            self.power_grid.addWidget(label(k, "muted"), i, 0)
            val = label(v)
            val.setStyleSheet("font-weight: 600;")
            self.power_grid.addWidget(val, i, 1)
        for i, (model, qty, w) in enumerate(pw.breakdown):
            self.power_grid.addWidget(label(f"{model} × {qty}", "caption"), i, 2)
            self.power_grid.addWidget(label(f"{w:,.0f}".replace(",", " ") + tr("ui.unit_w"), "caption"), i, 3)
        self.power_grid.setColumnStretch(4, 1)
        self.power_note.setText(
            tr(
                "ui.pw_note",
                eff=round(rules.poe_psu_efficiency * 100),
                pf=rules.ups_power_factor,
                head=round(rules.ups_headroom * 100),
                legacy=round(pw.legacy_w),
            )
        )

        self._clear(self.poe_box)
        any_poe = False
        for key, c in r.categories.items():
            dev = cat.models.get(c.model)
            if not c.count or not dev or not dev.poe:
                continue
            any_poe = True
            budget = dev.poe.budget_w
            per = c.poe_per_switch_w or (c.poe_load_w / c.count if c.count else 0)
            row = QVBoxLayout()
            row.setSpacing(px(4))
            head = QHBoxLayout()
            head.addWidget(label(f"{t.pick(cat.categories[key].label)} · {c.model} × {c.count}"), 1)
            pct = per / budget * 100 if budget else 0
            head.addWidget(label(tr("ui.poe_line", per=round(per), budget=round(budget), pct=round(pct)), "caption"))
            row.addLayout(head)
            m = Meter()
            m.set(per, budget, rules.poe_warn_ratio)
            row.addWidget(m)
            extra = []
            if dev.poe.ports_bt:
                extra.append(tr("ui.poe_bt", n=dev.poe.ports_bt * c.count, need=c.bt_needed))
            if dev.poe.budget_single_psu_w:
                extra.append(tr("ui.poe_single", w=round(dev.poe.budget_single_psu_w)))
            if extra:
                row.addWidget(label(" · ".join(extra), "faint"))
            holder = QWidget()
            holder.setLayout(row)
            self.poe_box.addWidget(holder)
        if not any_poe:
            self.poe_box.addWidget(label(tr("ui.poe_none"), "muted"))

        self._clear(self.uplink_grid)
        heads = [tr("ui.up_cat"), tr("ui.up_model"), tr("ui.up_links"), tr("ui.up_ratio")]
        for col, h in enumerate(heads):
            self.uplink_grid.addWidget(label(h, "caption"), 0, col)
        row_i = 1
        for key, c in r.categories.items():
            if not c.count:
                continue
            dev = cat.models.get(c.model)
            up = (
                f"{c.uplinks_per_switch}× {dev.uplinks.speed_gbps:g}G {dev.uplinks.type}"
                if dev and dev.uplinks
                else "—"
            )
            ratio = f"{c.oversubscription:.1f}:1" if c.oversubscription else "—"
            self.uplink_grid.addWidget(label(t.pick(cat.categories[key].label)), row_i, 0)
            self.uplink_grid.addWidget(label(c.model, "muted"), row_i, 1)
            self.uplink_grid.addWidget(label(up, "muted"), row_i, 2)
            rl = label(ratio)
            if c.oversubscription > rules.oversubscription_warn:
                rl.setStyleSheet(f"color: {tokens().warning}; font-weight: 600;")
            self.uplink_grid.addWidget(rl, row_i, 3)
            row_i += 1
        self.uplink_grid.addWidget(hline(), row_i, 0, 1, 4)
        self.uplink_grid.addWidget(
            label(tr("ui.up_note", limit=f"{rules.oversubscription_warn:g}"), "caption", wrap=True), row_i + 1, 0, 1, 4
        )

        rk = r.rack
        ps = rk.passive
        lines = []
        if ps.copper_links:
            lines.append(
                tr(
                    "ui.passive_copper",
                    links=ps.copper_links,
                    m=f"{ps.cable_m:,}".replace(",", " "),
                    drums=ps.cable_drums,
                    panels=ps.panels,
                    outlets=ps.outlets,
                )
            )
        else:
            lines.append(tr("ui.passive_none"))
        fspec = cat.passive.fiber.get(ps.fiber_type)
        if ps.fiber_links and fspec:
            lines.append(
                tr(
                    "ui.passive_fiber",
                    type=t.pick(fspec.label),
                    links=ps.fiber_links,
                    m=ps.fiber_m,
                    h=ps.housings_12 + ps.housings_24,
                )
            )
        lines.append(
            tr(
                "ui.rack_summary",
                used=rk.units_total,
                size=rk.rack_size_u,
                model=f"{rk.rack_model or '—'} × {rk.rack_count}",
                idf=rk.idf_count,
            )
        )
        self.passive_text.setText("\n".join(lines))
