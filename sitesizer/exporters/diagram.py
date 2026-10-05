"""Topology diagram renderer (QPainter).

One renderer draws the live in-app diagram, the PNG/SVG/PDF exports and the image embedded in
Excel, so what you see is exactly what you export. Style follows the prototype: navy firewall and
core boxes, light switch boxes, thin connectors, muted italic endpoint captions — flat, calm,
no gradients. Exports always use the light palette on a white background.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QMarginsF, QPointF, QRectF, QSize, QSizeF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtSvg import QSvgGenerator

from ..core.catalog import Catalog
from ..core.models import SiteResult
from ..i18n import Translator

FONT_FAMILIES = ["Inter", "Segoe UI Variable Text", "Segoe UI", "Noto Sans", "Arial"]


@dataclass(frozen=True)
class DiagramStyle:
    bg: str = "#FFFFFF"
    dark: str = "#1F4E78"
    dark_text: str = "#FFFFFF"
    light: str = "#EAF0F6"
    light_edge: str = "#B7C4D1"
    light_text: str = "#1F2D3A"
    line: str = "#9AA7B4"
    muted: str = "#7C8894"
    wan: str = "#4C9A6A"
    accent_soft: str = "#DCE6F0"


EXPORT_STYLE = DiagramStyle()


def style_from_tokens(t: object) -> DiagramStyle:
    g = lambda name: str(getattr(t, name))  # noqa: E731
    return DiagramStyle(
        bg=g("dia_bg"),
        dark=g("dia_dark"),
        dark_text=g("dia_dark_text"),
        light=g("dia_light"),
        light_edge=g("dia_light_edge"),
        light_text=g("dia_light_text"),
        line=g("dia_line"),
        muted=g("dia_muted"),
        wan=g("dia_wan"),
        accent_soft=g("dia_light"),
    )


def _font(px: float, weight: QFont.Weight = QFont.Weight.Normal, italic: bool = False, spacing: float = 0.0) -> QFont:
    f = QFont(FONT_FAMILIES[0])
    f.setFamilies(FONT_FAMILIES)
    f.setPixelSize(max(1, round(px)))
    f.setWeight(weight)
    f.setItalic(italic)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


@dataclass
class _Box:
    rect: QRectF
    title: str
    line: str
    dark: bool
    stacked: bool = False
    caption: str = ""
    uplink: str = ""


class SiteDiagram:
    """Hierarchical diagram of one location."""

    PAD = 36.0
    ROW_GAP = 56.0
    COL_GAP = 28.0
    BOX_H = 62.0

    def __init__(
        self,
        result: SiteResult,
        catalog: Catalog,
        lang: str = "uk",
        style: DiagramStyle = EXPORT_STYLE,
        title: bool = False,
    ) -> None:
        ensure_gui_app()
        self.r = result
        self.catalog = catalog
        self.t = Translator(lang)
        self.s = style
        self.show_title = title
        self.f_label = _font(10.5, QFont.Weight.DemiBold, spacing=0.6)
        self.f_model = _font(14, QFont.Weight.DemiBold)
        self.f_caption = _font(12, italic=True)
        self.f_small = _font(11)
        self.f_title = _font(18, QFont.Weight.DemiBold)
        self._layout()

    # ---- layout --------------------------------------------------------------------------
    def _layout(self) -> None:
        t, r = self.t, self.r
        tier = self.catalog.tier(r.tier_id)
        self.boxes: list[_Box] = []
        self.edges: list[tuple[QPointF, QPointF, str]] = []
        self.wan: list[tuple[QRectF, str]] = []
        self.oob: QRectF | None = None
        fm_model = QFontMetricsF(self.f_model)
        fm_label = QFontMetricsF(self.f_label)

        def box_w(title: str, line: str, minimum: float) -> float:
            return max(minimum, fm_model.horizontalAdvance(line) + 40, fm_label.horizontalAdvance(title) + 40)

        edge_boxes: list[_Box] = []
        for key in ("wifi_switch", "access_switch", "camera_switch"):
            c = r.categories[key]
            if not c.count:
                continue
            title = self.t.t(f"dia.{key}")
            line = f"{c.model} × {c.count}"
            caption = {
                "wifi_switch": t.plural("plural.aps", r.counts.aps),
                "access_switch": t.plural("plural.sockets", r.counts.sockets),
                "camera_switch": t.plural("plural.cameras", r.counts.cameras),
            }[key]
            dev = self.catalog.models.get(c.model)
            uplink = ""
            if dev and dev.uplinks:
                speed = min(dev.uplinks.speed_gbps, 10.0) if (r.core.count or self._fw_10g()) else 1.0
                uplink = f"{c.uplinks_per_switch}× {speed:g}G" if c.uplinks_per_switch > 1 else f"{speed:g}G"
            edge_boxes.append(
                _Box(
                    QRectF(0, 0, box_w(title, line, 196), self.BOX_H), title, line, False, c.count > 1, caption, uplink
                )
            )

        y = self.PAD
        if self.show_title:
            y += 44
        has_fw = r.firewall is not None
        dual_wan = has_fw and tier.dual_wan
        if has_fw:
            y += 40  # WAN row
        widths = [b.rect.width() for b in edge_boxes]
        row_w = sum(widths) + self.COL_GAP * max(len(widths) - 1, 0)
        fw_line = ""
        fw_title = t.t("dia.firewall")
        if has_fw and r.firewall:
            fw_line = f"{r.firewall.model} × {r.firewall.count}" + (
                " (HA)" if tier.fw_ha and r.firewall.count > 1 else ""
            )
        core_title = t.t("dia.core")
        core_line = f"{r.core.model} × {r.core.count}" + (" (N+1)" if tier.core_redundant and r.core.count > 1 else "")
        fw_w = box_w(fw_title, fw_line, 248) if has_fw else 0
        core_w = box_w(core_title, core_line, 232) if r.core.count else 0
        oob_w = 132.0 if tier.oob and has_fw else 0.0
        content_w = max(row_w, fw_w + (oob_w + 48) * 2, core_w, 560)
        self.width = content_w + self.PAD * 2
        cx = self.width / 2

        fw_box: _Box | None = None
        if has_fw:
            wan_labels = ["WAN 1", "WAN 2 · SD-WAN"] if dual_wan else ["WAN"]
            fm_s = QFontMetricsF(self.f_small)
            pills = [QSizeF(fm_s.horizontalAdvance(lbl) + 22, 22) for lbl in wan_labels]
            total = sum(p.width() for p in pills) + 16 * (len(pills) - 1)
            x = cx - total / 2
            for size, lbl in zip(pills, wan_labels, strict=True):
                self.wan.append((QRectF(x, y - 40, size.width(), size.height()), lbl))
                x += size.width() + 16
            fw_box = _Box(
                QRectF(cx - fw_w / 2, y, fw_w, self.BOX_H + 4),
                fw_title,
                fw_line,
                True,
                bool(r.firewall and r.firewall.count > 1),
            )
            self.boxes.append(fw_box)
            if oob_w:
                self.oob = QRectF(fw_box.rect.right() + 48, y + (self.BOX_H + 4 - 40) / 2, oob_w, 40)
            y += self.BOX_H + 4 + self.ROW_GAP

        parent = fw_box
        if r.core.count:
            core_box = _Box(
                QRectF(cx - core_w / 2, y, core_w, self.BOX_H), core_title, core_line, True, r.core.count > 1
            )
            self.boxes.append(core_box)
            if fw_box:
                self.edges.append((QPointF(cx, fw_box.rect.bottom()), QPointF(cx, core_box.rect.top()), ""))
            parent = core_box
            y += self.BOX_H + self.ROW_GAP

        if edge_boxes:
            x = cx - row_w / 2
            for b in edge_boxes:
                b.rect.moveTo(x, y)
                x += b.rect.width() + self.COL_GAP
                self.boxes.append(b)
                if parent:
                    self.edges.append(
                        (QPointF(cx, parent.rect.bottom()), QPointF(b.rect.center().x(), b.rect.top()), b.uplink)
                    )
            y += self.BOX_H + 30  # captions
        self.footnotes = []
        if tier.ups or r.addons.get("ups"):
            self.footnotes.append(t.t("dia.ups"))
        if tier.oob:
            self.footnotes.append(t.t("dia.oob"))
        self.tier_text = t.t("dia.tier", n=r.tier_id, label=t.pick(tier.label))
        y += 22
        if self.footnotes:
            y += 4
        self.height = y + self.PAD
        self.empty = not self.boxes
        if self.empty:
            self.width, self.height = 420.0, 120.0

    def _fw_10g(self) -> bool:
        fw = self.r.firewall
        if not fw or not fw.fits:
            return False
        dev = self.catalog.models.get(fw.model)
        return bool(dev and dev.firewall and dev.firewall.ports_10g > 0)

    def size(self) -> QSizeF:
        return QSizeF(self.width, self.height)

    # ---- painting ------------------------------------------------------------------------
    def paint(self, p: QPainter, background: bool = True) -> None:
        s = self.s
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if background:
            p.fillRect(QRectF(0, 0, self.width, self.height), QColor(s.bg))
        if self.show_title:
            p.setPen(QColor(s.light_text if s.bg != "#FFFFFF" else "#1F2D3A"))
            p.setFont(self.f_title)
            p.drawText(
                QRectF(self.PAD, self.PAD - 6, self.width - 2 * self.PAD, 30),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self.r.input.name,
            )
        if self.empty:
            p.setPen(QColor(s.muted))
            p.setFont(self.f_caption)
            p.drawText(QRectF(0, 0, self.width, self.height), Qt.AlignmentFlag.AlignCenter, self.t.t("dia.empty"))
            return
        # WAN pills + links
        fw_box = self.boxes[0] if self.boxes and self.boxes[0].title == self.t.t("dia.firewall") else None
        for rect, text in self.wan:
            if fw_box:
                pen = QPen(QColor(s.wan), 1.4)
                p.setPen(pen)
                p.drawLine(
                    QPointF(rect.center().x(), rect.bottom()),
                    QPointF(rect.center().x(), fw_box.rect.top() - (6 if fw_box.stacked else 0)),
                )
            p.setPen(QPen(QColor(s.wan), 1.2))
            p.setBrush(QColor(s.bg))
            p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            p.setPen(QColor(s.wan))
            p.setFont(self.f_small)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        # edges (orthogonal bus with rounded corners)
        line_pen = QPen(QColor(s.line), 1.3)
        line_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        for a, b, label in self.edges:
            path = QPainterPath(a)
            if abs(a.x() - b.x()) < 0.5:
                path.lineTo(b)
            else:
                mid = a.y() + (b.y() - a.y()) * 0.45
                rad = min(10.0, abs(b.x() - a.x()) / 2, (b.y() - mid) / 2)
                sign = 1 if b.x() > a.x() else -1
                path.lineTo(a.x(), mid - rad)
                path.quadTo(a.x(), mid, a.x() + sign * rad, mid)
                path.lineTo(b.x() - sign * rad, mid)
                path.quadTo(b.x(), mid, b.x(), mid + rad)
                path.lineTo(b)
            p.setPen(line_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            if label:
                p.setFont(self.f_small)
                p.setPen(QColor(s.muted))
                fm = QFontMetricsF(self.f_small)
                lw = fm.horizontalAdvance(label)
                p.drawText(QRectF(b.x() + 6, b.y() - 22, lw + 4, 16), Qt.AlignmentFlag.AlignLeft, label)
        # OOB side box
        if self.oob and fw_box:
            dash = QPen(QColor(s.muted), 1.1, Qt.PenStyle.DashLine)
            p.setPen(dash)
            p.drawLine(
                QPointF(fw_box.rect.right(), self.oob.center().y()), QPointF(self.oob.left(), self.oob.center().y())
            )
            p.setBrush(QColor(s.bg))
            p.drawRoundedRect(self.oob, 8, 8)
            p.setPen(QColor(s.muted))
            p.setFont(self.f_small)
            p.drawText(self.oob, Qt.AlignmentFlag.AlignCenter, self.t.t("dia.oob_box"))
        for box in self.boxes:
            self._paint_box(p, box)
        # footer
        p.setFont(self.f_caption)
        p.setPen(QColor(s.muted))
        parts = [self.tier_text, *self.footnotes]
        p.drawText(
            QRectF(0, self.height - self.PAD - 18, self.width, 20), Qt.AlignmentFlag.AlignCenter, "   •   ".join(parts)
        )

    def _paint_box(self, p: QPainter, b: _Box) -> None:
        s = self.s
        fill = QColor(s.dark if b.dark else s.light)
        edge = QColor(s.dark if b.dark else s.light_edge)
        text = QColor(s.dark_text if b.dark else s.light_text)
        radius = 9.0
        if b.stacked:  # a second card peeking out = "more than one unit"
            back = b.rect.translated(5, -5)
            c = QColor(fill)
            c.setAlphaF(0.38 if b.dark else 1.0)
            p.setPen(QPen(edge, 1.0) if not b.dark else Qt.PenStyle.NoPen)
            p.setBrush(c if b.dark else QColor(s.bg))
            p.drawRoundedRect(back, radius, radius)
        p.setPen(QPen(edge, 1.2))
        p.setBrush(fill)
        p.drawRoundedRect(b.rect, radius, radius)
        top = QRectF(b.rect.left(), b.rect.top() + 11, b.rect.width(), 16)
        bottom = QRectF(b.rect.left(), b.rect.top() + 29, b.rect.width(), 22)
        lab = QColor(text)
        lab.setAlphaF(0.78)
        p.setPen(lab)
        p.setFont(self.f_label)
        p.drawText(top, Qt.AlignmentFlag.AlignCenter, b.title)
        p.setPen(text)
        p.setFont(self.f_model)
        p.drawText(bottom, Qt.AlignmentFlag.AlignCenter, b.line)
        if b.caption:
            p.setPen(QColor(s.muted))
            p.setFont(self.f_caption)
            p.drawText(
                QRectF(b.rect.left() - 20, b.rect.bottom() + 8, b.rect.width() + 40, 18),
                Qt.AlignmentFlag.AlignCenter,
                b.caption,
            )


class ProjectDiagram:
    """Hub-and-spoke view of all locations in a project (HQ on top, WAN/VPN links in green)."""

    PAD = 36.0
    CARD_W = 236.0
    CARD_H = 100.0
    GAP_X = 28.0
    GAP_Y = 36.0
    PER_ROW = 4

    def __init__(
        self,
        entries: list[tuple[str, bool, SiteResult]],
        catalog: Catalog,
        lang: str = "uk",
        style: DiagramStyle = EXPORT_STYLE,
        title: str = "",
    ) -> None:
        ensure_gui_app()
        self.entries = entries
        self.catalog = catalog
        self.t = Translator(lang)
        self.s = style
        self.title = title
        self.f_name = _font(14, QFont.Weight.DemiBold)
        self.f_small = _font(11.5)
        self.f_label = _font(10.5, QFont.Weight.DemiBold, spacing=0.6)
        self.f_title = _font(18, QFont.Weight.DemiBold)
        hubs = [e for e in entries if e[1]] or entries[:1]
        self.hub = hubs[0] if hubs else None
        self.spokes = [e for e in entries if e is not self.hub]
        n = len(self.spokes)
        per_row = min(self.PER_ROW, max(n, 1))
        self.width = max(per_row * self.CARD_W + (per_row - 1) * self.GAP_X, self.CARD_W + 80) + 2 * self.PAD
        top = self.PAD + (44 if title else 0)
        self.hub_rect = QRectF(self.width / 2 - (self.CARD_W + 30) / 2, top, self.CARD_W + 30, self.CARD_H + 6)
        y0 = self.hub_rect.bottom() + 90
        self.spoke_rects: list[QRectF] = []
        for i in range(n):
            row, col = divmod(i, per_row)
            in_row = min(per_row, n - row * per_row)
            row_w = in_row * self.CARD_W + (in_row - 1) * self.GAP_X
            x = self.width / 2 - row_w / 2 + col * (self.CARD_W + self.GAP_X)
            self.spoke_rects.append(QRectF(x, y0 + row * (self.CARD_H + self.GAP_Y), self.CARD_W, self.CARD_H))
        bottom = self.spoke_rects[-1].bottom() if self.spoke_rects else self.hub_rect.bottom()
        self.height = bottom + self.PAD + 24

    def size(self) -> QSizeF:
        return QSizeF(self.width, self.height)

    def paint(self, p: QPainter, background: bool = True) -> None:
        s = self.s
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if background:
            p.fillRect(QRectF(0, 0, self.width, self.height), QColor(s.bg))
        if self.title:
            p.setFont(self.f_title)
            p.setPen(QColor(s.light_text))
            p.drawText(QRectF(self.PAD, self.PAD - 6, self.width, 30), Qt.AlignmentFlag.AlignLeft, self.title)
        if not self.hub:
            return
        pen = QPen(QColor(s.wan), 1.6)
        for rect in self.spoke_rects:
            a = QPointF(self.hub_rect.center().x(), self.hub_rect.bottom())
            b = QPointF(rect.center().x(), rect.top())
            path = QPainterPath(a)
            path.cubicTo(QPointF(a.x(), a.y() + 60), QPointF(b.x(), b.y() - 60), b)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        self._card(p, self.hub_rect, self.hub, hub=True)
        for rect, entry in zip(self.spoke_rects, self.spokes, strict=True):
            self._card(p, rect, entry, hub=False)
        p.setFont(_font(11.5, italic=True))
        p.setPen(QColor(s.muted))
        p.drawText(
            QRectF(0, self.height - self.PAD - 6, self.width, 18),
            Qt.AlignmentFlag.AlignCenter,
            self.t.t("dia.wan_legend"),
        )

    def _card(self, p: QPainter, rect: QRectF, entry: tuple[str, bool, SiteResult], hub: bool) -> None:
        s, t = self.s, self.t
        name, _is_hub, r = entry
        dark = hub
        fill = QColor(s.dark if dark else s.light)
        text = QColor(s.dark_text if dark else s.light_text)
        p.setPen(QPen(QColor(s.dark if dark else s.light_edge), 1.2))
        p.setBrush(fill)
        p.drawRoundedRect(rect, 10, 10)
        inner = rect.adjusted(14, 10, -14, -10)
        lab = QColor(text)
        lab.setAlphaF(0.75)
        p.setPen(lab)
        p.setFont(self.f_label)
        tier = self.catalog.tier(r.tier_id)
        head = (t.t("dia.hub") + " · " if hub else "") + t.t("dia.tier_short", n=r.tier_id, label=t.pick(tier.label))
        p.drawText(QRectF(inner.left(), inner.top(), inner.width(), 14), Qt.AlignmentFlag.AlignLeft, head.upper())
        p.setPen(text)
        p.setFont(self.f_name)
        fm = QFontMetricsF(self.f_name)
        p.drawText(
            QRectF(inner.left(), inner.top() + 18, inner.width(), 20),
            Qt.AlignmentFlag.AlignLeft,
            fm.elidedText(name, Qt.TextElideMode.ElideRight, inner.width()),
        )
        p.setFont(self.f_small)
        lines = []
        if r.firewall:
            lines.append(f"{r.firewall.model} × {r.firewall.count}")
        lines.append(t.t("dia.card_counts", sw=r.total_switches, ap=r.counts.aps))
        body = QColor(text)
        body.setAlphaF(0.85)
        p.setPen(body)
        for i, line in enumerate(lines):
            p.drawText(
                QRectF(inner.left(), inner.top() + 44 + i * 18, inner.width(), 18),
                Qt.AlignmentFlag.AlignLeft,
                QFontMetricsF(self.f_small).elidedText(line, Qt.TextElideMode.ElideRight, inner.width()),
            )


def paint_brand(p: QPainter, r: QRectF, color: str = "#1F4E78") -> None:
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(r, r.width() * 0.26, r.width() * 0.26)
    w = r.width()
    top = QRectF(r.left() + w * 0.36, r.top() + w * 0.2, w * 0.28, w * 0.2)
    left = QRectF(r.left() + w * 0.16, r.top() + w * 0.6, w * 0.24, w * 0.2)
    right = QRectF(r.left() + w * 0.6, r.top() + w * 0.6, w * 0.24, w * 0.2)
    pen = QPen(QColor(255, 255, 255, 170), max(1.0, w * 0.05))
    p.setPen(pen)
    mid_y = r.top() + w * 0.5
    p.drawLine(QPointF(top.center().x(), top.bottom()), QPointF(top.center().x(), mid_y))
    p.drawLine(QPointF(left.center().x(), mid_y), QPointF(right.center().x(), mid_y))
    p.drawLine(QPointF(left.center().x(), mid_y), QPointF(left.center().x(), left.top()))
    p.drawLine(QPointF(right.center().x(), mid_y), QPointF(right.center().x(), right.top()))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#FFFFFF"))
    for box in (top, left, right):
        p.drawRoundedRect(box, w * 0.05, w * 0.05)


Diagram = SiteDiagram | ProjectDiagram  # RackDiagram (exporters.rack) has the same interface


def ensure_gui_app() -> None:
    """Exports need a QGuiApplication (fonts); create an offscreen one for CLI use."""
    if QGuiApplication.instance() is None:
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QGuiApplication([])
        from PySide6.QtGui import QFontDatabase

        fonts = Path(__file__).resolve().parent.parent / "assets" / "fonts"
        for f in fonts.glob("*.ttf"):
            QFontDatabase.addApplicationFont(str(f))


def render_image(diagram: Diagram, scale: float = 2.0) -> QImage:
    ensure_gui_app()
    size = diagram.size()
    img = QImage(
        QSize(math.ceil(size.width() * scale), math.ceil(size.height() * scale)),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    img.setDevicePixelRatio(scale)
    img.fill(QColor(diagram.s.bg))
    p = QPainter(img)
    diagram.paint(p)
    p.end()
    return img


def export_png(diagram: Diagram, path: str | Path, scale: float = 2.0) -> Path:
    img = render_image(diagram, scale)
    if not img.save(str(path), "PNG"):  # type: ignore[call-overload]
        raise OSError(f"Cannot write {path}")
    return Path(path)


def export_svg(diagram: Diagram, path: str | Path, title: str = "") -> Path:
    ensure_gui_app()
    size = diagram.size()
    gen = QSvgGenerator()
    gen.setFileName(str(path))
    gen.setSize(QSize(math.ceil(size.width()), math.ceil(size.height())))
    gen.setViewBox(QRectF(0, 0, size.width(), size.height()))
    gen.setTitle(title or "Topology")
    gen.setDescription("SiteSizer topology diagram")
    p = QPainter(gen)
    diagram.paint(p)
    p.end()
    return Path(path)


def fit_rect(content: QSizeF, target: QRectF, margin: float = 0.0) -> tuple[float, QPointF]:
    """Scale and offset to fit ``content`` centred inside ``target``."""
    avail = target.marginsRemoved(QMarginsF(margin, margin, margin, margin))
    scale = min(avail.width() / content.width(), avail.height() / content.height()) if content.width() else 1.0
    offset = QPointF(
        avail.left() + (avail.width() - content.width() * scale) / 2,
        avail.top() + (avail.height() - content.height() * scale) / 2,
    )
    return scale, offset
