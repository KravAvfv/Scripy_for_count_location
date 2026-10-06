"""In-app help: the sizing logic in plain language (successor of the console manual)."""

from __future__ import annotations

import html

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QTextBrowser, QVBoxLayout, QWidget

from ...i18n import current, tr
from ..state import AppState
from ..theme import theme_manager, tokens
from ..widgets.controls import button, px


class HelpView(QWidget):
    start_tour = Signal()

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(px(12))
        bar = QHBoxLayout()
        tour = button(tr("ui.tour_start"), None, "sparkles")
        tour.clicked.connect(self.start_tour)
        bar.addWidget(tour)
        bar.addStretch(1)
        root.addLayout(bar)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        root.addWidget(self.browser, 1)
        state.catalogChanged.connect(self.refresh)
        theme_manager.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        t = current()
        tk = tokens()
        cat = self.state.catalog
        r = cat.rules
        tiers = "".join(
            f"<tr><td><b>{k} · {html.escape(t.pick(v.label))}</b><br><span class='m'>{html.escape(t.pick(v.description))}"
            f"</span></td><td>{html.escape(', '.join(e for on, e in self._effects(v) if on) or '—')}</td>"
            f"<td>{html.escape(t.pick(v.sla))}</td></tr>"
            for k, v in sorted(cat.tiers.items())
        )
        ladder = " → ".join(
            f"{m} ({cat.device(m).firewall.switch_limit(r.fortios_default)} FSW / {cat.device(m).firewall.max_aps} AP)"  # type: ignore[union-attr]
            for m in cat.firewall_ladder
        )
        cats = "".join(
            f"<li><b>{html.escape(t.pick(c.label))}</b>: {c.base} / {c.premium} — "
            f"{tr('help.per_switch', n=c.endpoints_per_switch)}</li>"
            for c in cat.categories.values()
        )
        body = tr(
            "help.html",
            tiers=tiers,
            ladder=ladder,
            cats=cats,
            core_sw=r.core_min_switches,
            core_floors=r.core_min_floors,
            dac_u=r.dac_short_max_u,
            template=html.escape(r.ip.id_template),
            reserve=f"{r.reserve_percent_default:g}",
            warn=round(r.poe_warn_ratio * 100),
            cam=f"{r.camera_watts_default:g}",
            head=round(r.fw_throughput_headroom * 100),
            ups=round(r.ups_headroom * 100),
            buf=round(r.ip.buffer * 100),
            oversub=f"{r.oversubscription_warn:g}",
        )
        css = (
            f"body {{ font-family: 'Inter', 'Segoe UI'; font-size: {px(14)}px; color: {tk.text}; line-height: 150%; }}"
            f"h1 {{ font-size: {px(22)}px; font-weight: 600; margin: 4px 0 8px 0; }}"
            f"h2 {{ font-size: {px(16)}px; font-weight: 600; margin-top: 22px; color: {tk.text}; }}"
            f".m {{ color: {tk.text_muted}; }}"
            f"td {{ padding: 6px 12px 6px 0; vertical-align: top; border-bottom: 1px solid {tk.border}; }}"
            f"th {{ text-align: left; color: {tk.text_muted}; padding: 4px 12px 4px 0; font-weight: 600; }}"
            f"code {{ background: {tk.surface_alt}; }}"
            f"a {{ color: {tk.info}; }}"
        )
        self.browser.document().setDefaultStyleSheet(css)
        self.browser.setHtml(f"<body>{body}</body>")

    @staticmethod
    def _effects(v) -> list[tuple[bool, str]]:
        t = current()
        return [
            (v.fw_ha, tr("effect.fw_ha")),
            (v.core_redundant, tr("effect.core")),
            (v.ups, tr("effect.ups")),
            (v.oob, tr("effect.oob")),
            (v.dual_psu, tr("effect.psu")),
            (v.dual_uplinks, tr("effect.uplinks")),
            (v.dual_wan, tr("effect.dual_wan")),
            (v.spare_percent > 0, tr("effect.spares", pct=f"{v.spare_percent:g}")),
            (v.fortiguard != "none", tr("effect.bundle", bundle=t.t(f"bundle.{v.fortiguard}"))),
        ]
