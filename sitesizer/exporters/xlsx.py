"""Excel export: BoM, IP plan, diagram and inputs, styled like the prototype but more polished."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .. import __version__
from ..core.catalog import Catalog
from ..core.models import SiteResult
from ..core.pricing import summarize_prices
from ..core.report import BOM_GROUP_ORDER, ip_columns, ip_rows
from ..i18n import Translator

NAVY = "1F4E78"
LIGHT = "EAF0F6"
BORDER = "B7B7B7"
MUTED = "7C8894"
TEXT = "1F2D3A"
FONT = "Calibri"

THIN = Side(style="thin", color=BORDER)
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEADER_FILL = PatternFill("solid", start_color=NAVY, end_color=NAVY)
GROUP_FILL = PatternFill("solid", start_color=LIGHT, end_color=LIGHT)
WRAP_TOP = Alignment(vertical="top", wrap_text=True)
CENTER = Alignment(horizontal="center", vertical="top", wrap_text=True)


class _PngImage(XLImage):
    """openpyxl image from PNG bytes without requiring Pillow."""

    def __init__(self, data: bytes, width: int, height: int) -> None:
        self.ref = None
        self._bytes = data
        self.width = width
        self.height = height
        self.format = "png"

    def _data(self) -> bytes:
        return self._bytes


def _title(ws: Worksheet, text: str, subtitle: str, ncols: int) -> int:
    ws.cell(row=1, column=1, value=text).font = Font(name=FONT, size=15, bold=True, color=TEXT)
    ws.cell(row=2, column=1, value=subtitle).font = Font(name=FONT, size=10, color=MUTED)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
    ws.row_dimensions[1].height = 24
    return 4


def _header(ws: Worksheet, row: int, columns: list[str]) -> None:
    for c, name in enumerate(columns, start=1):
        cell = ws.cell(row=row, column=c, value=name)
        cell.fill = HEADER_FILL
        cell.font = Font(name=FONT, color="FFFFFF", bold=True, size=11)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BOX
    ws.row_dimensions[row].height = 30


def _autofit(ws: Worksheet, first_row: int, ncols: int, caps: dict[int, int] | None = None) -> None:
    caps = caps or {}
    widths = [10] * ncols
    for row in ws.iter_rows(min_row=first_row, max_col=ncols):
        for cell in row:
            if cell.value is None or type(cell).__name__ == "MergedCell":
                continue
            longest = max((len(part) for part in str(cell.value).split("\n")), default=0)
            widths[cell.column - 1] = max(widths[cell.column - 1], longest + 2)
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = min(w, caps.get(i, 90))


def _row_height(texts: list[tuple[str, float]]) -> float:
    """Estimate a wrapped row height (Excel doesn't auto-size merged/wrapped rows reliably)."""
    lines = 1
    for text, width in texts:
        if not text:
            continue
        chars = max(int(width * 1.1), 1)
        est = sum(max(1, -(-len(part) // chars)) for part in str(text).split("\n"))
        lines = max(lines, est)
    return max(18.0, 15.0 * lines + 4)


def _print_setup(ws: Worksheet, header_row: int) -> None:
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{header_row}:{header_row}"
    ws.page_margins.left = ws.page_margins.right = 0.4


def export_xlsx(
    result: SiteResult,
    path: str | Path,
    catalog: Catalog | None = None,
    lang: str = "uk",
    options: dict[str, Any] | None = None,
) -> Path:
    """Write the workbook and return its path."""
    t = Translator(lang)
    opts = options or {}
    path = Path(path)
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "BoM"
    with_prices = any(line.unit_price is not None for line in result.bom)
    columns = [t.t("col.category"), t.t("col.model"), t.t("col.description"), t.t("col.qty"), t.t("col.reason")]
    if with_prices:
        columns += [t.t("col.unit_price"), t.t("col.total_price")]
    ncols = len(columns)
    meta = [
        x
        for x in (
            opts.get("project"),
            opts.get("customer"),
            date.today().isoformat(),
            opts.get("author"),
            opts.get("company"),
        )
        if x
    ]
    row = _title(ws, t.t("xl.title", site=result.input.name), "  ·  ".join(str(m) for m in meta), ncols)
    header_row = row
    _header(ws, row, columns)
    row += 1
    first_data = row
    order = {g: i for i, g in enumerate(BOM_GROUP_ORDER)}
    grouped: dict[str, list[Any]] = {}
    for line in result.bom:
        grouped.setdefault(line.group, []).append(line)
    widths = {1: 30, 2: 22, 3: 40, 4: 12, 5: 80}
    money = '#,##0.00 "₴"' if (catalog and catalog.meta.currency == "UAH") else "#,##0.00"
    for g in sorted(grouped, key=lambda g: order.get(g, 99)):
        cell = ws.cell(row=row, column=1, value=t.t(f"group.{g}"))
        cell.font = Font(name=FONT, bold=True, color=NAVY)
        for c in range(1, ncols + 1):
            ws.cell(row=row, column=c).fill = GROUP_FILL
            ws.cell(row=row, column=c).border = BOX
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
        row += 1
        for line in grouped[g]:
            values: list[Any] = [
                line.category,
                line.model,
                line.description,
                line.qty if line.qty is not None else "—",
                line.reason,
            ]
            if with_prices:
                values += [line.unit_price, line.total_price]
            for c, v in enumerate(values, start=1):
                cell = ws.cell(row=row, column=c, value=v)
                cell.border = BOX
                cell.alignment = CENTER if c == 4 else WRAP_TOP
                cell.font = Font(
                    name=FONT,
                    size=10,
                    italic=line.is_reference,
                    color=MUTED if line.is_reference else TEXT,
                    bold=(c == 2),
                )
                if c >= 6:
                    cell.number_format = money
            ws.row_dimensions[row].height = _row_height(
                [(line.reason, widths[5]), (line.description, widths[3]), (line.category, widths[1])]
            )
            row += 1
    if with_prices:
        s = summarize_prices(
            result.bom,
            catalog.meta.currency if catalog else "UAH",
            float(opts.get("discount_pct", 0) or 0),
            float(opts.get("vat_pct", 0) or 0),
        )
        if s:
            row += 1
            items = [(t.t("price.subtotal"), s.subtotal)]
            if s.discount_pct:
                items.append((t.t("price.discount", pct=f"{s.discount_pct:g}"), -s.discount))
            if s.vat_pct:
                items.append((t.t("price.vat", pct=f"{s.vat_pct:g}"), s.vat))
            items.append((t.t("price.total"), s.total))
            for i, (k, v) in enumerate(items):
                ws.cell(row=row, column=ncols - 1, value=k).font = Font(name=FONT, bold=i == len(items) - 1)
                c = ws.cell(row=row, column=ncols, value=v)
                c.number_format = money
                c.font = Font(name=FONT, bold=i == len(items) - 1)
                row += 1
    row += 1
    note = ws.cell(
        row=row, column=1, value=t.t("xl.footer", v=__version__, updated=catalog.meta.updated if catalog else "")
    )
    note.font = Font(name=FONT, size=9, italic=True, color=MUTED)
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
    for i, w in widths.items():
        ws.column_dimensions[get_column_letter(i)].width = w
    if with_prices:
        ws.column_dimensions["F"].width = 16
        ws.column_dimensions["G"].width = 18
    ws.freeze_panes = ws.cell(row=first_data, column=1)
    _print_setup(ws, header_row)

    # ---- IP plan --------------------------------------------------------------------------
    if result.ip_plan is not None:
        wsi = wb.create_sheet(t.t("xl.sheet_ip"))
        plan = result.ip_plan
        with_addr = bool(plan.base_network) and not plan.error
        cols = ip_columns(t, with_addr)
        r = _title(
            wsi, t.t("xl.ip_title", site=result.input.name), plan.base_network or t.t("xl.ip_no_base"), len(cols)
        )
        _header(wsi, r, cols)
        hdr = r
        r += 1
        for values in ip_rows(result):
            for c, v in enumerate(values, start=1):
                cell = wsi.cell(row=r, column=c, value=v)
                cell.border = BOX
                cell.alignment = WRAP_TOP
                cell.font = Font(name=FONT, size=10)
            r += 1
        if plan.error:
            wsi.cell(row=r + 1, column=1, value=plan.error).font = Font(name=FONT, color="A3433B", bold=True)
        _autofit(wsi, hdr, len(cols), {len(cols): 60})
        wsi.freeze_panes = wsi.cell(row=hdr + 1, column=1)
        _print_setup(wsi, hdr)

    # ---- diagram --------------------------------------------------------------------------
    if catalog is not None and result.has_equipment:
        try:
            png, w, h = _diagram_png(result, catalog, lang)
        except Exception:
            png = None
        if png:
            wsd = wb.create_sheet(t.t("xl.sheet_diagram"))
            wsd.sheet_view.showGridLines = False
            scale = min(1.0, 900 / w)
            wsd.add_image(_PngImage(png, int(w * scale), int(h * scale)), "B2")

    # ---- racks ----------------------------------------------------------------------------
    if catalog is not None and result.rack.plans:
        from .rack import rack_table_rows

        wsr = wb.create_sheet(t.t("xl.sheet_racks"))
        cols = [t.t("xl.col_rack"), t.t("xl.col_u"), t.t("xl.col_height"), t.t("xl.col_item"), t.t("col.model")]
        r = _title(wsr, t.t("xl.racks_title", site=result.input.name), "", len(cols))
        _header(wsr, r, cols)
        hdr = r
        r += 1
        for rack_row in rack_table_rows(result):
            name, span, height, item, model = rack_row
            for c, v in enumerate((name, span, f"{height}U", item, model), start=1):
                cell = wsr.cell(row=r, column=c, value=v)
                cell.border = BOX
                cell.alignment = CENTER if c in (2, 3) else WRAP_TOP
                cell.font = Font(name=FONT, size=10, bold=c == 1)
            r += 1
        for i, w in {1: 12, 2: 9, 3: 9, 4: 40, 5: 24}.items():
            wsr.column_dimensions[get_column_letter(i)].width = w
        wsr.freeze_panes = wsr.cell(row=hdr + 1, column=1)
        try:
            png, w, h = _rack_png(result, catalog, lang)
        except Exception:
            png = None
        if png:
            scale = min(1.0, 1100 / w)
            wsr.add_image(_PngImage(png, int(w * scale), int(h * scale)), "G2")

    # ---- inputs / notes -------------------------------------------------------------------
    wsn = wb.create_sheet(t.t("xl.sheet_inputs"))
    r = _title(wsn, t.t("xl.inputs_title"), result.input.name, 2)
    _header(wsn, r, [t.t("xl.param"), t.t("xl.value")])
    hdr = r
    r += 1
    for k, v in _input_rows(result, t, catalog):
        wsn.cell(row=r, column=1, value=k).border = BOX
        c = wsn.cell(row=r, column=2, value=v)
        c.border = BOX
        c.alignment = WRAP_TOP
        r += 1
    r += 1
    wsn.cell(row=r, column=1, value=t.t("xl.checks")).font = Font(name=FONT, bold=True, color=NAVY)
    r += 1
    for chk in result.checks:
        wsn.cell(row=r, column=1, value=t.t(f"xl.sev.{chk.severity.value}"))
        c = wsn.cell(row=r, column=2, value=chk.message + (f"\n{chk.hint}" if chk.hint else ""))
        c.alignment = WRAP_TOP
        r += 1
    wsn.column_dimensions["A"].width = 38
    wsn.column_dimensions["B"].width = 100
    wsn.freeze_panes = wsn.cell(row=hdr + 1, column=1)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _diagram_png(result: SiteResult, catalog: Catalog, lang: str) -> tuple[bytes, int, int]:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    from .diagram import SiteDiagram, render_image

    img = render_image(SiteDiagram(result, catalog, lang, title=False), scale=1.6)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")  # type: ignore[call-overload]
    buf.close()
    ratio = img.devicePixelRatio()
    return bytes(data.data()), int(img.width() / ratio), int(img.height() / ratio)


def _rack_png(result: SiteResult, catalog: Catalog, lang: str) -> tuple[bytes, int, int]:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    from .diagram import render_image
    from .rack import RackDiagram

    img = render_image(RackDiagram(result, catalog, lang), scale=1.4)  # type: ignore[arg-type]
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")  # type: ignore[call-overload]
    buf.close()
    ratio = img.devicePixelRatio()
    return bytes(data.data()), int(img.width() / ratio), int(img.height() / ratio)


def _input_rows(result: SiteResult, t: Translator, catalog: Catalog | None) -> list[tuple[str, Any]]:
    s = result.input
    tier_label = t.pick(catalog.tier(s.tier).label) if catalog else str(s.tier)
    rows: list[tuple[str, Any]] = [
        (t.t("ui.site_name"), s.name),
        (t.t("ui.mode"), t.t("ui.mode_quick") if s.mode == "quick" else t.t("ui.mode_extended")),
        (t.t("ui.sockets"), s.sockets),
        (t.t("ui.cameras"), s.cameras),
    ]
    for g in s.ap_groups:
        zone = catalog.ap_zones.get(g.zone) if catalog else None
        name = g.name or (t.pick(zone.label) if zone else g.zone)
        rows.append((t.t("xl.zone", name=name), f"{g.qty} × {g.model or (zone.model if zone else '')}"))
    rows += [
        (t.t("ui.tier"), f"{s.tier} · {tier_label}"),
        (t.t("ui.psu"), t.t("xl.yes") if result.dual_psu else t.t("xl.no")),
        (t.t("ui.agg"), t.t(f"ui.agg_{s.aggregation}")),
        (t.t("ui.reserve"), f"+{round((result.counts.reserve_factor - 1) * 100)}%" if s.reserve else t.t("xl.no")),
        (t.t("ui.fortios"), result.fortios_version),
        (t.t("ui.rack_size"), f"{s.rack_size_u}U" if s.rack_size_u else t.t("ui.rack_size_auto")),
    ]
    if s.mode == "extended":
        rows += [
            (t.t("ui.clients"), s.wifi_clients_expected or t.t("xl.auto")),
            (t.t("ui.voice"), s.voice_phones),
            (t.t("ui.guest"), s.guest_clients),
            (t.t("ui.iot"), s.iot_devices),
            (t.t("ui.cam_w"), s.camera_watts or (catalog.rules.camera_watts_default if catalog else "")),
            (t.t("ui.inspected"), s.inspected_mbps or "—"),
            (t.t("ui.max_run"), s.max_cable_run_m or "—"),
            (t.t("ui.fiber_type"), t.t("ui.fiber_auto") if s.fiber_type == "auto" else s.fiber_type.upper()),
            (t.t("ui.fiber_backbone"), s.fiber_backbone_m or t.t("xl.auto")),
            (t.t("ui.base_net"), s.ip.base_network or "—"),
        ]
    rows += [
        (t.t("ui.pw_total"), f"{result.power.total_w:.0f} W · UPS ≥ {result.power.ups_va} VA"),
    ]
    if s.notes:
        rows.append((t.t("ui.notes"), s.notes))
    return rows
