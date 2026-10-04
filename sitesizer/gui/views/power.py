"""Power & rack view: power/UPS summary, PoE meters per switch category, uplinks and a rack elevation."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ...core.models import SiteResult
from ...i18n import current, tr
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


class RackElevation(QWidget):
    """Front view of the suggested rack(s); occupied units coloured by category."""

    MAX_RACKS = 3

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.units: list[tuple[str, str, int]] = []  # (label, group, height U)
        self.size_u = 42
        self.racks = 1
        self.setMinimumWidth(px(260))
        theme_manager.changed.connect(self.update)

    def set_units(self, units: list[tuple[str, str, int]], size_u: int, racks: int) -> None:
        self.units = units
        self.size_u = max(size_u, 1)
        self.racks = max(1, min(racks, self.MAX_RACKS))
        self.updateGeometry()
        self.update()

    def _u(self) -> float:
        return px(14) if self.size_u <= 22 else px(11)

    def sizeHint(self) -> QSize:
        return QSize(px(300), int(self._u() * self.size_u + px(40)))

    def minimumSizeHint(self) -> QSize:
        return QSize(px(260), self.sizeHint().height())

    @staticmethod
    def _text_on(fill: QColor) -> QColor:
        lum = 0.2126 * fill.redF() + 0.7152 * fill.greenF() + 0.0722 * fill.blueF()
        return QColor("#1F2D3A") if lum > 0.55 else QColor("#FFFFFF")

    def paintEvent(self, _e: QPaintEvent) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        u = self._u()
        n = self.size_u
        gap = px(14)
        label_w = px(24)
        col_w = (self.width() - label_w - gap * (self.racks - 1)) / self.racks
        fill_iter = iter(self.units)
        pending: tuple[str, str, int] | None = next(fill_iter, None)
        for k in range(self.racks):
            x0 = label_w + k * (col_w + gap)
            frame = QRectF(x0, px(6), col_w, u * n + px(16))
            p.setPen(QPen(t.q("border_strong"), 1.2))
            p.setBrush(t.q("surface_sunken"))
            p.drawRoundedRect(frame, px(7), px(7))
            inner = frame.adjusted(px(8), px(8), -px(8), -px(8))
            p.setFont(theme_manager.font(theme_manager.type.caption - 2))
            for i in range(n):
                y = inner.top() + i * u
                if k == 0 and (n - i) % (5 if n > 22 else 1) == 0:
                    p.setPen(t.q("text_faint"))
                    p.drawText(
                        QRectF(0, y, label_w - px(4), u),
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        str(n - i),
                    )
                p.setPen(QPen(t.q("border"), 0.5))
                p.drawLine(QPointF(inner.left(), y + u), QPointF(inner.right(), y + u))
            y = inner.top()
            while pending is not None:
                text, group, h = pending
                if y + h * u > inner.bottom() + 0.5:
                    break
                rect = QRectF(inner.left() + 1, y + 1, inner.width() - 2, h * u - 2)
                base = t.category(group) if not group.startswith("_") else t.q("border_strong")
                fill = QColor(base)
                if group in ("cabling",) or group.startswith("_"):
                    fill.setAlphaF(0.32)
                    fg = t.q("text_muted")
                else:
                    fg = self._text_on(fill)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(rect, px(2.5), px(2.5))
                if u >= px(11):
                    p.setPen(fg)
                    p.setFont(theme_manager.font(theme_manager.type.caption - 2, QFont.Weight.Medium))
                    p.drawText(
                        rect.adjusted(px(6), 0, -px(4), 0),
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                        text,
                    )
                y += h * u
                pending = next(fill_iter, None)
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
        root = QHBoxLayout(body)
        root.setContentsMargins(px(4), px(4), px(12), px(24))
        root.setSpacing(px(16))
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

        self.uplink_card = Card(tr("ui.uplink_title"), tr("ui.uplink_sub"))
        self.uplink_grid = QGridLayout()
        self.uplink_grid.setHorizontalSpacing(px(16))
        self.uplink_grid.setVerticalSpacing(px(6))
        self.uplink_card.add(self.uplink_grid)
        left.addWidget(self.uplink_card)
        left.addStretch(1)

        self.rack_card = Card(tr("ui.rack_title"), tr("ui.rack_sub"))
        self.rack_summary = label("", "muted", wrap=True)
        self.rack_card.add(self.rack_summary)
        self.rack = RackElevation()
        self.rack_card.add(self.rack)
        self.rack_legend = label("", "caption", wrap=True)
        self.rack_card.add(self.rack_legend)
        right.addWidget(self.rack_card)
        right.addStretch(1)

        state.resultChanged.connect(self.on_result)
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
        self.rack_summary.setText(
            tr(
                "ui.rack_summary",
                used=rk.units_total,
                spare=rk.units_with_spare,
                size=rk.rack_size_u,
                model=f"{rk.rack_model or '—'} × {rk.rack_count}",
                idf=rk.idf_count,
            )
        )
        units: list[tuple[str, str, int]] = []
        if rk.units_other:
            units.append(("OOB 4G/5G", "power", rk.units_other))
        if r.firewall and r.firewall.fits:
            for i in range(r.firewall.count):
                units.append((r.firewall.model + (f" #{i + 1}" if r.firewall.count > 1 else ""), "firewall", 1))
        for i in range(r.core.count):
            units.append((f"{r.core.model} #{i + 1}", "core_switch", 1))
        panels_left = rk.patch_panels
        managers_left = rk.units_managers
        panel_no = 0
        ports = rules.patch_panel_ports
        for key, c in r.categories.items():
            if not c.count:
                continue
            per_switch = -(-c.endpoints // c.count)
            for i in range(c.count):
                for _ in range(min(panels_left, -(-per_switch // ports))):
                    panel_no += 1
                    panels_left -= 1
                    units.append((tr("ui.rack_panel", n=panel_no), "cabling", 1))
                units.append((f"{c.model} #{i + 1}", key, 1))
                if managers_left > 0:
                    managers_left -= 1
                    units.append((tr("ui.rack_manager"), "_manager", 1))
        while panels_left > 0:
            panel_no += 1
            panels_left -= 1
            units.append((tr("ui.rack_panel", n=panel_no), "cabling", 1))
        if rk.units_ups:
            units.append((pw.ups_model or "UPS", "power", rk.units_ups))
        self.rack.set_units(units, rk.rack_size_u or 42, rk.rack_count)
        self.rack_legend.setText(tr("ui.rack_legend"))
