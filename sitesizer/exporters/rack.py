"""Rack elevation renderer (QPainter): front view of every cabinet of a location.

Like the topology diagram, one renderer serves the in-app view, PNG/SVG exports and the images
in Excel and PDF. Units are numbered from the bottom (U1) as on real cabinet rails; a switch is
drawn with its patch panels and cable manager directly above it.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen

from ..core.catalog import Catalog
from ..core.models import RackItem, RackPlan, SiteResult
from ..i18n import Translator
from .diagram import EXPORT_STYLE, DiagramStyle, _font, ensure_gui_app

EQUIPMENT_COLORS = {
    "wifi_switch": "#4F8A74",
    "access_switch": "#5F72A0",
    "camera_switch": "#A0784A",
    "core_switch": "#1F4E78",
    "firewall": "#94505B",
    "power": "#857637",
    "device": "#4C6E91",
}
PASSIVE_LIGHT = {
    "panel": "#D9E0E8",
    "manager": "#B9C1C9",
    "fiber": "#CFE6E3",
    "pdu": "#E6E1CF",
    "shelf": "#E2E2E2",
    "blank": "#F1F1F1",
    "custom": "#E9DFF0",
}
PASSIVE_DARK = {
    "panel": "#35404C",
    "manager": "#4A535C",
    "fiber": "#2D4A48",
    "pdu": "#45402F",
    "shelf": "#3A3A3A",
    "blank": "#2C2C2C",
    "custom": "#3E3546",
}


def _text_on(fill: QColor) -> QColor:
    lum = 0.2126 * fill.redF() + 0.7152 * fill.greenF() + 0.0722 * fill.blueF()
    return QColor("#1F2D3A") if lum > 0.55 else QColor("#FFFFFF")


class RackDiagram:
    """All cabinets of one location side by side (wrapping after ``PER_ROW``)."""

    PAD = 28.0
    U_H = 26.0
    """Tall enough for the device name with its model on a second line."""
    COL_W = 330.0
    RAIL_W = 28.0
    HEAD_H = 46.0
    GAP = 36.0
    PER_ROW = 4

    def __init__(
        self,
        result: SiteResult,
        catalog: Catalog,
        lang: str = "uk",
        style: DiagramStyle = EXPORT_STYLE,
        colors: dict[str, str] | None = None,
        dark: bool = False,
    ) -> None:
        ensure_gui_app()
        self.r = result
        self.catalog = catalog
        self.t = Translator(lang)
        self.s = style
        self.colors = {**EQUIPMENT_COLORS, **(colors or {}), **(PASSIVE_DARK if dark else PASSIVE_LIGHT)}
        self.plans: list[RackPlan] = list(result.rack.plans)
        self.mark_manual = False
        """Draw a small marker on items placed by hand (editor only)."""
        self.f_title = _font(14, QFont.Weight.DemiBold)
        self.f_sub = _font(11)
        self.f_item = _font(11, QFont.Weight.Medium)
        self.f_model = _font(9.5)
        self.f_u = _font(9)

    # ---- geometry ------------------------------------------------------------------------
    def _rows(self) -> list[list[RackPlan]]:
        return [self.plans[i : i + self.PER_ROW] for i in range(0, len(self.plans), self.PER_ROW)] or [[]]

    def _row_h(self, row: list[RackPlan]) -> float:
        size = max((p.size_u for p in row), default=24)
        return self.HEAD_H + size * self.U_H + 2 * 12

    def size(self) -> QSizeF:
        rows = self._rows()
        cols = min(len(self.plans), self.PER_ROW) or 1
        w = 2 * self.PAD + cols * (self.RAIL_W + self.COL_W) + (cols - 1) * self.GAP
        h = 2 * self.PAD + sum(self._row_h(r) for r in rows) + (len(rows) - 1) * self.GAP
        return QSizeF(w, h)

    def origins(self) -> list[tuple[RackPlan, float, float]]:
        """Top-left corner of every cabinet (including its unit-number rail)."""
        out = []
        y = self.PAD
        for row in self._rows():
            x = self.PAD
            for plan in row:
                out.append((plan, x, y))
                x += self.RAIL_W + self.COL_W + self.GAP
            y += self._row_h(row) + self.GAP
        return out

    def header_rect(self, x: float, y: float) -> QRectF:
        return QRectF(x + self.RAIL_W, y, self.COL_W, self.HEAD_H)

    def inner_rect(self, plan: RackPlan, x: float, y: float) -> QRectF:
        top = y + self.HEAD_H
        return QRectF(x + self.RAIL_W + 12, top + 12, self.COL_W - 24, plan.size_u * self.U_H)

    def unit_rect(self, plan: RackPlan, x: float, y: float, u: int, height: int) -> QRectF:
        return self._unit_rect(plan, self.inner_rect(plan, x, y), u, height)

    def hit(self, pos: QPointF) -> tuple[RackPlan | None, RackItem | None, int, bool]:
        """(cabinet, item, unit under the point, point is on the cabinet header)."""
        for plan, x, y in self.origins():
            if self.header_rect(x, y).contains(pos):
                return plan, None, 0, True
            inner = self.inner_rect(plan, x, y)
            body = inner.adjusted(-12 - self.RAIL_W, 0, 12, 0)
            if body.contains(pos):
                u = plan.size_u - int((pos.y() - inner.top()) // self.U_H)
                u = max(1, min(plan.size_u, u))
                item = next((it for it in plan.items if it.u <= u <= it.top), None)
                return plan, item, u, False
        return None, None, 0, False

    # ---- painting ------------------------------------------------------------------------
    def paint(self, p: QPainter, background: bool = True) -> None:
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if background:
            size = self.size()
            p.fillRect(QRectF(0, 0, size.width(), size.height()), QColor(self.s.bg))
        for plan, x, y in self.origins():
            self._paint_rack(p, plan, x, y)
        p.restore()

    def _paint_rack(self, p: QPainter, plan: RackPlan, x: float, y: float) -> None:
        s, t = self.s, self.t
        left = x + self.RAIL_W
        # header
        p.setPen(QColor(s.light_text))
        p.setFont(self.f_title)
        p.drawText(
            QRectF(left, y, self.COL_W, 22),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            t.t("rack.title", name=plan.name, size=plan.size_u),
        )
        p.setPen(QColor(s.muted))
        p.setFont(self.f_sub)
        sub = t.t("rack.subtitle", model=plan.model or "—", used=plan.used_u, free=plan.free_u)
        p.drawText(
            QRectF(left, y + 22, self.COL_W, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetricsF(self.f_sub).elidedText(sub, Qt.TextElideMode.ElideRight, self.COL_W),
        )
        # cabinet body
        top = y + self.HEAD_H
        body_h = plan.size_u * self.U_H
        frame = QRectF(left, top, self.COL_W, body_h + 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(s.dark))
        p.drawRoundedRect(frame, 6, 6)
        inner = QRectF(left + 12, top + 12, self.COL_W - 24, body_h)
        p.setBrush(QColor(s.bg))
        p.drawRect(inner)
        # unit grid + numbers
        p.setFont(self.f_u)
        for i in range(plan.size_u):
            u = plan.size_u - i
            ry = inner.top() + i * self.U_H
            p.setPen(QPen(QColor(s.light_edge), 0.6))
            p.drawLine(QPointF(inner.left(), ry + self.U_H), QPointF(inner.right(), ry + self.U_H))
            p.setPen(QColor(s.muted))
            p.drawText(
                QRectF(x, ry, self.RAIL_W - 4, self.U_H),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(u),
            )
        # free gaps
        occupied = [False] * (plan.size_u + 1)
        for it in plan.items:
            for u in range(it.u, min(plan.size_u, it.u + it.height - 1) + 1):
                occupied[u] = True
        u = plan.size_u
        p.setFont(self.f_model)
        while u >= 1:
            if occupied[u]:
                u -= 1
                continue
            start = u
            while u >= 1 and not occupied[u]:
                u -= 1
            n = start - u
            if n >= 2:
                rect = self._unit_rect(plan, inner, u + 1, n)
                text = f"{t.t('rack.free')} · {n}U"
                tw = QFontMetricsF(self.f_model).horizontalAdvance(text) + 14
                pill = QRectF(rect.center().x() - tw / 2, rect.center().y() - 9, tw, 18)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(s.bg))
                p.drawRoundedRect(pill, 9, 9)
                p.setPen(QColor(s.muted))
                p.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)
        # items
        for it in plan.items:
            self._paint_item(p, plan, inner, it)

    def _unit_rect(self, plan: RackPlan, inner: QRectF, u: int, height: int) -> QRectF:
        top_index = plan.size_u - (u + height - 1)
        return QRectF(inner.left(), inner.top() + top_index * self.U_H, inner.width(), height * self.U_H)

    def _paint_item(self, p: QPainter, plan: RackPlan, inner: QRectF, it: RackItem) -> None:
        rect = self._unit_rect(plan, inner, it.u, it.height).adjusted(1.5, 1.2, -1.5, -1.2)
        fill = QColor(self.colors.get(it.group, self.s.light))
        p.setPen(QPen(QColor(self.s.light_edge), 0.6) if it.group in PASSIVE_LIGHT else Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(rect, 2.5, 2.5)
        fg = _text_on(fill)
        if it.group == "panel":
            self._paint_ports(p, rect, fg)
        text_rect = rect.adjusted(8, 0, -8, 0)
        if it.group == "panel":
            text_rect.setRight(text_rect.right() - text_rect.width() * 0.42 - 4)
        model = it.model if it.model and it.model not in it.label else ""
        tag = f"{it.height}U" if it.height > 1 else ""
        fm_item, fm_model = QFontMetricsF(self.f_item), QFontMetricsF(self.f_model)
        tag_w = fm_model.horizontalAdvance(tag) + 6 if tag and it.group != "panel" else 0
        name_rect = text_rect.adjusted(0, 0, -tag_w, 0)
        muted = QColor(fg)
        muted.setAlphaF(0.75)
        p.setPen(fg)
        p.setFont(self.f_item)
        if model:
            # name on top, model right below it, centred as a block in the item
            block = fm_item.height() + fm_model.height() - 2
            top = rect.center().y() - block / 2
            p.drawText(
                QRectF(name_rect.left(), top, name_rect.width(), fm_item.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                fm_item.elidedText(it.label, Qt.TextElideMode.ElideRight, name_rect.width()),
            )
            p.setPen(muted)
            p.setFont(self.f_model)
            p.drawText(
                QRectF(name_rect.left(), top + fm_item.height() - 2, name_rect.width(), fm_model.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                fm_model.elidedText(model, Qt.TextElideMode.ElideRight, name_rect.width()),
            )
        else:
            p.drawText(
                name_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                fm_item.elidedText(it.label, Qt.TextElideMode.ElideRight, name_rect.width()),
            )
        if tag_w:
            p.setPen(muted)
            p.setFont(self.f_model)
            p.drawText(text_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, tag)
        if it.manual and self.mark_manual:
            pin = QRectF(rect.right() - 7, rect.top() + 3, 4, 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#E0A030"))
            p.drawEllipse(pin)

    def _paint_ports(self, p: QPainter, rect: QRectF, fg: QColor) -> None:
        """24 small port squares on the right side of a patch panel."""
        ports = 24
        area_w = rect.width() * 0.42
        x0 = rect.right() - area_w - 4
        cell = area_w / ports
        side = max(2.0, min(cell - 1.2, rect.height() - 7))
        c = QColor(fg)
        c.setAlphaF(0.55)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        for i in range(ports):
            gap = 2.0 if i >= ports // 2 else 0.0
            px_ = x0 + i * cell + gap
            p.drawRect(QRectF(px_, rect.center().y() - side / 2, side, side))


def rack_table_rows(result: SiteResult) -> list[tuple[str, str, int, str, str]]:
    """(cabinet, units, height, label, model) for every placed item, top to bottom."""
    rows = []
    for plan in result.rack.plans:
        for it in plan.items:
            span = f"{it.u}" if it.height == 1 else f"{it.u}–{it.u + it.height - 1}"
            rows.append((plan.name, span, it.height, it.label, it.model))
    return rows


def has_racks(result: SiteResult) -> bool:
    return bool(result.rack.plans)
