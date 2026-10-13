"""Painter of the device-level network topology (:mod:`sitesizer.core.topology`).

Colours follow the whiteboard sketch the design comes from: FortiGates and the HA link red, the core
pair and its links blue, access stacks and FortiLink green. One painter serves the editor in the app,
PNG / SVG / PDF and the picture in Excel; draw.io gets the same nodes and routes (``exporters.drawio``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

from ..core.catalog import Catalog
from ..core.models import SiteResult, TopologyEdits
from ..core.topology import Rect, TopoLinkResult, Topology, TopoNode, build_topology
from ..i18n import Translator
from .diagram import EXPORT_STYLE, DiagramStyle, _font, ensure_gui_app


@dataclass(frozen=True)
class TopoColors:
    """Fill / edge / text of a node kind and the colour of a link kind."""

    firewall: tuple[str, str, str] = ("#FBE9E7", "#C62828", "#8E1B1B")
    core_switch: tuple[str, str, str] = ("#E3EDF8", "#1F4E78", "#163A5A")
    wifi_switch: tuple[str, str, str] = ("#E6F4EC", "#2E7D57", "#1D5239")
    camera_switch: tuple[str, str, str] = ("#FFF3E0", "#B26A00", "#7A4800")
    access_switch: tuple[str, str, str] = ("#E7F3F1", "#2B8C7E", "#1B5C52")
    wan: tuple[str, str, str] = ("#FFFFFF", "#7C8894", "#4A5560")
    links: tuple[tuple[str, str], ...] = (
        ("ha", "#C62828"),
        ("isl", "#1F4E78"),
        ("uplink", "#2F6DB5"),
        ("fortilink", "#2B8C7E"),
        ("stack", "#2B8C7E"),
        ("wan", "#7C8894"),
        ("custom", "#8E6BBF"),
    )

    def node(self, kind: str) -> tuple[str, str, str]:
        return getattr(self, kind, self.access_switch)  # type: ignore[no-any-return]

    def link(self, kind: str) -> str:
        return dict(self.links).get(kind, "#7C8894")


LIGHT = TopoColors()
DARK = TopoColors(
    firewall=("#3A1E1E", "#EF7B7B", "#FFD9D9"),
    core_switch=("#1C2D40", "#7FB0E3", "#DCEBFA"),
    wifi_switch=("#1B3027", "#6CC79A", "#D5F2E3"),
    camera_switch=("#33281A", "#E0A657", "#FBE7CB"),
    access_switch=("#18302D", "#5FC1B2", "#D2F1EC"),
    wan=("#20262C", "#98A4AF", "#D5DCE2"),
    links=(
        ("ha", "#EF7B7B"),
        ("isl", "#7FB0E3"),
        ("uplink", "#7FB0E3"),
        ("fortilink", "#5FC1B2"),
        ("stack", "#5FC1B2"),
        ("wan", "#98A4AF"),
        ("custom", "#B79BE0"),
    ),
)


def _is_dark(color: str) -> bool:
    c = QColor(color)
    return c.lightnessF() < 0.45


class TopologyDiagram:
    """Same interface as :class:`~sitesizer.exporters.diagram.SiteDiagram` (``size`` / ``paint``) plus hit tests."""

    def __init__(
        self,
        result: SiteResult | None,
        catalog: Catalog,
        lang: str = "uk",
        style: DiagramStyle = EXPORT_STYLE,
        title: bool = False,
        edits: TopologyEdits | None = None,
        topology: Topology | None = None,
    ) -> None:
        ensure_gui_app()
        self.t = Translator(lang)
        self.s = style
        self.c = DARK if _is_dark(style.bg) else LIGHT
        self.show_title = title
        if topology is None:
            topology = build_topology(result, catalog, lang, edits) if result is not None else Topology()
        self.topo = topology
        self.selected: set[str] = set()
        """Node / link ids drawn highlighted (the editor's selection)."""
        self.f_name = _font(12.5, QFont.Weight.DemiBold)
        self.f_model = _font(11)
        self.f_link = _font(10)
        self.f_port = _font(10, QFont.Weight.DemiBold)
        self.f_module = _font(9)
        self.f_endpoints = _font(10)
        self.f_group = _font(11, QFont.Weight.DemiBold, spacing=0.3)
        self.f_caption = _font(12, italic=True)
        self.f_title = _font(18, QFont.Weight.DemiBold)
        self.title_h = 40.0 if title else 0.0

    # ---- geometry ---------------------------------------------------------------------------
    @property
    def offset(self) -> QPointF:
        """Node coordinates -> drawing coordinates."""
        ox, oy = self.topo.origin
        return QPointF(-ox, -oy + self.title_h)

    def size(self) -> QSizeF:
        if self.topo.empty:
            return QSizeF(420, 120)
        return QSizeF(self.topo.width, self.topo.height + self.title_h)

    def to_topo(self, pos: QPointF) -> QPointF:
        return pos - self.offset

    def hit(self, pos: QPointF) -> tuple[str, str]:
        """("node" | "link" | "group" | "", id) under a point in drawing coordinates."""
        p = self.to_topo(pos)
        for n in reversed(self.topo.nodes):
            if n.rect.contains(p.x(), p.y()):
                return "node", n.id
        best, dist = "", 6.0
        for lk in self.topo.links:
            for (x1, y1), (x2, y2) in zip(lk.points, lk.points[1:], strict=False):
                d = _seg_dist(p.x(), p.y(), x1, y1, x2, y2)
                if d < dist:
                    best, dist = lk.id, d
        if best:
            return "link", best
        for g in self.topo.groups:
            r = g.rect
            if r.x <= p.x() <= r.right and r.y <= p.y() <= r.y + 22:
                return "group", g.id
        return "", ""

    # ---- painting -----------------------------------------------------------------------------
    def paint(self, p: QPainter, background: bool = True) -> None:
        s = self.s
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        size = self.size()
        if background:
            p.fillRect(QRectF(0, 0, size.width(), size.height()), QColor(s.bg))
        if self.show_title:
            p.setPen(QColor(s.light_text))
            p.setFont(self.f_title)
            p.drawText(
                QRectF(36, 14, size.width() - 72, 30),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self.topo.title,
            )
        if self.topo.empty:
            p.setPen(QColor(s.muted))
            p.setFont(self.f_caption)
            p.drawText(QRectF(0, 0, size.width(), size.height()), Qt.AlignmentFlag.AlignCenter, self.t.t("dia.empty"))
            return
        p.save()
        p.translate(self.offset)
        for cab in self.topo.cabinets:
            self._paint_cabinet(p, cab.rect, cab.title)
        for g in self.topo.groups:
            self._paint_group(p, g.rect, g.title, g.id in self.selected)
        for lk in self.topo.links:
            self._paint_link(p, lk)
        for n in self.topo.nodes:
            self._paint_node(p, n)
        for lk in self.topo.links:
            self._paint_link_label(p, lk)
            self._paint_ports(p, lk)
        p.restore()
        # footer: legend of the access design and findings
        p.setFont(self.f_caption)
        p.setPen(QColor(s.muted))
        mode = self.t.t("topo.mode_ring" if self.topo.access == "ring" else "topo.mode_dual")
        text = "   •   ".join([mode, *self.topo.problems])
        p.drawText(QRectF(0, size.height() - 34, size.width(), 20), Qt.AlignmentFlag.AlignCenter, text)

    def _paint_cabinet(self, p: QPainter, r: Rect, title: str) -> None:
        """The cabinet the stacks stand in (its PoE and ASW stacks are in one frame)."""
        s = self.s
        rect = QRectF(r.x, r.y, r.w, r.h)
        p.setPen(QPen(QColor(s.light_edge), 1.4))
        fill = QColor(s.light)
        fill.setAlphaF(0.22)
        p.setBrush(fill)
        p.drawRoundedRect(rect, 12, 12)
        p.setPen(QColor(s.light_text))
        p.setFont(self.f_group)
        p.drawText(rect.adjusted(12, 5, -8, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, title)

    def _paint_group(self, p: QPainter, r: Rect, title: str, selected: bool) -> None:
        s = self.s
        rect = QRectF(r.x, r.y, r.w, r.h)
        pen = QPen(QColor(s.light_edge), 1.1, Qt.PenStyle.DashLine)
        if selected:
            pen = QPen(QColor(s.dark), 1.6, Qt.PenStyle.DashLine)
        p.setPen(pen)
        fill = QColor(s.light)
        fill.setAlphaF(0.35)
        p.setBrush(fill)
        p.drawRoundedRect(rect, 10, 10)
        p.setPen(QColor(s.muted))
        p.setFont(self.f_group)
        p.drawText(rect.adjusted(12, 3, -8, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, title)

    def _paint_link(self, p: QPainter, lk: TopoLinkResult) -> None:
        if len(lk.points) < 2:
            return
        color = QColor(self.c.link(lk.kind))
        width = {"isl": 2.6, "ha": 2.2, "uplink": 1.8, "wan": 1.3}.get(lk.kind, 1.6)
        if lk.id in self.selected:
            halo = QColor(color)
            halo.setAlphaF(0.25)
            p.setPen(QPen(halo, width + 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPath(_rounded(lk.points))
        pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        if lk.kind == "wan":
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(_rounded(lk.points))
        if lk.kind == "isl" and len(lk.points) == 2:
            # the ICL is two links (Fortinet recommends at least two)
            (x1, y1), (x2, y2) = lk.points
            p.drawLine(QPointF(x1, y1 + 6), QPointF(x2, y2 + 6))

    def _paint_link_label(self, p: QPainter, lk: TopoLinkResult) -> None:
        if not lk.label or len(lk.points) < 2:
            return
        fm = QFontMetricsF(self.f_link)
        cx, cy = lk.point_at(lk.label_t)
        if lk.kind in ("ha", "isl"):
            cy -= 12
        elif lk.kind == "stack":
            cx += 4 + fm.horizontalAdvance(lk.label) / 2 + 4
        w = fm.horizontalAdvance(lk.label) + 8
        rect = QRectF(cx - w / 2, cy - 8, w, 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.s.bg))
        p.drawRoundedRect(rect, 4, 4)
        p.setPen(QColor(self.c.link(lk.kind)))
        p.setFont(self.f_link)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, lk.label)

    def _paint_ports(self, p: QPainter, lk: TopoLinkResult) -> None:
        """Port number at each end of a link, just outside the device; the module in that port is written
        along the line (like the hand-drawn schemes), so that neighbouring ports do not overlap."""
        if len(lk.points) < 2:
            return
        color = QColor(self.c.link(lk.kind))
        bg = QColor(self.s.bg)
        bg.setAlphaF(0.85)
        fm_p, fm_m = QFontMetricsF(self.f_port), QFontMetricsF(self.f_module)
        ends = (
            (lk.points[0], lk.points[1], lk.port_a, lk.module_a, self.topo.node(lk.a)),
            (lk.points[-1], lk.points[-2], lk.port_b, lk.module_b, self.topo.node(lk.b)),
        )
        for (x0, y0), (x1, y1), port, module, node in ends:
            vertical = abs(x1 - x0) < 0.5
            if node is not None:  # leaves the device through its top or bottom edge
                upright = abs(y0 - node.rect.y) < 1 or abs(y0 - node.rect.bottom) < 1
            else:
                upright = abs(y1 - y0) > abs(x1 - x0)
            if port:
                w = fm_p.horizontalAdvance(port) + 4
                if upright:
                    x = x0 - 4 - w if lk.kind == "stack" else x0 + 4  # a stack's cable label is on its right
                    y = y0 - 14 if y1 < y0 else y0 + 2
                else:  # sideways: under the line (its cable label goes up the lane), HA / ISL above it
                    x = x0 + 4 if x1 > x0 else x0 - 4 - w
                    y = y0 - 14 if lk.kind in ("ha", "isl") else y0 + 2
                self._tag(p, QRectF(x, y, w, 12), port, self.f_port, color, bg)
            if not module:
                continue
            if upright and not vertical:  # a slanted line: the module next to the port
                w = fm_m.horizontalAdvance(module) + 4
                y = y0 - 26 if y1 < y0 else y0 + 14
                self._tag(p, QRectF(x0 + 4, y, w, 11), module, self.f_module, color, bg)
                continue
            length = fm_m.horizontalAdvance(module) + 4
            p.save()
            if vertical:
                down = y1 > y0
                p.translate(x0 + 2 if lk.kind != "stack" else x0 - 14, y0 + (16 if down else -16))
                p.rotate(90 if down else -90)
                rect = QRectF(0, -12, length, 11) if down else QRectF(0, 1, length, 11)
            else:
                right = x1 > x0
                p.translate(x0 + (6 if right else -6 - length), y0 - 13)
                rect = QRectF(0, 0, length, 11)
            self._tag(p, rect, module, self.f_module, color, bg)
            p.restore()

    @staticmethod
    def _tag(p: QPainter, rect: QRectF, text: str, font: QFont, color: QColor, bg: QColor) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(rect.adjusted(-1, 0, 1, 0), 3, 3)
        p.setPen(color)
        p.setFont(font)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_node(self, p: QPainter, n: TopoNode) -> None:
        fill, edge, text = self.c.node(n.kind)
        r = QRectF(n.rect.x, n.rect.y, n.rect.w, n.rect.h)
        if n.id in self.selected:
            halo = QColor(edge)
            halo.setAlphaF(0.25)
            p.setPen(QPen(halo, 6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 9, 9)
        p.setPen(QPen(QColor(edge), 2.0 if n.kind in ("firewall", "core_switch") else 1.4))
        p.setBrush(QColor(fill))
        radius = r.height() / 2 if n.kind == "wan" else 8.0
        p.drawRoundedRect(r, radius, radius)
        p.setPen(QColor(text))
        if n.kind == "wan" or not n.model:
            p.setFont(self.f_name)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, n.label)
            return
        fm = QFontMetricsF(self.f_name)
        name = fm.elidedText(n.label, Qt.TextElideMode.ElideMiddle, r.width() - 14)
        rows = 3 if n.endpoints else 2
        row_h = (r.height() - 8) / rows
        p.setFont(self.f_name)
        p.drawText(QRectF(r.x() + 7, r.y() + 4, r.width() - 14, row_h), Qt.AlignmentFlag.AlignCenter, name)
        muted = QColor(text)
        muted.setAlphaF(0.72)
        p.setPen(muted)
        p.setFont(self.f_model)
        p.drawText(QRectF(r.x() + 7, r.y() + 4 + row_h, r.width() - 14, row_h), Qt.AlignmentFlag.AlignCenter, n.model)
        if n.endpoints:
            p.setPen(QColor(self.c.link("fortilink")))
            p.setFont(self.f_endpoints)
            fe = QFontMetricsF(self.f_endpoints)
            p.drawText(
                QRectF(r.x() + 5, r.y() + 4 + 2 * row_h, r.width() - 10, row_h),
                Qt.AlignmentFlag.AlignCenter,
                fe.elidedText(n.endpoints, Qt.TextElideMode.ElideRight, r.width() - 10),
            )


def _rounded(points: list[tuple[float, float]], radius: float = 8.0) -> QPainterPath:
    path = QPainterPath(QPointF(*points[0]))
    for i in range(1, len(points) - 1):
        (x0, y0), (x1, y1), (x2, y2) = points[i - 1], points[i], points[i + 1]
        d1 = math.hypot(x1 - x0, y1 - y0)
        d2 = math.hypot(x2 - x1, y2 - y1)
        rad = min(radius, d1 / 2, d2 / 2)
        if rad < 0.5 or d1 == 0 or d2 == 0:
            path.lineTo(x1, y1)
            continue
        ax = x1 - (x1 - x0) / d1 * rad
        ay = y1 - (y1 - y0) / d1 * rad
        bx = x1 + (x2 - x1) / d2 * rad
        by = y1 + (y2 - y1) / d2 * rad
        path.lineTo(ax, ay)
        path.quadTo(x1, y1, bx, by)
    path.lineTo(QPointF(*points[-1]))
    return path


def _seg_dist(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    k = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    qx, qy = x1 + k * dx, y1 + k * dy
    return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5
