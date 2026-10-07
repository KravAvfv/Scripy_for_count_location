from __future__ import annotations

import math

import pytest

from sitesizer.core.catalog import Catalog, catalog_with_overrides
from sitesizer.core.models import Severity, SiteInput
from sitesizer.core.sizing import apply_reserve, size_site

from .conftest import make_site


def codes(result) -> set[str]:
    return {c.code for c in result.checks}


# ---- reserve --------------------------------------------------------------------------------
def test_reserve_rounds_up_before_sizing(catalog: Catalog) -> None:
    site = make_site(sockets=40, cameras=41, aps=[("low_density", 20), ("outdoor", 1)], reserve=True)
    counts = apply_reserve(site, catalog)
    assert counts.sockets == 48 and counts.cameras == math.ceil(41 * 1.2)
    assert [g.qty for g in counts.ap_groups] == [24, 2]
    assert counts.aps == 26
    r = size_site(site, catalog)
    assert r.categories["access_switch"].count == 1  # 48 fits exactly
    assert r.categories["camera_switch"].count == 2  # 50 cameras
    assert r.categories["wifi_switch"].count == 2  # 26 APs


def test_reserve_custom_percent_and_exact_multiples(catalog: Catalog) -> None:
    site = make_site(sockets=10, reserve=True, reserve_percent=50)
    assert apply_reserve(site, catalog).sockets == 15
    # 5 * 1.2 is 6.000000000000001 in floating point — must not become 7
    assert apply_reserve(make_site(sockets=5, reserve=True), catalog).sockets == 6


def test_reserve_off_keeps_values(catalog: Catalog) -> None:
    counts = apply_reserve(make_site(sockets=7, cameras=3), catalog)
    assert (counts.sockets, counts.cameras, counts.reserve_factor) == (7, 3, 1.0)


# ---- zero / empty ---------------------------------------------------------------------------
def test_empty_site_has_no_equipment(catalog: Catalog) -> None:
    r = size_site(make_site(), catalog)
    assert r.bom == [] and r.firewall is None and r.total_switches == 0
    assert "NO_INPUT" in codes(r)


def test_negative_values_rejected() -> None:
    with pytest.raises(ValueError):
        SiteInput(sockets=-1)


# ---- switch counts --------------------------------------------------------------------------
@pytest.mark.parametrize(("sockets", "expected"), [(1, 1), (48, 1), (49, 2), (96, 2), (97, 3)])
def test_access_switch_count(catalog: Catalog, sockets: int, expected: int) -> None:
    assert size_site(make_site(sockets=sockets), catalog).categories["access_switch"].count == expected


@pytest.mark.parametrize(("aps", "expected"), [(1, 1), (24, 1), (25, 2)])
def test_wifi_switch_count(catalog: Catalog, aps: int, expected: int) -> None:
    r = size_site(make_site(aps=[("corridor", aps)]), catalog)
    assert r.categories["wifi_switch"].count == expected


# ---- variant selection ----------------------------------------------------------------------
def test_dual_psu_variant_when_confirmed(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=10, cameras=5, aps=[("corridor", 2)], redundant_psu=True), catalog)
    assert r.categories["access_switch"].model == "FS-448E"
    assert r.categories["camera_switch"].model == "FS-448E-FPOE"
    assert r.categories["wifi_switch"].model == "FS-624F-FPOE"
    assert "PSU_CONFIRM" not in codes(r)


def test_tier_suggests_dual_psu_and_asks_for_confirmation(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=10, tier=2), catalog)
    assert r.dual_psu and not r.dual_psu_confirmed
    assert r.categories["access_switch"].model == "FS-448E"
    confirm = [c for c in r.checks if c.code == "PSU_CONFIRM"]
    assert confirm and confirm[0].action == "confirm_psu"


def test_tier_suggestion_can_be_declined(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=10, tier=1, redundant_psu=False), catalog)
    assert r.categories["access_switch"].model == "FS-148F"
    assert "PSU_CONFIRM" not in codes(r)


def test_low_tier_uses_base_without_prompt(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=10, tier=3), catalog)
    assert r.categories["access_switch"].model == "FS-148F"
    assert "PSU_CONFIRM" not in codes(r)


@pytest.mark.parametrize(("count_sockets", "model"), [(24 * 48, "FS-148F"), (25 * 48, "FS-448E")])
def test_quantity_threshold_mode(compat_catalog: Catalog, count_sockets: int, model: str) -> None:
    r = size_site(make_site(sockets=count_sockets, aggregation="no"), compat_catalog)
    assert r.categories["access_switch"].model == model


def test_variant_threshold_is_editable(catalog: Catalog) -> None:
    cat = catalog_with_overrides(catalog, {"rules": {"variant_mode": "quantity", "variant_quantity_threshold": 2}})
    assert size_site(make_site(sockets=96), cat).categories["access_switch"].model == "FS-448E"
    assert size_site(make_site(sockets=48), cat).categories["access_switch"].model == "FS-148F"


# ---- PoE ------------------------------------------------------------------------------------
def test_bt_auto_upgrade_to_624f(catalog: Catalog) -> None:
    r = size_site(make_site(aps=[("high_density", 12)]), catalog)  # 12 bt APs > 8 bt ports
    wifi = r.categories["wifi_switch"]
    assert wifi.model == "FS-624F-FPOE" and wifi.upgraded_for_bt
    assert "BT_UPGRADE" in codes(r)
    assert "auto" in r.lines("wifi_switch")[0].tags


def test_bt_within_base_capacity(catalog: Catalog) -> None:
    r = size_site(make_site(aps=[("high_density", 8)]), catalog)
    assert r.categories["wifi_switch"].model == "FS-124G-FPOE"


def test_bt_warning_when_upgrade_disabled(catalog: Catalog) -> None:
    cat = catalog_with_overrides(catalog, {"rules": {"bt_auto_upgrade": False}})
    r = size_site(make_site(aps=[("high_density", 12)]), cat)
    assert r.categories["wifi_switch"].model == "FS-124G-FPOE"
    assert "BT_PORTS" in codes(r)


def test_camera_poe_autoscale(catalog: Catalog) -> None:
    # 48 cameras × 25 W = 1200 W > 740 W → 2 switches
    r = size_site(make_site(cameras=48, camera_watts=25), catalog)
    assert r.categories["camera_switch"].count == 2
    assert "POE_AUTOSCALE" in codes(r)


def test_camera_poe_near_budget_warning(catalog: Catalog) -> None:
    r = size_site(make_site(cameras=48, camera_watts=15), catalog)  # 720/740 = 97 %
    near = [c for c in r.checks if c.code == "POE_NEAR"]
    assert near and near[0].severity == Severity.WARNING


def test_camera_poe_over_without_autoscale(catalog: Catalog) -> None:
    cat = catalog_with_overrides(catalog, {"rules": {"poe_autoscale": False}})
    r = size_site(make_site(cameras=48, camera_watts=25), cat)
    assert r.categories["camera_switch"].count == 1
    assert any(c.code == "POE_OVER" and c.severity == Severity.ERROR for c in r.checks)
    assert "⚠️" in r.lines("camera_switch")[0].reason


def test_single_psu_poe_note(catalog: Catalog) -> None:
    # 24 × 41.7 W ≈ 1000 W on one FS-624F-FPOE: fine with 2 PSUs (1440 W), not with 1 (780 W)
    r = size_site(make_site(aps=[("high_density", 24)], redundant_psu=True), catalog)
    assert "POE_SINGLE_PSU" in codes(r)


# ---- aggregation ----------------------------------------------------------------------------
def test_core_only_above_16_switches_and_2_floors(catalog: Catalog) -> None:
    def core(switches: int, floors: int) -> int:
        return size_site(make_site(sockets=48 * switches, floors=floors), catalog).core.count

    assert core(16, 5) == 0  # 16 switches is not "more than 16"
    assert core(17, 2) == 0  # 2 floors is not "more than 2"
    assert core(17, 3) == 1
    assert core(3, 10) == 0  # the old "3 switches" rule is gone


def test_aggregation_forced_and_disabled(catalog: Catalog) -> None:
    assert size_site(make_site(sockets=10, aggregation="yes"), catalog).core.count == 1
    r = size_site(make_site(sockets=48 * 20, floors=4, aggregation="no"), catalog)
    assert r.core.count == 0 and "AGG_OFF_MANY" in codes(r)


def test_prototype_aggregation_aliases() -> None:
    assert SiteInput(aggregation="y").aggregation == "yes"  # type: ignore[arg-type]
    assert SiteInput(aggregation="n").aggregation == "no"  # type: ignore[arg-type]
    assert SiteInput(aggregation="").aggregation == "auto"  # type: ignore[arg-type]


def test_tier1_core_pair(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=200, tier=1, redundant_psu=False, aggregation="yes"), catalog)
    assert r.core.count == 2
    assert "n+1" in r.lines("core_switch")[0].tags


def test_core_port_limit_adds_groups(catalog: Catalog) -> None:
    # 24 ports − 1 FortiLink uplink = 23 access switches per single core
    r = size_site(make_site(sockets=48 * 30, floors=5), catalog)
    assert r.core.count == 2 and "CORE_PORTS" in codes(r)


# ---- firewall ladder ------------------------------------------------------------------------
def test_firewall_smallest_fit_without_core(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=96), catalog)
    assert r.firewall is not None and r.firewall.model == "FG-80F"


def test_core_requires_10g_firewall(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=10, aggregation="yes"), catalog)
    assert r.firewall is not None and r.firewall.model == "FG-120G"
    assert any("FG-80F" in s for s in r.firewall.skipped)


@pytest.mark.parametrize(
    ("switches", "fortios", "model"),
    [
        (40, "7.6.4", "FG-120G"),
        (40, "7.4.5", "FG-200G"),
        (60, "7.6.4", "FG-200G"),
        (80, "7.6.4", "FG-400G"),
    ],
)
def test_firewall_ladder_switch_limits(catalog: Catalog, switches: int, fortios: str, model: str) -> None:
    # access switches + cores; single core can't hold them all but the count is what matters here
    r = size_site(make_site(sockets=48 * switches, aggregation="no", fortios_version=fortios), catalog)
    assert r.firewall is not None and r.firewall.model == model


def test_firewall_ap_limit(catalog: Catalog) -> None:
    r = size_site(make_site(aps=[("corridor", 100)], aggregation="no"), catalog)  # 100 APs > 96 (80F)
    assert r.firewall is not None and r.firewall.model == "FG-120G"
    assert any("AP" in s for s in r.firewall.skipped)


def test_firewall_fallback_when_nothing_fits(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=48 * 100, aggregation="no"), catalog)
    assert r.firewall is not None and not r.firewall.fits
    assert "FG-600G+" in r.firewall.model
    assert any(c.code == "FW_NONE" and c.severity == Severity.ERROR for c in r.checks)


def test_firewall_throughput_criterion(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=96, inspected_mbps=2500), catalog)  # 120G 2.8 Gbps × 0.7 < 2.5
    assert r.firewall is not None and r.firewall.model == "FG-200G"


def test_firewall_ha_pair(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=10, tier=1, redundant_psu=False), catalog)
    assert r.firewall is not None and r.firewall.count == 2
    assert "ha" in r.lines("firewall")[0].tags


# ---- tiers ----------------------------------------------------------------------------------
def test_tier_lines(catalog: Catalog) -> None:
    t1 = size_site(make_site(sockets=10, tier=1, redundant_psu=False), catalog)
    t2 = size_site(make_site(sockets=10, tier=2, redundant_psu=False), catalog)
    t3 = size_site(make_site(sockets=10, tier=3), catalog)
    assert {line.model for line in t1.lines("power")} >= {"OOB-LTE"}
    assert len(t1.lines("power")) == 2
    assert len(t2.lines("power")) == 1
    assert t3.lines("power") == []


def test_tier_effects_configurable(catalog: Catalog) -> None:
    cat = catalog_with_overrides(catalog, {"tiers": {"3": {"fw_ha": True, "ups": True}}})
    r = size_site(make_site(sockets=10, tier=3), cat)
    assert r.firewall is not None and r.firewall.count == 2
    assert r.lines("power")


# ---- add-ons --------------------------------------------------------------------------------
def test_quick_mode_has_only_passive_addons(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=100, cameras=20, aps=[("corridor", 6)]), catalog)
    addon_groups = {line.group for line in r.bom if "addon" in line.tags}
    assert addon_groups == {"cabling", "rack"}


def test_extended_mode_addons(catalog: Catalog) -> None:
    r = size_site(
        make_site(mode="extended", sockets=100, cameras=20, aps=[("corridor", 6)], tier=2, redundant_psu=False), catalog
    )
    groups = {line.group for line in r.bom}
    assert {"transceiver", "cabling", "rack", "license", "spare"} <= groups
    panels = next(line for line in r.bom if line.model == catalog.passive.panel)
    # panels are counted per switch: Wi-Fi 6 APs → 1, access 100 sockets over 3 switches → 2+2+2, CCTV 20 → 1
    assert panels.qty == 1 + 6 + 1 >= math.ceil(126 / 24)


def test_addon_can_be_forced_off(catalog: Catalog) -> None:
    r = size_site(make_site(mode="extended", sockets=100, addons={"cabling": False}), catalog)
    assert not r.lines("cabling")


def test_spares_minimum_one(catalog: Catalog) -> None:
    r = size_site(make_site(mode="extended", sockets=10, tier=1, redundant_psu=False), catalog)
    spare = r.lines("spare")
    assert spare and all(line.qty and line.qty >= 1 for line in spare)


def test_idf_hint_and_fibre(catalog: Catalog) -> None:
    r = size_site(make_site(mode="extended", sockets=200, max_cable_run_m=200), catalog)
    assert r.rack.idf_count == 3 and "IDF" in codes(r)
    assert any(line.model == "FN-TRAN-SFP+SR" for line in r.bom)


def test_80f_without_sfp_uses_copper_sfp(catalog: Catalog) -> None:
    r = size_site(make_site(mode="extended", sockets=96), catalog)
    assert r.firewall is not None and r.firewall.model == "FG-80F"
    assert any(line.model == "FN-TRAN-GC" and line.qty == 2 for line in r.bom)


# ---- power ----------------------------------------------------------------------------------
def test_power_includes_poe(catalog: Catalog) -> None:
    r = size_site(make_site(cameras=10, camera_watts=10), catalog)
    assert r.power.poe_w == pytest.approx(100)
    assert r.power.total_w == pytest.approx(r.power.equipment_w + 100 / 0.9)
    assert r.power.ups_va >= r.power.total_w / 0.9


def test_ups_model_scales(catalog: Catalog) -> None:
    r = size_site(make_site(mode="extended", cameras=400, tier=2, redundant_psu=False), catalog)
    ups = next(line for line in r.bom if line.group == "power")
    assert ups.model.startswith("UPS-")


# ---- misc checks ----------------------------------------------------------------------------
def test_high_density_hint(catalog: Catalog) -> None:
    site = SiteInput.model_validate({"ap_groups": [{"zone": "low_density", "qty": 4, "clients_per_ap": 30}]})
    assert "HIGH_DENSITY" in codes(size_site(site, catalog))


def test_english_output(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=10), catalog, lang="en")
    assert r.lines("access_switch")[0].category == "Access switches"


def test_ip_plan_in_every_mode(catalog: Catalog) -> None:
    r = size_site(make_site(mode="extended", sockets=50), catalog)
    assert r.ip_plan is not None and r.ip_plan.segments[0].id == "data"
    quick = size_site(make_site(sockets=50), catalog).ip_plan
    assert quick is not None and not quick.has_addresses


def test_old_project_with_voice_phones_loads(catalog: Catalog) -> None:
    site = SiteInput.model_validate({"sockets": 10, "voice_phones": 30})
    r = size_site(site, catalog)
    assert r.ip_plan is not None and "voice" not in [s.id for s in r.ip_plan.segments]


def test_448e_fpoe_camera_switch(catalog: Catalog) -> None:
    # FS-448E-FPOE: 772 W covers 48 cameras × 15 W = 720 W on one switch (93 % → near-budget warning)
    r = size_site(make_site(cameras=48, redundant_psu=True), catalog)
    cam = r.categories["camera_switch"]
    assert (cam.model, cam.count) == ("FS-448E-FPOE", 1)
    assert "POE_NEAR" in codes(r) and "POE_AUTOSCALE" not in codes(r)
    eoo = [c for c in r.checks if c.code == "EOO"]
    assert eoo and all(c.severity == Severity.INFO for c in eoo)


# ---- passive infrastructure & racks ---------------------------------------------------------
def test_copper_quantities(catalog: Catalog) -> None:
    pr = catalog.passive
    r = size_site(make_site(sockets=40, cameras=10, aps=[("corridor", 6)], avg_cable_run_m=30), catalog)
    ps = r.rack.passive
    assert ps.copper_links == 56
    assert ps.cable_m == 56 * (30 + pr.cable_slack_m)
    assert ps.cable_drums == math.ceil(ps.cable_m / pr.cable_drum_m)
    assert ps.jacks == 112 and ps.jack_packs == math.ceil(112 / pr.jack_pack)
    assert ps.outlets == 20 + 16
    assert ps.cords_rack == 56 + 16 and ps.cords_user == 40
    models = {line.model: line.qty for line in r.lines("cabling")}
    assert models[pr.cable] == ps.cable_drums and models[pr.outlet] == 36


def test_small_site_fits_24u(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=20), catalog)
    assert [p.size_u for p in r.rack.plans] == [24]
    plan = r.rack.plans[0]
    assert plan.items[0].u == 24  # firewall at the top
    assert any(it.group == "pdu" and it.u == 1 for it in plan.items)
    assert any(line.model == "RACK-24U" for line in r.lines("rack"))


def test_rack_size_preference_and_split(catalog: Catalog) -> None:
    big = dict(sockets=600, cameras=100, aps=[("low_density", 40)])
    auto = size_site(make_site(**big), catalog)
    assert all(p.size_u == 42 for p in auto.rack.plans)
    forced = size_site(make_site(rack_size_u=24, **big), catalog)
    assert all(p.size_u == 24 for p in forced.rack.plans)
    assert len(forced.rack.plans) > len(auto.rack.plans)
    for plan in forced.rack.plans:
        assert plan.used_u <= plan.size_u
        units = [u for it in plan.items for u in range(it.u, it.u + it.height)]
        assert len(units) == len(set(units)), "items overlap"


def test_house_rack_pattern(catalog: Catalog) -> None:
    """Organizer · PP · organizer · switch · organizer · PP PP · organizer · switch … (photo of rack 5B)."""
    r = size_site(make_site(sockets=96, cameras=48, aps=[("corridor", 10)]), catalog)
    items = r.rack.plans[0].items
    first = next(i for i, it in enumerate(items) if it.group == "manager")
    body = [it.group for it in items[first:] if it.group not in ("pdu", "power")]
    assert body == [
        "manager", "panel",  # Wi-Fi panel
        "manager", "wifi_switch",
        "manager", "panel",  # ASW01 upper
        "manager", "access_switch",
        "manager", "panel", "panel",  # ASW01 lower + ASW02 upper
        "manager", "access_switch",
        "manager", "panel", "panel",  # ASW02 lower + VSW01 upper
        "manager", "camera_switch",
        "manager", "panel",  # VSW01 lower
    ]  # fmt: skip
    labels = [it.label for it in items if it.group == "panel"]
    assert labels == ["ПП Wi-Fi", "ПП №1", "ПП №2", "ПП №3", "ПП №4", "ПП №V1", "ПП №V2"]


def test_device_and_rack_names_use_location_code(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=96, location_code="BO123"), catalog)
    plan = r.rack.plans[0]
    assert plan.name == "BO123-1 поверх комутаційна A (1A)"
    names = [it.label for it in plan.items if it.group == "access_switch"]
    assert names == ["BO123-1A-ASW01", "BO123-1A-ASW02"]
    r2 = size_site(
        make_site(sockets=96, location_code="BO123", layout={"props": {"mdf-1": {"floor": 5, "letter": "B"}}}),
        catalog,
    )
    assert r2.rack.plans[0].tag == "5B"
    assert next(it.label for it in r2.rack.plans[0].items if it.group == "access_switch") == "BO123-5B-ASW01"


def _dac(r) -> dict[str, int]:
    return {line.model: line.qty for line in r.bom if line.model.startswith("FN-CABLE")}


def test_dac_length_follows_layout(catalog: Catalog) -> None:
    site = dict(mode="extended", sockets=48 * 2, aggregation="yes")
    r = size_site(make_site(**site), catalog)
    # core and both switches sit close together at the top of the cabinet → 1 m
    assert _dac(r) == {"FN-CABLE-SFP+1": 3}  # 2 uplinks + core ↔ FortiGate
    # drag the second switch to the bottom of the cabinet → its uplink needs 3 m
    plan = r.rack.plans[0]
    moved = make_site(**site, layout={"positions": {"access_switch:2": {"rack": plan.key, "u": 3}}})
    r2 = size_site(moved, catalog)
    assert _dac(r2) == {"FN-CABLE-SFP+1": 2, "FN-CABLE-SFP+3": 1}
    it = next(i for i in r2.rack.plans[0].items if i.id == "access_switch:2")
    assert it.u == 3 and it.manual


def test_manual_layout_racks_and_extras(catalog: Catalog) -> None:
    base = size_site(make_site(sockets=96), catalog)
    key = base.rack.plans[0].key
    layout = {
        "added": ["user-1"],
        "props": {"user-1": {"size_u": 24}},
        "positions": {"access_switch:2": {"rack": "user-1", "u": 20}},
        "extras": [{"id": "x1", "kind": "manager", "rack": "user-1", "u": 21}],
        "hidden": [f"pdu:{key}:1"],
    }
    r = size_site(make_site(sockets=96, layout=layout), catalog)
    assert [p.key for p in r.rack.plans] == [key, "user-1"]
    user = r.rack.plans[1]
    assert user.size_u == 24 and user.letter == "B"
    # the moved switch needs power: the user cabinet gets its own PDU
    assert {it.id for it in user.items} == {"access_switch:2", "x1", "pdu:user-1:1"}
    assert not any(it.group == "pdu" for it in r.rack.plans[0].items)  # the deleted PDU stays deleted
    assert any(line.model == "RACK-24U" for line in r.lines("rack"))
    # removing the automatic cabinet moves its devices into the remaining one
    r2 = size_site(make_site(sockets=96, layout={**layout, "removed": [key]}), catalog)
    assert [p.key for p in r2.rack.plans] == ["user-1"]
    ids = {it.id for it in r2.rack.plans[0].items}
    assert {"access_switch:1", "access_switch:2", "firewall:1"} <= ids


def test_manual_overlap_is_reported(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=96), catalog)
    plan = r.rack.plans[0]
    fw = next(it for it in plan.items if it.group == "firewall")
    site = make_site(sockets=96, layout={"positions": {"access_switch:1": {"rack": plan.key, "u": fw.u}}})
    r2 = size_site(site, catalog)
    units = [u for it in r2.rack.plans[0].items for u in range(it.u, it.top + 1)]
    assert len(units) == len(set(units)), "the firewall must move away from the dropped switch"


def test_manual_quantity_and_price(catalog: Catalog) -> None:
    site = make_site(sockets=96, bom={"overrides": {"access_switch:FS-148F": {"qty": 5, "price": 1000}}})
    r = size_site(site, catalog)
    line = r.lines("access_switch")[0]
    assert (line.qty, line.calc_qty, line.unit_price, line.manual) == (5, 2, 1000, True)
    assert r.categories["access_switch"].count == 5
    assert sum(1 for p in r.rack.plans for it in p.items if it.group == "access_switch") == 5
    custom = {"id": "c1", "name": "Монтаж шафи", "qty": 2, "price": 900, "section": "works"}
    r2 = size_site(make_site(sockets=10, bom={"custom": [custom]}), catalog)
    line = r2.lines("custom")[0]
    assert (line.description, line.qty, line.total_price, line.key) == ("Монтаж шафи", 2, 1800, "custom:c1")


def test_fibre_backbone_to_idf(catalog: Catalog) -> None:
    r = size_site(make_site(mode="extended", sockets=300, max_cable_run_m=200), catalog)
    ps = r.rack.passive
    assert r.rack.idf_count == 3 and len(ps.backbone_m) == 2
    assert ps.fiber_type == "om4" and ps.fiber_links > 0
    roles = [p.role for p in r.rack.plans]
    assert roles.count("mdf") == 1 and roles.count("idf") == 2
    fibre = {line.model: line.qty for line in r.lines("fiber")}
    om4 = catalog.passive.fiber["om4"]
    assert fibre[om4.cable] == ps.fiber_m and fibre[om4.cord] == ps.fiber_links * 2
    sr = next(line for line in r.bom if line.model == om4.transceiver)
    assert sr.qty == ps.fiber_links * 2


def test_closet_count_entered_by_the_user(catalog: Catalog) -> None:
    r = size_site(make_site(mode="extended", sockets=300, closets=3), catalog)
    assert r.rack.idf_count == 3
    roles = [p.role for p in r.rack.plans]
    assert roles.count("mdf") == 1 and roles.count("idf") == 2
    # no cable run given: each remote closet is estimated one copper reach further
    assert r.rack.passive.backbone_m == [90, 180] and r.rack.passive.fiber_links > 0
    # the entered number wins over the cable run
    r2 = size_site(make_site(mode="extended", sockets=300, max_cable_run_m=200, closets=2), catalog)
    assert r2.rack.idf_count == 2
    # more closets than switches: the empty ones still get a cabinet to switch to
    r3 = size_site(make_site(sockets=48, closets=3), catalog)
    assert [p.role for p in r3.rack.plans] == ["mdf", "idf", "idf"]
    assert r3.rack.plans[2].items == []
    # 0 = automatic, as before
    assert size_site(make_site(sockets=300, closets=0), catalog).rack.idf_count == 1


def test_telecom_rooms(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=200, closets=2), catalog)
    assert r.rack.rooms == ["Комутаційна 1 (MDF)", "Комутаційна 2 (IDF-1)"]
    assert [(p.key, p.room) for p in r.rack.plans] == [("mdf-1", 0), ("idf1-1", 1)]
    # cabinets added by the user stand in the room they were added to, next to its other cabinets
    layout = {
        "added": ["user-1", "user-2"],
        "props": {"user-1": {"room": 1, "size_u": 24}, "user-2": {"room": 7}},
        "room_names": {"1": "Серверна"},
    }
    r2 = size_site(make_site(sockets=200, closets=2, layout=layout), catalog)
    assert [(p.key, p.room) for p in r2.rack.plans] == [("mdf-1", 0), ("idf1-1", 1), ("user-1", 1), ("user-2", 1)]
    assert r2.rack.rooms == ["Комутаційна 1 (MDF)", "Серверна"]


def test_drop_room_renumbers_the_layout(catalog: Catalog) -> None:
    from sitesizer.core.passive import drop_room

    layout = {
        "added": ["user-1", "user-2"],
        "removed": ["idf1-2", "idf2-2"],
        "props": {"user-1": {"room": 1}, "user-2": {"room": 2}, "idf2-1": {"size_u": 42}},
        "positions": {
            "access_switch:1": {"rack": "idf2-1", "u": 3},
            "access_switch:2": {"rack": "user-1", "u": 3},
            "pdu:idf2-1:1": {"rack": "idf2-1", "u": 1},
        },
        "hidden": ["pdu:idf1-1:1", "pdu:idf2-1:2", "fiber:1:0:12:1", "fiber:2:1:12:1"],
        "extras": [{"id": "x1", "rack": "user-1", "u": 5}, {"id": "x2", "rack": "user-2", "u": 5}],
        "labels": {"pdu:idf2-1:1": "PDU", "access_switch:1": "A1"},
        "room_names": {"1": "Старий", "2": "Новий"},
    }
    drop_room(layout, 1, {"idf1-1", "user-1"})
    assert layout["added"] == ["user-2"]
    assert layout["removed"] == ["idf1-2"]
    assert layout["props"] == {"user-2": {"room": 1}, "idf1-1": {"size_u": 42}}
    assert layout["positions"] == {
        "access_switch:1": {"rack": "idf1-1", "u": 3},
        "pdu:idf1-1:1": {"rack": "idf1-1", "u": 1},
    }
    assert layout["hidden"] == ["pdu:idf1-1:2", "fiber:1:1:12:1"]
    assert [ex["rack"] for ex in layout["extras"]] == ["", "user-2"]
    assert layout["labels"] == {"pdu:idf1-1:1": "PDU", "access_switch:1": "A1"}
    assert layout["room_names"] == {"1": "Новий"}
    # the result still sizes: the device of the removed room lands in another cabinet
    site = make_site(sockets=200, closets=2, layout=layout)
    r = size_site(site, catalog)
    assert any(it.id == "x1" for p in r.rack.plans for it in p.items)


def test_rack_items_named_by_the_user(catalog: Catalog) -> None:
    base = size_site(make_site(sockets=96, location_code="BO123"), catalog)
    key = base.rack.plans[0].key
    layout = {
        "labels": {"access_switch:2": "Комутатор 2 поверх", "firewall:1": ""},
        "extras": [{"id": "x1", "kind": "device", "rack": key, "u": 30, "model": "FS-148F", "label": "Мій"}],
    }
    r = size_site(make_site(sockets=96, location_code="BO123", layout=layout), catalog)
    items = {it.id: it for it in r.rack.plans[0].items}
    assert items["access_switch:2"].label == "Комутатор 2 поверх"
    assert items["access_switch:2"].model == "FS-148F"
    assert items["access_switch:1"].label == "BO123-1A-ASW01"  # the others keep their automatic names
    assert items["firewall:1"].label.startswith("BO123-1A-FW")  # an empty name = automatic
    assert items["x1"].label == "Мій"
    assert not make_site(layout={"labels": {"a": "b"}}).layout.is_empty


def test_long_backbone_switches_to_single_mode(catalog: Catalog) -> None:
    r = size_site(make_site(mode="extended", sockets=200, max_cable_run_m=150, fiber_backbone_m=900), catalog)
    assert r.rack.passive.fiber_type == "os2"
    assert any(line.model == "FN-TRAN-SFP+LR" for line in r.bom)
    r2 = size_site(make_site(mode="extended", sockets=200, max_cable_run_m=150, fiber_type="os2"), catalog)
    assert r2.rack.passive.fiber_type == "os2" and not r2.rack.passive.fiber_auto


def test_dual_psu_gets_ab_pdus(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=48, tier=2, redundant_psu=True), catalog)
    pdus = [it for it in r.rack.plans[0].items if it.group == "pdu"]
    assert len(pdus) == 2 and {it.label.split()[1] for it in pdus} == {"A", "B"}


def test_devices_added_to_racks_go_to_the_bom(catalog: Catalog) -> None:
    base = size_site(make_site(sockets=48), catalog)
    key = base.rack.plans[0].key
    managers = base.rack.passive.managers
    extras = [
        {"id": "d1", "kind": "device", "model": "FS-148F-FPOE", "rack": key, "u": 15},
        {"id": "d2", "kind": "device", "model": "UPS-3000", "rack": key, "u": 10, "height": 2},
        {"id": "c1", "kind": "custom", "label": "NVR", "rack": key, "u": 6, "height": 2},
        {"id": "m1", "kind": "manager", "rack": key, "u": 4},
    ]
    r = size_site(make_site(sockets=48, layout={"extras": extras}), catalog)
    devices = {line.model: line.qty for line in r.lines("rack_device")}
    assert devices == {"FS-148F-FPOE": 1, "UPS-3000": 1, "NVR": 1}  # custom devices are listed too
    assert all(line.manual for line in r.lines("rack_device"))
    line = next(line for line in r.lines("rack_device") if line.model == "FS-148F-FPOE")
    assert line.code == catalog.models["FS-148F-FPOE"].code and line.key == "rack_device:FS-148F-FPOE"
    assert r.rack.passive.managers == managers + 1  # a hand-added organizer is counted too
    items = {it.id: it for it in r.rack.plans[0].items}
    assert items["d2"].height == 2 and items["c1"].label == "NVR" and items["d1"].label == "FS-148F-FPOE"


def _with_devices(catalog: Catalog, models: list[str], rack: str | None = None, **kw: object):
    base = size_site(make_site(sockets=48, **kw), catalog)
    key = rack or base.rack.plans[0].key
    extras = [{"id": f"d{i}", "kind": "device", "model": m, "rack": key, "u": 2 + i} for i, m in enumerate(models)]
    layout = {"extras": extras}
    if rack and rack.startswith("user-"):
        layout["added"] = [rack]
        layout["props"] = {rack: {"size_u": 42}}
    return base, size_site(make_site(sockets=48, layout=layout, **kw), catalog)


def test_rack_device_counts_in_power_heat_and_ups(catalog: Catalog) -> None:
    base, r = _with_devices(catalog, ["FS-148F", "FS-148F"], ups=True)
    w = catalog.models["FS-148F"].power_base_w
    assert w and r.power.equipment_w == pytest.approx(base.power.equipment_w + 2 * w)
    assert r.power.total_w == pytest.approx(base.power.total_w + 2 * w)
    assert r.power.heat_btu > base.power.heat_btu and r.power.ups_va >= base.power.ups_va
    assert ("FS-148F", 2, 2 * w) in r.power.breakdown
    # a UPS placed by hand feeds the cabinet, it is not a load
    _, r2 = _with_devices(catalog, ["UPS-3000"])
    assert r2.power.total_w == pytest.approx(base.power.total_w)


def test_rack_device_switch_counts_for_firewall_and_mgmt_ips(catalog: Catalog) -> None:
    base, r = _with_devices(catalog, ["FS-148F"])
    mgmt = {seg.id: seg.hosts for seg in base.ip_plan.segments}
    mgmt2 = {seg.id: seg.hosts for seg in r.ip_plan.segments}
    assert mgmt2["mgmt"] == mgmt["mgmt"] + 1
    assert r.firewall and base.firewall and r.firewall.model == base.firewall.model
    # enough hand-placed switches push the FortiGate over its FortiLink switch limit
    limit = base.firewall.switch_limit
    _, big = _with_devices(catalog, ["FS-148F"] * limit, rack="user-1")
    assert big.firewall and big.firewall.model != base.firewall.model
    assert big.firewall.switch_limit >= limit + 1


def test_rack_devices_get_pdu_outlets(catalog: Catalog) -> None:
    outlets = catalog.passive.pdu_outlets
    base, r = _with_devices(catalog, ["FS-148F"] * (outlets + 1), rack="user-1")
    user = next(p for p in r.rack.plans if p.key == "user-1")
    pdus = [it for it in user.items if it.group == "pdu"]
    assert len(pdus) == 2
    units = [u for it in user.items for u in range(it.u, it.top + 1)]
    assert len(units) == len(set(units)), "PDUs must not overlap the devices"
    pdu_qty = {line.model: line.qty for line in r.lines("rack")}[catalog.passive.pdu]
    assert pdu_qty == base.rack.passive.pdus + 2 == r.rack.passive.pdus


def test_rack_device_survives_a_missing_cabinet(catalog: Catalog) -> None:
    _, r = _with_devices(catalog, ["FS-148F"], rack="idf7-1")  # this cabinet is not produced
    assert {line.model: line.qty for line in r.lines("rack_device")} == {"FS-148F": 1}
    assert any(it.id == "d0" for p in r.rack.plans for it in p.items)


def test_rack_device_in_spec_and_overrides(catalog: Catalog) -> None:
    from sitesizer.core.report import spec_sections
    from sitesizer.i18n import Translator

    base, r = _with_devices(catalog, ["FS-148F"])
    rows = {row.model: row.qty for _, sec in spec_sections(r, catalog, Translator("uk")) for row in sec}
    rows0 = {row.model: row.qty for _, sec in spec_sections(base, catalog, Translator("uk")) for row in sec}
    assert rows["FS-148F"] == rows0.get("FS-148F", 0) + 1
    key = base.rack.plans[0].key
    site = make_site(
        sockets=48,
        layout={"extras": [{"id": "d0", "kind": "device", "model": "FS-148F", "rack": key, "u": 1}]},
        bom={"overrides": {"rack_device:FS-148F": {"qty": 3}}},
    )
    line = size_site(site, catalog).lines("rack_device")[0]
    assert line.qty == 3 and line.calc_qty == 1


def test_rack_device_unknown_model_is_ignored_safely(catalog: Catalog) -> None:
    base, r = _with_devices(catalog, ["NOT-IN-CATALOG"])
    assert r.power.total_w == pytest.approx(base.power.total_w)
    assert {line.model for line in r.lines("rack_device")} == {"NOT-IN-CATALOG"}
