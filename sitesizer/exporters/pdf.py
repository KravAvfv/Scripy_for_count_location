"""Customer-ready PDF report (A4) rendered with Qt: header, summary, diagram, BoM, IP plan, notes."""

from __future__ import annotations

import html
from datetime import date
from pathlib import Path
from typing import Any

from PySide6.QtCore import QMarginsF, QPointF, QRectF, QSizeF, Qt, QUrl
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFont,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
    QTextDocument,
)

from .. import __version__
from ..core.catalog import Catalog
from ..core.models import Severity, SiteResult
from ..core.pricing import format_money, summarize_prices
from ..core.report import BOM_GROUP_ORDER
from ..i18n import Translator
from .diagram import FONT_FAMILIES, SiteDiagram, ensure_gui_app, paint_brand, render_image

NAVY = "#1F4E78"
LIGHT = "#EAF0F6"
BORDER = "#D5DEE7"
TEXT = "#1F2D3A"
MUTED = "#6A7682"
DPI = 300


def _e(text: object) -> str:
    return html.escape("" if text is None else str(text))


def _css() -> str:
    fam = ", ".join(f"'{f}'" for f in FONT_FAMILIES)
    return f"""
body {{ font-family: {fam}; font-size: 9pt; color: {TEXT}; }}
h1 {{ font-size: 17pt; font-weight: 600; margin: 0; color: {TEXT}; }}
h2 {{ font-size: 12pt; font-weight: 600; margin-top: 16pt; margin-bottom: 6pt; color: {NAVY}; }}
p {{ margin: 0 0 4pt 0; }}
.m {{ color: {MUTED}; }}
.small {{ font-size: 8pt; }}
table.grid {{ border-collapse: collapse; width: 100%; }}
table.grid th {{ background: {NAVY}; color: #FFFFFF; font-weight: 600; padding: 4pt 5pt; text-align: left; }}
table.grid td {{ border-bottom: 1px solid {BORDER}; padding: 4pt 5pt; vertical-align: top; }}
td.group {{ background: {LIGHT}; color: {NAVY}; font-weight: 600; }}
td.qty {{ text-align: center; font-weight: 600; }}
td.num {{ text-align: right; }}
td.ref {{ color: {MUTED}; font-style: italic; }}
table.tiles td {{ background: {LIGHT}; padding: 6pt 8pt; vertical-align: top; }}
.tl {{ color: {MUTED}; font-size: 7.5pt; }}
.tv {{ font-size: 11pt; font-weight: 600; }}
.err {{ color: #A3433B; }} .warn {{ color: #93651A; }} .info {{ color: #2F6690; }}
"""


def build_html(
    result: SiteResult,
    catalog: Catalog,
    t: Translator,
    opts: dict[str, Any],
    image_width: int = 500,
    rack_width: int = 0,
) -> str:
    tier = catalog.tier(result.tier_id)
    fw = result.firewall
    parts: list[str] = []
    # ---- summary tiles -----------------------------------------------------------------------
    tiles = [
        (t.t("pdf.t_tier"), f"{result.tier_id} · {t.pick(tier.label)}", t.pick(tier.sla)),
        (
            t.t("sum.firewall"),
            f"{fw.model} × {fw.count}" if fw else "—",
            t.t("pdf.t_fw_sub") if fw and fw.count > 1 else "",
        ),
        (
            t.t("sum.switches"),
            str(result.total_switches),
            t.t("ui.m_sw_sub", edge=result.edge_switch_count, core=result.core.count),
        ),
        (t.t("sum.aps"), str(result.counts.aps), ""),
        (
            t.t("sum.power"),
            f"{result.power.total_w:,.0f}".replace(",", " ") + t.t("ui.unit_w"),
            t.t("ui.m_power_sub", va=result.power.ups_va),
        ),
    ]
    tile_cells = "".join(
        f"<td width='20%'><span class='tl'>{_e(a)}</span><br><span class='tv'>{_e(b)}</span><br>"
        f"<span class='tl'>{_e(c)}</span></td>"
        for a, b, c in tiles
    )
    parts.append(f"<table class='tiles' width='100%' cellspacing='4'><tr>{tile_cells}</tr></table>")

    # ---- diagram -------------------------------------------------------------------------------
    parts.append(f"<h2>{_e(t.t('pdf.h_diagram'))}</h2>")
    parts.append(f"<p align='center'><img src='diagram://site' width='{image_width}'></p>")

    # ---- BoM -----------------------------------------------------------------------------------
    with_prices = any(line.unit_price is not None for line in result.bom)
    parts.append(f"<h2>{_e(t.t('pdf.h_bom'))}</h2>")
    head: list[str] = [t.t("col.model"), t.t("ui.col_qty_short"), t.t("col.reason")]
    if with_prices:
        head += [t.t("col.unit_price"), t.t("col.total_price")]
    widths = ["26%", "8%", "66%"] if not with_prices else ["22%", "7%", "45%", "13%", "13%"]
    rows = ["<tr>" + "".join(f"<th width='{w}'>{_e(h)}</th>" for h, w in zip(head, widths, strict=True)) + "</tr>"]
    order = {g: i for i, g in enumerate(BOM_GROUP_ORDER)}
    grouped: dict[str, list[Any]] = {}
    for line in result.bom:
        grouped.setdefault(line.group, []).append(line)
    cur = catalog.meta.currency
    for g in sorted(grouped, key=lambda g: order.get(g, 99)):
        rows.append(f"<tr><td class='group' colspan='{len(head)}'>{_e(t.t(f'group.{g}'))}</td></tr>")
        for line in grouped[g]:
            cls = " class='ref'" if line.is_reference else ""
            desc = f"<br><span class='m small'>{_e(line.description or line.category)}</span>"
            cells = [
                f"<td{cls}><b>{_e(line.model)}</b>{desc}</td>",
                f"<td class='qty'>{_e(line.qty if line.qty is not None else '—')}</td>",
                f"<td{cls}>{_e(line.reason)}</td>",
            ]
            if with_prices:
                cells += [
                    f"<td class='num'>{_e(format_money(line.unit_price, cur) if line.unit_price is not None else '')}</td>",
                    f"<td class='num'>{_e(format_money(line.total_price, cur) if line.total_price is not None else '')}</td>",
                ]
            rows.append("<tr>" + "".join(cells) + "</tr>")
    parts.append("<table class='grid' cellspacing='0'>" + "".join(rows) + "</table>")
    if with_prices:
        s = summarize_prices(
            result.bom, cur, float(opts.get("discount_pct", 0) or 0), float(opts.get("vat_pct", 0) or 0)
        )
        if s:
            items = [(t.t("price.subtotal"), s.subtotal)]
            if s.discount_pct:
                items.append((t.t("price.discount", pct=f"{s.discount_pct:g}"), -s.discount))
            if s.vat_pct:
                items.append((t.t("price.vat", pct=f"{s.vat_pct:g}"), s.vat))
            items.append((t.t("price.total"), s.total))
            trs = "".join(
                f"<tr><td class='num'>{_e(k)}</td><td class='num' width='22%'><b>{_e(format_money(v, cur))}"
                f"</b></td></tr>"
                for k, v in items
            )
            parts.append(f"<table width='100%' cellspacing='0'>{trs}</table>")
            if not s.complete:
                parts.append(f"<p class='m small'>{_e(t.t('price.incomplete', n=s.unpriced_lines))}</p>")

    # ---- IP plan -------------------------------------------------------------------------------
    plan = result.ip_plan
    if plan is not None and plan.segments:
        parts.append(f"<h2>{_e(t.t('pdf.h_ip'))}</h2>")
        with_addr = bool(plan.base_network) and not plan.error
        head = [t.t("col.segment"), "VLAN", t.t("col.hosts"), t.t("col.prefix")]
        if with_addr:
            head += [t.t("col.network"), t.t("col.gateway"), t.t("col.dhcp")]
        rows = ["<tr>" + "".join(f"<th>{_e(h)}</th>" for h in head) + "</tr>"]
        for sgm in plan.segments:
            vals = [sgm.name, sgm.vlan, sgm.hosts, f"/{sgm.prefix} ({sgm.capacity})"]
            if with_addr:
                vals += [sgm.network, sgm.gateway, sgm.dhcp_range]
            rows.append("<tr>" + "".join(f"<td>{_e(v)}</td>" for v in vals) + "</tr>")
        parts.append("<table class='grid' cellspacing='0'>" + "".join(rows) + "</table>")
        parts.append(f"<p class='m small'>{_e(t.t('ip.note', pct=round(catalog.rules.ip.buffer * 100)))}</p>")

    # ---- power & rack ---------------------------------------------------------------------------
    parts.append(f"<h2>{_e(t.t('pdf.h_power'))}</h2>")
    pw, rk = result.power, result.rack
    parts.append(
        f"<p>{_e(t.t('pdf.power_line', eq=round(pw.equipment_w), poe=round(pw.poe_w), total=round(pw.total_w), va=pw.ups_va, btu=round(pw.heat_btu)))}</p>"
    )
    if rk.plans:
        parts.append(
            f"<p>{_e(t.t('ui.rack_summary', used=rk.units_total, size=rk.rack_size_u, model=f'{rk.rack_model} × {rk.rack_count}', idf=rk.idf_count))}</p>"
        )

    # ---- racks ---------------------------------------------------------------------------------
    if result.rack.plans and rack_width:
        parts.append(f"<h2>{_e(t.t('pdf.h_racks'))}</h2>")
        parts.append(f"<p align='center'><img src='diagram://racks' width='{rack_width}'></p>")

    # ---- notes & checks ------------------------------------------------------------------------
    notable = [
        c
        for c in result.checks
        if c.severity != Severity.INFO
        or c.code
        in ("PSU_CONFIRM", "FW_FORTIOS", "BT_UPGRADE", "POE_AUTOSCALE", "DUAL_WAN", "UNVERIFIED", "CORE_PORTS")
    ]
    if notable or result.input.notes:
        parts.append(f"<h2>{_e(t.t('pdf.h_notes'))}</h2>")
        if result.input.notes:
            parts.append(f"<p>{_e(result.input.notes)}</p>")
        for c in notable:
            cls = {"error": "err", "warning": "warn", "info": "info"}[c.severity.value]
            parts.append(
                f"<p><span class='{cls}'>●</span> {_e(c.message)}"
                + (f" <span class='m'>{_e(c.hint)}</span>" if c.hint else "")
                + "</p>"
            )

    parts.append(f"<h2>{_e(t.t('pdf.h_assumptions'))}</h2>")
    for key in ("pdf.a1", "pdf.a2", "pdf.a3", "pdf.a4"):
        parts.append(f"<p class='m small'>• {_e(t.t(key, updated=catalog.meta.updated))}</p>")
    return "".join(parts)


def export_pdf(
    result: SiteResult,
    path: str | Path,
    catalog: Catalog | None = None,
    lang: str = "uk",
    options: dict[str, Any] | None = None,
) -> Path:
    ensure_gui_app()
    from ..core.catalog import load_default_catalog

    catalog = catalog or load_default_catalog()
    t = Translator(lang)
    opts = options or {}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    writer = QPdfWriter(str(path))
    writer.setResolution(DPI)
    writer.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Portrait,
            QMarginsF(14, 14, 14, 14),
            QPageLayout.Unit.Millimeter,
        )
    )
    writer.setTitle(t.t("xl.title", site=result.input.name))
    writer.setCreator(f"SiteSizer {__version__}")
    painter = QPainter(writer)
    if not painter.isActive():
        raise OSError(f"Cannot write {path}")
    page_rect = writer.pageLayout().paintRectPixels(DPI)
    pw, ph = float(page_rect.width()), float(page_rect.height())
    mm = DPI / 25.4

    header_h = 30 * mm
    footer_h = 8 * mm
    body_h = ph - header_h - footer_h

    doc = QTextDocument()
    doc.setDocumentMargin(0)
    base = QFont(FONT_FAMILIES[0])
    base.setFamilies(FONT_FAMILIES)
    base.setPointSizeF(9)
    doc.setDefaultFont(base)
    doc.setDefaultStyleSheet(_css())
    # diagram resource (rendered at print resolution)
    diagram = SiteDiagram(result, catalog, lang, title=False)
    img = render_image(diagram, scale=2.4)
    doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl("diagram://site"), img)
    # QTextDocument lays out in its own units; scale so 1 unit == 1 point on the page.
    scale = DPI / 72.0
    doc.setPageSize(QSizeF(pw / scale, body_h / scale))
    doc_w = pw / scale
    img_w = int(min(doc_w - 8, img.width() / img.devicePixelRatio()))
    rack_w = 0
    if result.rack.plans:
        from .rack import RackDiagram

        rack_img = render_image(RackDiagram(result, catalog, lang), scale=2.4)  # type: ignore[arg-type]
        doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl("diagram://racks"), rack_img)
        natural_w = rack_img.width() / rack_img.devicePixelRatio()
        natural_h = rack_img.height() / rack_img.devicePixelRatio()
        # fit the page width and ~85% of the body height so a cabinet never splits across pages
        max_h = body_h / scale * 0.85
        rack_w = int(min(doc_w - 8, natural_w, natural_w * max_h / natural_h))
    doc.setHtml(f"<html><body>{build_html(result, catalog, t, opts, img_w, rack_w)}</body></html>")
    pages = max(1, doc.pageCount())

    logo = QImage(str(opts["logo"])) if opts.get("logo") and Path(str(opts["logo"])).exists() else QImage()
    for page in range(pages):
        if page:
            writer.newPage()
        _draw_header(painter, t, result, opts, pw, header_h, mm, logo, first=page == 0)
        painter.save()
        painter.translate(0, header_h)
        painter.scale(scale, scale)
        clip = QRectF(0, page * body_h / scale, pw / scale, body_h / scale)
        painter.translate(0, -clip.top())
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.clip = clip
        painter.setClipRect(clip)
        doc.documentLayout().draw(painter, ctx)
        painter.restore()
        _draw_footer(painter, t, opts, pw, ph, footer_h, page + 1, pages)
    painter.end()
    return path


def _font(pt: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(FONT_FAMILIES[0])
    f.setFamilies(FONT_FAMILIES)
    f.setPointSizeF(pt)
    f.setWeight(weight)
    return f


def _draw_header(
    p: QPainter,
    t: Translator,
    result: SiteResult,
    opts: dict[str, Any],
    pw: float,
    h: float,
    mm: float,
    logo: QImage,
    first: bool,
) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    x = 0.0
    if not logo.isNull():
        lh = 12 * mm
        lw = lh * logo.width() / max(logo.height(), 1)
        p.drawImage(QRectF(0, 0, lw, lh), logo)
        x = lw + 5 * mm
    else:
        paint_brand(p, QRectF(0, 0, 11 * mm, 11 * mm), NAVY)
        x = 15 * mm
    from PySide6.QtGui import QFontMetricsF

    p.setPen(QColor(MUTED))
    p.setFont(_font(7.5, QFont.Weight.DemiBold))
    p.drawText(
        QRectF(x, 0, pw - x, 4.5 * mm),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        t.t("pdf.kicker").upper(),
    )
    p.setPen(QColor(TEXT))
    title_font = _font(15 if first else 12, QFont.Weight.DemiBold)
    p.setFont(title_font)
    name = QFontMetricsF(title_font).elidedText(result.input.name, Qt.TextElideMode.ElideRight, pw - x)
    p.drawText(QRectF(x, 4.5 * mm, pw - x, 8 * mm), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
    p.setPen(QColor(MUTED))
    p.setFont(_font(8.5))
    meta = [
        x_
        for x_ in (
            opts.get("company"),
            opts.get("project"),
            opts.get("customer") and t.t("pdf.for_customer", c=opts.get("customer")),
            date.today().strftime("%d.%m.%Y"),
            opts.get("author"),
        )
        if x_
    ]
    p.drawText(
        QRectF(x, 13 * mm, pw - x, 6 * mm),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        "  ·  ".join(str(m) for m in meta),
    )
    p.setPen(QColor(BORDER))
    p.drawLine(QPointF(0, h - 5 * mm), QPointF(pw, h - 5 * mm))
    p.restore()


def _draw_footer(
    p: QPainter, t: Translator, opts: dict[str, Any], pw: float, ph: float, h: float, page: int, pages: int
) -> None:
    p.save()
    p.setPen(QColor(MUTED))
    p.setFont(_font(7.5))
    left = f"SiteSizer {__version__}" + (f"  ·  {opts['project']}" if opts.get("project") else "")
    p.drawText(QRectF(0, ph - h, pw / 2, h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, left)
    p.drawText(
        QRectF(pw / 2, ph - h, pw / 2, h),
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
        t.t("pdf.page", n=page, total=pages),
    )
    p.restore()
