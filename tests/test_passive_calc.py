"""Passive parts of existing cabinets: typed in, or read from an Excel file."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from sitesizer.core.catalog import Catalog
from sitesizer.core.passive_calc import cabinets_from_xlsx, count_passive, parse_cabinet
from sitesizer.passive_cli import main


def qty(pc, model: str) -> int:
    return sum(line.qty for line in pc.lines if line.model == model)


def test_parse_cabinet() -> None:
    cab = parse_cabinet("5В: W1 A4, V1")  # Cyrillic В is fine
    assert (cab.tag, cab.floor, cab.wifi, cab.access, cab.video) == ("5B", 5, 1, 4, 1)
    with pytest.raises(ValueError):
        parse_cabinet("5B W1")
    with pytest.raises(ValueError):
        parse_cabinet("5B: X3")


def test_house_rules(catalog: Catalog) -> None:
    cabs = [parse_cabinet("5B: W1 A4 V1"), parse_cabinet("5A: A2"), parse_cabinet("7A: A1 F1")]
    pc = count_passive(cabs, catalog)
    pr = catalog.passive
    assert pc.fw_tag == "7A" and pc.links == [("5B", "5A"), ("5A", "7A")]
    # panels: Wi-Fi 1 + 8 other switches × 2; organizers: Wi-Fi 1 + 8 × 2 + one per optical panel (4)
    assert qty(pc, pr.panel) == 1 + 8 * 2
    assert qty(pc, pr.manager) == 1 + 8 * 2 + 4
    assert qty(pc, pr.fiber["om4"].housing_12) == 4
    # DACs per floor: floor 5 has 8 switches → 4 + 1, floor 7 has 1 → 1 + 1
    assert qty(pc, "FN-CABLE-SFP+1") == 4 + 1 and qty(pc, "FN-CABLE-SFP+3") == 1 + 1
    assert not any(line.model == pr.outlet for line in pc.lines)


def test_ten_switches_give_five_short_and_one_long_dac(catalog: Catalog) -> None:
    pc = count_passive([parse_cabinet("3A: A10")], catalog)
    assert qty(pc, "FN-CABLE-SFP+1") == 5 and qty(pc, "FN-CABLE-SFP+3") == 1


def test_rack_scheme_from_excel(tmp_path: Path, catalog: Catalog) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for v in ("ВО123-5 поверх комутаційна В (5В)", "ODF 5A", "Органайзер", "ПП Wi-Fi модульна cat.6a",
              "BO123-5B-WSW01 (FS-624F-FPOE)", "BO123-5B-ASW01 (FS-148F)", "BO123-5B-ASW02 (FS-148F)",
              "BO123-5B-VSW01 (FS-148F-FPOE)", "BO123-7A-FW01 (FG-80F)", "BO123-7A-ASW01 (FS-148F)"):  # fmt: skip
        ws.append([v])
    ws.append(["BO123-5B-ASW01 (FS-148F)"])  # the same device listed twice counts once
    path = tmp_path / "scheme.xlsx"
    wb.save(path)
    cabs, warnings, how = cabinets_from_xlsx(path, catalog)
    assert how == "names" and not warnings
    by = {c.tag: c for c in cabs}
    assert (by["5B"].wifi, by["5B"].access, by["5B"].video) == (1, 2, 1)
    assert by["7A"].firewall == 1 and count_passive(cabs, catalog).fw_tag == "7A"


def test_specification_from_excel(tmp_path: Path, catalog: Catalog) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Код", "Найменування", "Од.", "К-сть"])
    ws.append(["000081182", "Комутатор FS-148F", "шт.", 3])
    ws.append(["", "Forticare Premium Support 1 year for FS-148F", "шт.", 3])
    ws.append(["", "Комутатор Fortinet FortiSwitch 624F-FPOE", "шт.", 1])
    ws.append(["", "Комутатор FS-124G-FPOE", "шт.", 0])
    path = tmp_path / "spec.xlsx"
    wb.save(path)
    cabs, warnings, how = cabinets_from_xlsx(path, catalog)
    assert how == "spec" and warnings
    assert (cabs[0].access, cabs[0].wifi) == (3, 1)


def test_cli_writes_excel(tmp_path: Path, capsys) -> None:
    out = tmp_path / "p.xlsx"
    assert main(["--cab", "5B: W1 A2", "--cab", "7A: A1 F1", "--out", str(out)]) == 0
    assert out.exists() and "5B→" in capsys.readouterr().out
