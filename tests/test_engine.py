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
def test_aggregation_auto_threshold(catalog: Catalog) -> None:
    assert size_site(make_site(sockets=96), catalog).core.count == 0  # 2 switches
    assert size_site(make_site(sockets=144), catalog).core.count == 1  # 3 switches


def test_aggregation_forced_and_disabled(catalog: Catalog) -> None:
    assert size_site(make_site(sockets=10, aggregation="yes"), catalog).core.count == 1
    r = size_site(make_site(sockets=48 * 6, aggregation="no"), catalog)
    assert r.core.count == 0 and "AGG_OFF_MANY" in codes(r)


def test_prototype_aggregation_aliases() -> None:
    assert SiteInput(aggregation="y").aggregation == "yes"  # type: ignore[arg-type]
    assert SiteInput(aggregation="n").aggregation == "no"  # type: ignore[arg-type]
    assert SiteInput(aggregation="").aggregation == "auto"  # type: ignore[arg-type]


def test_tier1_core_pair(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=200, tier=1, redundant_psu=False), catalog)
    assert r.core.count == 2
    assert "n+1" in r.lines("core_switch")[0].tags


def test_core_port_limit_adds_groups(catalog: Catalog) -> None:
    # 24 ports − 1 FortiLink uplink = 23 access switches per single core
    r = size_site(make_site(sockets=48 * 30), catalog)
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


def test_extended_ip_plan_present(catalog: Catalog) -> None:
    r = size_site(make_site(mode="extended", sockets=50), catalog)
    assert r.ip_plan is not None and r.ip_plan.segments[0].id == "data"
    assert size_site(make_site(sockets=50), catalog).ip_plan is None


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


def test_switch_stays_with_its_panels(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=96, rack_size_u=24), catalog)
    items = r.rack.plans[0].items
    idx = next(i for i, it in enumerate(items) if it.group == "access_switch")
    assert [it.group for it in items[idx - 3 : idx]] == ["panel", "panel", "manager"]


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
