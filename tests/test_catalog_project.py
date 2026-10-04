from __future__ import annotations

import json
from pathlib import Path

import pytest

from sitesizer.core.catalog import (
    Catalog,
    CatalogError,
    catalog_from_dict,
    load_catalog,
    parse_version,
    save_catalog,
)
from sitesizer.core.compare import cost_delta, diff_results
from sitesizer.core.presets import load_presets
from sitesizer.core.pricing import summarize_prices
from sitesizer.core.project import ProjectError, load_project, new_project, save_project
from sitesizer.core.report import bom_csv, bom_tsv, result_to_dict
from sitesizer.core.sizing import size_site
from sitesizer.i18n import Translator, plural_index

from .conftest import make_site


# ---- catalog --------------------------------------------------------------------------------
def test_default_catalog_valid(catalog: Catalog) -> None:
    assert catalog.firewall_ladder == ["FG-80F", "FG-120G", "FG-200G", "FG-400G"]
    for model in catalog.firewall_ladder:
        assert catalog.device(model).verified == "datasheet"


def test_catalog_roundtrip(tmp_path: Path, catalog: Catalog) -> None:
    path = tmp_path / "c.json"
    save_catalog(catalog, path)
    again = load_catalog(path)
    assert again.model_dump() == catalog.model_dump()


def test_catalog_bad_reference_is_friendly(catalog: Catalog) -> None:
    data = catalog.model_dump(mode="json")
    data["categories"]["wifi_switch"]["base"] = "FS-NOPE"
    with pytest.raises(CatalogError) as exc:
        catalog_from_dict(data)
    assert "FS-NOPE" in str(exc.value)


def test_catalog_negative_value_rejected(catalog: Catalog) -> None:
    data = catalog.model_dump(mode="json")
    data["models"]["FS-148F-FPOE"]["poe"]["budget_w"] = -5
    with pytest.raises(CatalogError) as exc:
        catalog_from_dict(data)
    assert "budget_w" in str(exc.value)


def test_catalog_missing_section(catalog: Catalog) -> None:
    data = catalog.model_dump(mode="json")
    del data["models"]["FG-80F"]["firewall"]
    with pytest.raises(CatalogError):
        catalog_from_dict(data)


def test_catalog_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{ nope", encoding="utf-8")
    with pytest.raises(CatalogError) as exc:
        load_catalog(p)
    assert "JSON" in str(exc.value)


@pytest.mark.parametrize(("text", "expected"), [("7.6.1", (7, 6, 1)), ("v7.4", (7, 4, 0)), ("7.6.1+", (7, 6, 1))])
def test_parse_version(text: str, expected: tuple[int, ...]) -> None:
    assert parse_version(text) == expected


def test_fortios_dependent_limit(catalog: Catalog) -> None:
    fw = catalog.device("FG-120G").firewall
    assert fw is not None
    assert fw.switch_limit("7.4.4") == 32
    assert fw.switch_limit("7.6.1") == 48
    assert fw.switch_limit(None) == 32


# ---- project --------------------------------------------------------------------------------
def test_project_roundtrip(tmp_path: Path) -> None:
    project = new_project("Тест", author="Інженер")
    project.sites[0].input.sockets = 42
    second = project.add_site(make_site(name="Філія", cameras=8, aps=[("outdoor", 2)]))
    dup = project.duplicate_site(second.id)
    assert dup is not None and dup.input.name.endswith("(копія)")
    path = save_project(project, tmp_path / "p.sizing.json")
    loaded = load_project(path)
    assert loaded.model_dump(exclude={"modified"}) == project.model_dump(exclude={"modified"})
    assert loaded.hub is not None and loaded.hub.input.sockets == 42


def test_project_remove_hub_reassigns(tmp_path: Path) -> None:
    project = new_project()
    other = project.add_site()
    assert project.remove_site(project.sites[0].id)
    assert project.hub is not None and project.hub.id == other.id


def test_project_errors(tmp_path: Path) -> None:
    with pytest.raises(ProjectError):
        load_project(tmp_path / "missing.json")
    bad = tmp_path / "x.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ProjectError):
        load_project(bad)


def test_bare_site_json_loads_as_project(tmp_path: Path) -> None:
    p = tmp_path / "site.json"
    p.write_text(json.dumps({"name": "X", "sockets": 5}), encoding="utf-8")
    assert load_project(p).sites[0].input.sockets == 5


# ---- presets --------------------------------------------------------------------------------
def test_presets_build_and_size(catalog: Catalog) -> None:
    presets = load_presets()
    assert {p.id for p in presets} >= {"warehouse", "rnd_office", "farm3d", "small_branch", "hq"}
    for p in presets:
        result = size_site(p.build(), catalog)
        assert result.has_equipment


# ---- pricing / compare / report ---------------------------------------------------------------
def test_pricing_hidden_without_prices(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=10), catalog)
    assert summarize_prices(r.bom) is None


def test_pricing_totals(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=100), catalog)
    for line in r.bom:
        if line.qty:
            line.unit_price = 1000.0
    s = summarize_prices(r.bom, discount_pct=10, vat_pct=20)
    assert s is not None
    qty = sum(line.qty for line in r.bom if line.qty)
    assert s.subtotal == 1000 * qty
    assert s.total == pytest.approx(1000 * qty * 0.9 * 1.2)


def test_compare_tiers(catalog: Catalog) -> None:
    a = size_site(make_site(sockets=100, tier=2, redundant_psu=False), catalog)
    b = size_site(make_site(sockets=100, tier=1, redundant_psu=False), catalog)
    rows = {(r.group, r.model): r for r in diff_results(a, b)}
    fw = rows[("firewall", "FG-120G")]  # 3 access switches → core → 10G firewall
    assert fw.qty_a == 1 and fw.qty_b == 2 and fw.status == "changed"
    assert rows[("power", "OOB-LTE")].status == "added"
    assert cost_delta(list(rows.values())) is None


def test_report_exports(catalog: Catalog) -> None:
    r = size_site(make_site(mode="extended", sockets=10, cameras=2), catalog)
    t = Translator("uk")
    tsv = bom_tsv(r, t)
    assert tsv.splitlines()[0].startswith("Категорія\tМодель")
    assert ";" in bom_csv(r, t).splitlines()[0]
    d = result_to_dict(r)
    json.dumps(d, ensure_ascii=False)
    assert d["bom"] and d["ip_plan"]


# ---- i18n -----------------------------------------------------------------------------------
@pytest.mark.parametrize(("n", "idx"), [(1, 0), (2, 1), (4, 1), (5, 2), (11, 2), (12, 2), (21, 0), (22, 1), (25, 2)])
def test_ukrainian_plural(n: int, idx: int) -> None:
    assert plural_index("uk", n) == idx


def test_translation_keys_in_sync() -> None:
    root = Path(__file__).parent.parent / "sitesizer" / "i18n"
    uk = json.loads((root / "uk.json").read_text(encoding="utf-8"))
    en = json.loads((root / "en.json").read_text(encoding="utf-8"))
    assert set(uk) == set(en)
