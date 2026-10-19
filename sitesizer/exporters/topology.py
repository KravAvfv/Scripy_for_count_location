"""Painter of the device-level network topology (:mod:`sitesizer.core.topology`).

Colours follow the whiteboard sketch the design comes from: FortiGates and the HA link red, the core
pair and its links blue, access stacks and FortiLink green. One painter serves the editor in the app,
PNG / SVG / PDF and the picture in Excel; draw.io gets the same nodes and routes (``exporters.drawio``).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer

from ..core.catalog import Catalog
from ..core.models import SiteResult, TopologyEdits
from ..core.topology import ODF_CAP_W, Rect, TopoLinkResult, Topology, TopoNode, build_topology
from ..i18n import Translator
from .device_icons import ICON_H, ICON_W, icon_svg
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
    odf: tuple[str, str, str] = ("#FAFAF7", "#4E5A66", "#26313B")
    panel: tuple[str, str, str] = ("#F6F1EC", "#8D6E63", "#4E342E")
    free_cell: str = "#C3CBD3"
    bad: str = "#C62828"
    note: str = "#B26A00"
    links: tuple[tuple[str, str], ...] = (
        ("ha", "#C62828"),
        ("isl", "#1F4E78"),
        ("uplink", "#2F6DB5"),
        ("fortilink", "#2B8C7E"),
        ("stack", "#2B8C7E"),
        ("wan", "#7C8894"),
        ("custom", "#8E6BBF"),
        ("patch", "#8D6E63"),
    )
    rooms: tuple[str, ...] = ("#2B8C7E", "#7B3FA8", "#C2570C", "#1565C0", "#B0245E", "#4E7D14", "#00838F", "#6D4C41")
    """Links of telecom room 0, 1, 2…: each room its own colour."""

    def node(self, kind: str) -> tuple[str, str, str]:
        return getattr(self, kind, self.access_switch)  # type: ignore[no-any-return]

    def link(self, kind: str, room: int = -1) -> str:
        """Colour of a link: an access link (FortiLink, stack) takes the colour of its telecom room."""
        if room >= 0 and kind in ROOM_KINDS:
            return self.rooms[room % len(self.rooms)]
        return dict(self.links).get(kind, "#7C8894")


ROOM_KINDS = ("fortilink", "stack", "custom")
"""Link kinds coloured by the telecom room of their switch."""


LIGHT = TopoColors()
DARK = TopoColors(
    firewall=("#3A1E1E", "#EF7B7B", "#FFD9D9"),
    core_switch=("#1C2D40", "#7FB0E3", "#DCEBFA"),
    wifi_switch=("#1B3027", "#6CC79A", "#D5F2E3"),
    camera_switch=("#33281A", "#E0A657", "#FBE7CB"),
    access_switch=("#18302D", "#5FC1B2", "#D2F1EC"),
    wan=("#20262C", "#98A4AF", "#D5DCE2"),
    odf=("#1E2329", "#9AA7B4", "#E3E8ED"),
    panel=("#2A231F", "#C2A091", "#F0E2DA"),
    free_cell="#4A5560",
    bad="#EF7B7B",
    note="#E0A657",
    links=(
        ("ha", "#EF7B7B"),
        ("isl", "#7FB0E3"),
        ("uplink", "#7FB0E3"),
        ("fortilink", "#5FC1B2"),
        ("stack", "#5FC1B2"),
        ("wan", "#98A4AF"),
        ("custom", "#B79BE0"),
        ("patch", "#C2A091"),
    ),
    rooms=("#5FC1B2", "#B88BE0", "#F0A05C", "#7FB0E3", "#E57FA8", "#9CCB5E", "#5CC8D4", "#C2A091"),
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
        self.f_odf_name = _font(10, QFont.Weight.DemiBold)
        self.f_odf = _font(9)
        self.f_cell = _font(8.5, QFont.Weight.DemiBold)
        self.f_group = _font(11, QFont.Weight.DemiBold, spacing=0.3)
        self.f_caption = _font(12, italic=True)
        self.f_title = _font(18, QFont.Weight.DemiBold)
        self.title_h = 40.0 if title else 0.0
        self._icons: dict[str, QSvgRenderer] = {}

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
        under: list[Seg] = []
        for lk in self.topo.links:
            self._paint_link(p, lk, under)
            if lk.kind not in ("ha", "isl"):
                under += _segments(lk.points)
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

    def _paint_link(self, p: QPainter, lk: TopoLinkResult, under: list[Seg] | None = None) -> None:
        if len(lk.points) < 2:
            return
        color = QColor(self.c.link(lk.kind, lk.room))
        width = {"isl": 2.6, "ha": 2.2, "uplink": 1.8, "wan": 1.3}.get(lk.kind, 1.6)
        if lk.id in self.selected:
            halo = QColor(color)
            halo.setAlphaF(0.25)
            p.setPen(QPen(halo, width + 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPath(_rounded(lk.points, under=under))
        pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        if lk.kind == "wan":
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(_rounded(lk.points, under=under))
        if lk.kind == "isl" and len(lk.points) == 2:
            # the ICL is two links (Fortinet recommends at least two)
            (x1, y1), (x2, y2) = lk.points
            p.drawLine(QPointF(x1, y1 + 6), QPointF(x2, y2 + 6))

    def _paint_link_label(self, p: QPainter, lk: TopoLinkResult) -> None:
        if not lk.label or len(lk.points) < 2:
            return
        if lk.via and not lk.typed_label:
            return  # through ODFs: the cable is written at the ODF, the ports in its cells
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
        p.setPen(QColor(self.c.link(lk.kind, lk.room)))
        p.setFont(self.f_link)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, lk.label)

    def _paint_ports(self, p: QPainter, lk: TopoLinkResult) -> None:
        """Port number at each end of a link, just outside the device; the module in that port is written
        along the line (like the hand-drawn schemes), so that neighbouring ports do not overlap."""
        if len(lk.points) < 2:
            return
        color = QColor(self.c.link(lk.kind, lk.room))
        bg = QColor(self.s.bg)
        bg.setAlphaF(0.85)
        fm_p, fm_m = QFontMetricsF(self.f_port), QFontMetricsF(self.f_module)
        ends = (
            (lk.points[0], lk.points[1], lk.port_a, lk.module_a, self.topo.node(lk.a)),
            (lk.points[-1], lk.points[-2], lk.port_b, lk.module_b, self.topo.node(lk.b)),
        )
        for (x0, y0), (x1, y1), port, module, node in ends:
            if node is not None and node.kind in ("odf", "panel"):
                continue  # the ODF's cell carries the port number
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
            length = fm_m.horizontalAdvance(module) + 4
            if upright and not vertical:  # a slanted line: the module along it, past the port number
                angle = math.degrees(math.atan2(y1 - y0, x1 - x0))
                d = math.hypot(x1 - x0, y1 - y0) or 1.0
                ux, uy = (x1 - x0) / d, (y1 - y0) / d
                p.save()
                p.translate(x0 + ux * 18, y0 + uy * 18)
                flip = abs(angle) > 90  # keep the text readable (never upside down)
                p.rotate(angle + 180 if flip else angle)
                rect = QRectF(-length, -12, length, 11) if flip else QRectF(0, -12, length, 11)
                self._tag(p, rect, module, self.f_module, color, bg)
                p.restore()
                continue
            if upright and vertical and y1 < y0 and lk.kind != "stack":
                # up from a switch's top: beside its port number, clear of the lines of the band above
                x = x0 + 4 + (fm_p.horizontalAdvance(port) + 8 if port else 0)
                self._tag(p, QRectF(x, y0 - 13, length, 11), module, self.f_module, color, bg)
                continue
            if not upright and len(lk.points) > 2:
                # sideways into a lane: written up the lane, on its outer side
                (lx, ly), (nx, ny) = (x1, y1), (lk.points[2] if (x0, y0) == lk.points[0] else lk.points[-3])
                if abs(nx - lx) < 0.5 and ny < ly:
                    p.save()
                    p.translate(lx, ly - 8)
                    p.rotate(-90)
                    rect = QRectF(0, -12, length, 11) if lx < x0 else QRectF(0, 1, length, 11)
                    self._tag(p, rect, module, self.f_module, color, bg)
                    p.restore()
                    continue
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

    def _icon(self, kind: str) -> QSvgRenderer:
        renderer = self._icons.get(kind)
        if renderer is None:
            renderer = QSvgRenderer(QByteArray(icon_svg(kind).encode()))
            self._icons[kind] = renderer
        return renderer

    def _paint_odf(self, p: QPainter, n: TopoNode) -> None:
        """An optical patch panel: its name, where its cable goes and the cable; then its duplex ports,
        numbered, each in the colour of the link through it (free ports grey, a note: a dot)."""
        fill, edge, text = self.c.node("odf")
        r = QRectF(n.rect.x, n.rect.y, n.rect.w, n.rect.h)
        if n.id in self.selected:
            halo = QColor(edge)
            halo.setAlphaF(0.3)
            p.setPen(QPen(halo, 6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 6, 6)
        p.setPen(QPen(QColor(edge), 1.4))
        p.setBrush(QColor(fill))
        p.drawRoundedRect(r, 5, 5)
        cap = QRectF(r.x() + 6, r.y() + 3, ODF_CAP_W - 10, r.height() - 6)
        rows = [(n.label, self.f_odf_name, QColor(text)), (n.caption, self.f_odf, QColor(text))]
        if n.cable:
            muted = QColor(text)
            muted.setAlphaF(0.7)
            rows.append((n.cable, self.f_odf, muted))
        row_h = cap.height() / len(rows)
        for i, (line, font, color) in enumerate(rows):
            p.setFont(font)
            p.setPen(color)
            fm = QFontMetricsF(font)
            p.drawText(
                QRectF(cap.x(), cap.y() + i * row_h, cap.width(), row_h),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                fm.elidedText(line, Qt.TextElideMode.ElideRight, cap.width()),
            )
        links = {lk.id: lk for lk in self.topo.links}
        bg = QColor(self.s.bg)
        p.setFont(self.f_cell)
        for port in range(1, n.shown_ports + 1):
            c = n.cell_rect(port)
            box = QRectF(c.x + 1.5, c.y, c.w - 3, c.h)
            lk = links.get(n.cells.get(port, ""))
            over = port > n.ports
            if lk is not None:
                color = QColor(self.c.bad if over else self.c.link(lk.kind, lk.room))
                p.setPen(QPen(color, 1.6))
                p.setBrush(bg)
            else:
                color = QColor(self.c.bad if over else self.c.free_cell)
                p.setPen(QPen(color, 1.0, Qt.PenStyle.DashLine if not over else Qt.PenStyle.SolidLine))
                p.setBrush(QColor(fill))
            p.drawRoundedRect(box, 3, 3)
            p.setPen(color if lk is not None or over else QColor(self.s.muted))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, str(port))
            if port in n.notes:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(self.c.note))
                p.drawEllipse(QPointF(box.right() - 2, box.top() + 2), 2.4, 2.4)

    def _paint_panel(self, p: QPainter, n: TopoNode) -> None:
        """A copper patch panel: its name and which switch ports its ports go to."""
        fill, edge, text = self.c.node("panel")
        r = QRectF(n.rect.x, n.rect.y, n.rect.w, n.rect.h)
        if n.id in self.selected:
            halo = QColor(edge)
            halo.setAlphaF(0.3)
            p.setPen(QPen(halo, 6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 6, 6)
        p.setPen(QPen(QColor(edge), 1.2))
        p.setBrush(QColor(fill))
        p.drawRoundedRect(r, 4, 4)
        body = r.adjusted(6, 1, -6, -1)
        two = r.height() >= 26
        p.setPen(QColor(text))
        p.setFont(self.f_odf_name)
        fm = QFontMetricsF(self.f_odf_name)
        if two:
            half = QRectF(body.x(), body.y(), body.width(), body.height() / 2)
            p.drawText(
                half, Qt.AlignmentFlag.AlignCenter, fm.elidedText(n.label, Qt.TextElideMode.ElideRight, body.width())
            )
            p.setFont(self.f_odf)
            p.drawText(half.translated(0, half.height()), Qt.AlignmentFlag.AlignCenter, n.caption)
        else:
            line = f"{n.label} · {n.caption}"
            p.drawText(
                body, Qt.AlignmentFlag.AlignCenter, fm.elidedText(line, Qt.TextElideMode.ElideRight, body.width())
            )

    def _paint_node(self, p: QPainter, n: TopoNode) -> None:
        if n.kind == "odf":
            self._paint_odf(p, n)
            return
        if n.kind == "panel":
            self._paint_panel(p, n)
            return
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
        # the device's figure on the left, its texts beside it
        ih = min(ICON_H, r.height() - 10)
        iw = ih * ICON_W / ICON_H
        self._icon(n.kind).render(p, QRectF(r.x() + 8, r.center().y() - ih / 2, iw, ih))
        body = QRectF(r.x() + iw + 14, r.y(), r.width() - iw - 20, r.height())
        p.setPen(QColor(text))
        if n.kind == "wan" or not n.model:
            p.setFont(self.f_name)
            p.drawText(body, Qt.AlignmentFlag.AlignCenter, n.label)
            return
        fm = QFontMetricsF(self.f_name)
        name = fm.elidedText(n.label, Qt.TextElideMode.ElideMiddle, body.width())
        rows = 3 if n.endpoints else 2
        row_h = (r.height() - 8) / rows
        p.setFont(self.f_name)
        p.drawText(QRectF(body.x(), r.y() + 4, body.width(), row_h), Qt.AlignmentFlag.AlignCenter, name)
        muted = QColor(text)
        muted.setAlphaF(0.72)
        p.setPen(muted)
        p.setFont(self.f_model)
        p.drawText(QRectF(body.x(), r.y() + 4 + row_h, body.width(), row_h), Qt.AlignmentFlag.AlignCenter, n.model)
        if n.endpoints:
            p.setPen(QColor(self.c.link("fortilink")))
            p.setFont(self.f_endpoints)
            fe = QFontMetricsF(self.f_endpoints)
            p.drawText(
                QRectF(body.x(), r.y() + 4 + 2 * row_h, body.width(), row_h),
                Qt.AlignmentFlag.AlignCenter,
                fe.elidedText(n.endpoints, Qt.TextElideMode.ElideRight, body.width()),
            )


HOP_R = 5.0
"""Radius of the hop a line makes over a line drawn before it."""

Seg = tuple[tuple[float, float], tuple[float, float]]


def _segments(points: list[tuple[float, float]]) -> list[Seg]:
    return list(itertools.pairwise(points))


def _crossings(seg: Seg, under: list[Seg]) -> list[tuple[float, float]]:
    """Where an upright / level segment crosses the perpendicular ones of ``under``, along it
    (not near the ends, where lines meet their devices)."""
    (x0, y0), (x1, y1) = seg
    level = abs(y1 - y0) < 0.5
    if not level and abs(x1 - x0) >= 0.5:
        return []
    out: list[tuple[float, float]] = []
    margin = HOP_R + 3
    for (a0, b0), (a1, b1) in under:
        if level and abs(a1 - a0) < 0.5:  # a level segment over an upright one
            x, lo, hi = a0, min(b0, b1), max(b0, b1)
            if min(x0, x1) + margin < x < max(x0, x1) - margin and lo + margin < y0 < hi - margin:
                out.append((x, y0))
        elif not level and abs(b1 - b0) < 0.5:  # an upright segment over a level one
            y, lo, hi = b0, min(a0, a1), max(a0, a1)
            if min(y0, y1) + margin < y < max(y0, y1) - margin and lo + margin < x0 < hi - margin:
                out.append((x0, y))
    out.sort(key=lambda pt: math.hypot(pt[0] - x0, pt[1] - y0))
    return out


def _hops(path: QPainterPath, seg: Seg, under: list[Seg]) -> None:
    """Half circles over the lines ``seg`` crosses (the path is at the segment's start)."""
    (x0, y0), (x1, y1) = seg
    d = math.hypot(x1 - x0, y1 - y0) or 1.0
    ux, uy = (x1 - x0) / d, (y1 - y0) / d
    nx, ny = (0.0, -1.0) if abs(uy) < 0.5 else (1.0, 0.0)  # up over a level line, right of an upright one
    bulge = HOP_R * 1.33
    last = -1e9
    for cx, cy in _crossings(seg, under):
        k = math.hypot(cx - x0, cy - y0)
        if k - last < 2 * HOP_R + 2:
            continue  # lines side by side: one hop over both
        last = k
        sx, sy = cx - ux * HOP_R, cy - uy * HOP_R
        ex, ey = cx + ux * HOP_R, cy + uy * HOP_R
        path.lineTo(sx, sy)
        path.cubicTo(sx + nx * bulge, sy + ny * bulge, ex + nx * bulge, ey + ny * bulge, ex, ey)


def _rounded(points: list[tuple[float, float]], radius: float = 8.0, under: list[Seg] | None = None) -> QPainterPath:
    """The line through ``points`` with rounded corners, hopping over the lines of ``under``."""
    path = QPainterPath(QPointF(*points[0]))
    under = under or []
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
        cur = path.currentPosition()
        _hops(path, ((cur.x(), cur.y()), (ax, ay)), under)
        path.lineTo(ax, ay)
        path.quadTo(x1, y1, bx, by)
    cur = path.currentPosition()
    _hops(path, ((cur.x(), cur.y()), points[-1]), under)
    path.lineTo(QPointF(*points[-1]))
    return path


def _seg_dist(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    k = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    qx, qy = x1 + k * dx, y1 + k * dy
    return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5
