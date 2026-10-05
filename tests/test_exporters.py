from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openpyxl import load_workbook

from sitesizer.core.catalog import Catalog
from sitesizer.core.presets import preset
from sitesizer.core.sizing import size_site
from sitesizer.exporters.diagram import ProjectDiagram, SiteDiagram, export_png, export_svg
from sitesizer.exporters.pdf import export_pdf
from sitesizer.exporters.xlsx import export_xlsx

from .conftest import make_site


@pytest.fixture(scope="module")
def hq_result(catalog: Catalog):
    p = preset("hq")
    assert p is not None
    return size_site(p.build(name="Тест HQ"), catalog)


def test_xlsx_structure(tmp_path: Path, catalog: Catalog, hq_result) -> None:
    path = export_xlsx(hq_result, tmp_path / "out.xlsx", catalog=catalog, options={"author": "A", "project": "P"})
    wb = load_workbook(path)
    assert wb.sheetnames == ["BoM", "IP-план", "Схема", "Шафи", "Вихідні дані"]
    ws = wb["BoM"]
    assert ws["A4"].value == "Категорія" and ws["A4"].fill.start_color.rgb.endswith("1F4E78")
    assert ws.freeze_panes == "A5"
    models = [ws.cell(row=r, column=2).value for r in range(5, ws.max_row + 1)]
    assert "FG-120G" in models
    with zipfile.ZipFile(path) as z:
        assert any(n.startswith("xl/media/") for n in z.namelist())


def test_xlsx_quick_mode_has_no_ip_sheet(tmp_path: Path, catalog: Catalog) -> None:
    r = size_site(make_site(sockets=10), catalog)
    wb = load_workbook(export_xlsx(r, tmp_path / "q.xlsx", catalog=catalog))
    assert "IP-план" not in wb.sheetnames


def test_xlsx_prices(tmp_path: Path, catalog: Catalog) -> None:
    r = size_site(make_site(sockets=10), catalog)
    for line in r.bom:
        if line.qty:
            line.unit_price = 100.0
    wb = load_workbook(export_xlsx(r, tmp_path / "p.xlsx", catalog=catalog, options={"vat_pct": 20}))
    ws = wb["BoM"]
    assert ws.cell(row=4, column=7).value == "Сума"


def test_pdf(tmp_path: Path, catalog: Catalog, hq_result) -> None:
    path = export_pdf(hq_result, tmp_path / "r.pdf", catalog=catalog, options={"customer": "X"})
    data = path.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 20_000


def test_diagram_exports(tmp_path: Path, catalog: Catalog, hq_result) -> None:
    png = export_png(SiteDiagram(hq_result, catalog), tmp_path / "d.png")
    assert png.read_bytes()[:4] == b"\x89PNG"
    svg = export_svg(ProjectDiagram([("HQ", True, hq_result), ("B", False, hq_result)], catalog), tmp_path / "d.svg")
    assert "<svg" in svg.read_text(encoding="utf-8")


def test_rack_exports(tmp_path: Path, catalog: Catalog, hq_result) -> None:
    from sitesizer.exporters.rack import RackDiagram, rack_table_rows

    d = RackDiagram(hq_result, catalog)
    assert d.size().height() > hq_result.rack.plans[0].size_u * d.U_H
    assert export_png(d, tmp_path / "r.png").read_bytes()[:4] == b"\x89PNG"  # type: ignore[arg-type]
    assert "<svg" in export_svg(d, tmp_path / "r.svg").read_text(encoding="utf-8")  # type: ignore[arg-type]
    rows = rack_table_rows(hq_result)
    assert len(rows) == sum(len(p.items) for p in hq_result.rack.plans)
    wb = load_workbook(export_xlsx(hq_result, tmp_path / "x.xlsx", catalog=catalog))
    ws = wb["Шафи"]
    assert ws.cell(row=4, column=1).value == "Шафа" and str(ws.cell(row=5, column=1).value).startswith("MDF")


def test_empty_diagram(catalog: Catalog) -> None:
    d = SiteDiagram(size_site(make_site(), catalog), catalog)
    assert d.empty and d.size().width() > 0


def test_cli_exports(tmp_path: Path) -> None:
    from sitesizer import cli

    inp = tmp_path / "s.json"
    inp.write_text('{"name": "X", "sockets": 30, "cameras": 4, "mode": "extended"}', encoding="utf-8")
    assert (
        cli.main(["--json", str(inp), "--xlsx", str(tmp_path / "x.xlsx"), "--pdf", str(tmp_path / "x.pdf"), "--quiet"])
        == 0
    )
    assert (tmp_path / "x.xlsx").exists() and (tmp_path / "x.pdf").exists()
