from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from sitesizer.core.catalog import Catalog, catalog_from_dict
from sitesizer.core.report import spec_sections
from sitesizer.core.sizing import size_site
from sitesizer.core.template_import import apply_template, read_template
from sitesizer.i18n import Translator

from .conftest import make_site


def _template(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Слаботрумка"
    rows = [
        (None, None, "СКС"),
        ("000018257", 'Блок розеток 220В 19" 1U 8 розеток', "шт", 0, 690.30, None, 767, None),
        (None, None, "Network"),
        ("000084545", "Комутатор FS-148F", "шт.", 0, "4 611,60", None, 5124, None),
        ("000075051", "Forticare Premium Support 1 year for FS-148F", "шт.", 0, 43659, None, 48510, None),
        ("000085280", "Додатковий БЖ для FG-80F (5 шт)", "шт.", 0, None, None, None, None),
        ("000081433", "DAC-кабель FN-CABLE-SFP+1", "шт.", 0, 2357.1, None, 2619, None),
        (None, None, "Роботи"),
        (None, "Встановлення відеокамери", "шт.", 0, 900, None, 1000, None),
    ]
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    return path


def test_template_import(tmp_path: Path, catalog: Catalog) -> None:
    rows = read_template(_template(tmp_path / "t.xlsx"))
    assert [r.section for r in rows] == ["sks", "network", "network", "network", "network", "works"]
    data, report = apply_template(catalog.model_dump(mode="json"), rows)
    cat = catalog_from_dict(data)
    sw = cat.models["FS-148F"]
    assert (sw.code, sw.price_min, sw.price, sw.section) == ("000084545", 4611.6, 5124, "network")
    assert cat.models["FN-CABLE-SFP+1"].price == 2619
    assert cat.models["FG-80F"].price is None, "accessories 'for FG-80F' must not overwrite the firewall"
    assert "000075051" in report.added and "000085280" in report.added
    work = cat.models["Встановлення відеокамери"]
    assert work.kind == "work" and work.price == 1000
    # the specification now follows the template order and sections
    r = size_site(make_site(sockets=48), cat)
    sections = dict(spec_sections(r, cat, Translator("uk")))
    assert [row.code for row in sections["sks"]][:1] == ["000018257"]
    assert sections["network"][0].name == "Комутатор FS-148F" and sections["network"][0].qty == 1
    assert sections["works"][0].qty == 0
