"""A location with several floors: one firewall, optical links to it, the specification added up."""

from __future__ import annotations

from sitesizer.core.catalog import Catalog
from sitesizer.core.location import size_location
from sitesizer.core.models import SiteInput
from sitesizer.core.sizing import size_site


def floor(n: int, **kw) -> SiteInput:
    return SiteInput.model_validate({"name": f"{n} поверх", "floor": n, "location_code": "BO123", **kw})


def qty(result, model: str) -> int:
    return sum(line.qty or 0 for line in result.bom if line.model == model)


def test_firewall_only_on_its_floor(catalog: Catalog) -> None:
    loc = size_location(
        [("f7", floor(7, sockets=96), True), ("f5", floor(5, sockets=144), False)], catalog, name="BO123"
    )
    f7, f5 = loc.floor("f7"), loc.floor("f5")
    assert f7 is not None and f5 is not None
    assert f7.lines("firewall") and not f5.lines("firewall")
    assert not any(it.group == "firewall" for p in f5.rack.plans for it in p.items)
    # the firewall manages the switches of every floor
    assert f7.firewall is not None and loc.combined is not None
    assert loc.combined.lines("firewall")[0].qty == 1
    assert loc.combined.lines("access_switch")[0].qty == 2 + 3


def test_optical_panel_towards_the_firewall_floor(catalog: Catalog) -> None:
    loc = size_location([("f7", floor(7, sockets=48), True), ("f5", floor(5, sockets=48), False)], catalog)
    f7, f5 = loc.floor("f7"), loc.floor("f5")
    assert f7 is not None and f5 is not None
    odf5 = [it for it in f5.rack.plans[0].items if it.group == "fiber"]
    odf7 = [it for it in f7.rack.plans[0].items if it.group == "fiber"]
    assert [it.label for it in odf5] == ["ODF 7A"] and [it.label for it in odf7] == ["ODF 5A"]
    # each optical panel has its organizer right under it
    items = f5.rack.plans[0].items
    i = items.index(odf5[0])
    assert items[i + 1].group == "manager" and items[i + 1].u == odf5[0].u - 1
    combined = loc.combined
    assert combined is not None
    om = catalog.passive.fiber[combined.rack.passive.fiber_type]
    assert qty(combined, om.housing_12) == 2 and qty(combined, om.transceiver) == 2
    assert combined.rack.passive.fiber_links == 1


def test_second_cabinet_in_a_room_is_linked_by_fibre(catalog: Catalog) -> None:
    r = size_site(floor(1, sockets=48 * 8, rack_size_u=24), catalog)
    assert len(r.rack.plans) > 1 and all(p.room == 0 for p in r.rack.plans)
    first = r.rack.plans[0]
    for plan in r.rack.plans[1:]:
        assert any(it.id == f"odf:{plan.key}:{first.key}" for it in plan.items)
        assert any(it.id == f"odf:{first.key}:{plan.key}" for it in first.items)


def test_single_floor_project_is_a_standalone_location(catalog: Catalog) -> None:
    site = floor(1, sockets=96)
    loc = size_location([("a", site, True)], catalog)
    plain = size_site(site, catalog)
    assert loc.combined is not None
    assert [(line.key, line.qty) for line in loc.combined.bom] == [(line.key, line.qty) for line in plain.bom]
    assert not any(it.group == "fiber" for p in plain.rack.plans for it in p.items)


def test_patch_panels_and_organizers_per_switch(catalog: Catalog) -> None:
    r = size_site(floor(1, sockets=48, ap_groups=[{"zone": "corridor", "qty": 4}]), catalog)
    pr = catalog.passive
    # Wi-Fi switch: 1 panel + 1 organizer; access switch: 2 panels + 2 organizers
    assert qty(r, pr.panel) == 3 and qty(r, pr.manager) == 3
    assert not any(line.model == pr.outlet for line in r.bom)


def test_deleting_from_the_cabinet_updates_the_specification(catalog: Catalog) -> None:
    base = size_site(floor(1, sockets=96), catalog)
    key = base.rack.plans[0].key
    hidden = ["firewall:1", "access_switch:2", "panel:access_switch:2:1"]
    r = size_site(floor(1, sockets=96, layout={"hidden": hidden}), catalog)
    assert not r.lines("firewall") and not any(line.model == "FG-BUNDLE" for line in r.bom)
    assert r.lines("access_switch")[0].qty == 1
    assert qty(r, catalog.passive.panel) == qty(base, catalog.passive.panel) - 1
    assert all(it.id not in hidden for it in r.rack.plans[0].items)
    assert key == r.rack.plans[0].key


def test_combined_lines_explain_the_floors(catalog: Catalog) -> None:
    loc = size_location([("a", floor(2, sockets=48), True), ("b", floor(3, sockets=96), False)], catalog)
    assert loc.combined is not None
    line = loc.combined.lines("access_switch")[0]
    assert line.qty == 3 and line.reason == "2 поверх: 1 · 3 поверх: 2"
    assert {p.floor for p in loc.combined.rack.plans} == {2, 3}
