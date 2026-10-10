"""The sizing engine: ``SiteInput`` + ``Catalog`` -> ``SiteResult``.

Pure, deterministic and side-effect free; safe to call on every keystroke. All tunable
numbers come from ``catalog.rules`` / ``catalog.tiers``; all user-facing text comes from the
i18n tables, so the same engine serves the GUI, the CLI and the exporters.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from ..i18n import Translator
from .catalog import EDGE_CATEGORIES, Catalog, Category, Device, Tier, parse_version
from .ipplan import SegmentRequest, plan_by_template, plan_segments
from .models import (
    ADDON_KEYS,
    ApGroup,
    BomLine,
    CategoryResult,
    Check,
    EffectiveCounts,
    FirewallChoice,
    FloorContext,
    IpPlan,
    PowerSummary,
    RackSummary,
    Severity,
    SiteInput,
    SiteResult,
)
from .passive import plan_passive

log = logging.getLogger(__name__)

FORTIGUARD_SKU = {"atp": "928", "utp": "950", "enterprise": "809"}


# =========================================================================================
# helpers
# =========================================================================================
def ceil_div(a: float, b: float) -> int:
    return math.ceil(a / b) if a > 0 and b > 0 else 0


def apply_reserve(site: SiteInput, catalog: Catalog) -> EffectiveCounts:
    """Apply the growth reserve (default +20 %) and ``ceil`` — before any switch sizing.

    Each AP group is scaled individually and the AP total is the sum of the scaled groups,
    so BoM lines and switch sizing always agree. (The prototype scaled the total separately,
    which could differ by one AP when several groups were present.)
    """
    pct = site.reserve_percent if site.reserve_percent is not None else catalog.rules.reserve_percent_default
    factor = 1 + pct / 100 if site.reserve else 1.0

    def scale(n: int) -> int:
        return math.ceil(round(n * factor, 6)) if n else 0

    groups = [g.model_copy(update={"qty": scale(g.qty)}) for g in site.ap_groups if g.qty > 0]
    clients = site.wifi_clients_expected
    if clients:
        clients = scale(clients)
    return EffectiveCounts(
        sockets=scale(site.sockets),
        cameras=scale(site.cameras),
        aps=sum(g.qty for g in groups),
        ap_groups=groups,
        wifi_clients=clients or None,
        reserve_factor=factor,
    )


def ap_model_for(group: ApGroup, catalog: Catalog) -> str:
    if group.model and group.model in catalog.models:
        return group.model
    zone = catalog.ap_zones.get(group.zone)
    if zone:
        return zone.model
    return next(iter(catalog.aps()), "")


@dataclass
class _Ctx:
    site: SiteInput
    catalog: Catalog
    t: Translator
    tier: Tier
    counts: EffectiveCounts
    dual_psu: bool
    checks: list[Check]
    calc_counts: dict[str, int] = field(default_factory=dict)
    """BoM line key -> calculated quantity, for lines whose count the user overrode."""
    manual_devices: list[tuple[str, Device]] = field(default_factory=list)
    """Catalog devices the user put into cabinets by hand, one entry per unit."""
    floor: FloorContext | None = None
    """Place of this floor in a multi-floor location (``None`` = standalone location)."""

    @property
    def has_firewall(self) -> bool:
        return self.floor is None or self.floor.has_firewall

    def other(self, key: str) -> int:
        """Total of the location's other floors (0 for a standalone location)."""
        return self.floor.others.get(key, 0) if self.floor and self.floor.has_firewall else 0

    def manual_count(self, *kinds: str) -> int:
        return sum(1 for _, dev in self.manual_devices if dev.kind in kinds)

    def check(
        self,
        severity: Severity,
        code: str,
        key: str,
        hint_key: str = "",
        category: str = "",
        action: str = "",
        **kw: object,
    ) -> None:
        self.checks.append(
            Check(
                severity=severity,
                code=code,
                message=self.t.t(key, **kw),
                hint=self.t.t(hint_key, **kw) if hint_key else "",
                category=category,
                action=action,
            )
        )

    @property
    def rules(self):
        return self.catalog.rules


# =========================================================================================
# variant selection
# =========================================================================================
def pick_variant(cat: Category, count: int, ctx: _Ctx) -> tuple[str, bool]:
    """Return (model, is_premium) for a switch category according to ``rules.variant_mode``."""
    mode = ctx.rules.variant_mode
    if mode == "always_base":
        premium = False
    elif mode == "always_premium":
        premium = True
    elif mode == "quantity":
        thr = ctx.rules.variant_quantity_threshold
        premium = thr > 0 and count >= thr
    else:  # dual_psu
        premium = ctx.dual_psu
    return (cat.premium if premium else cat.base), premium


def apply_count_override(ctx: _Ctx, key: str, model: str, count: int) -> int:
    """Return the user's manual quantity for ``key:model`` (remembering the calculated one)."""
    ov = ctx.site.bom.overrides.get(f"{key}:{model}")
    if ov is None or ov.qty is None or not model:
        return count
    ctx.calc_counts[f"{key}:{model}"] = count
    return ov.qty


def _override_count(ctx: _Ctx, res: CategoryResult) -> None:
    res.count = apply_count_override(ctx, res.key, res.model, res.count)
    if res.count and res.poe_load_w:
        res.poe_per_switch_w = res.poe_load_w / res.count


def _variant_note(ctx: _Ctx, dev: Device, premium: bool) -> str:
    t = ctx.t
    if not premium:
        return ""
    if ctx.rules.variant_mode == "quantity":
        return t.t("reason.variant_quantity", thr=ctx.rules.variant_quantity_threshold)
    if dev.psu and dev.psu.hot_swap:
        return t.t("reason.variant_dual_psu_hot")
    return t.t("reason.variant_dual_psu")


# =========================================================================================
# per-category sizing
# =========================================================================================
def _poe_checks(ctx: _Ctx, res: CategoryResult, dev: Device, label: str) -> None:
    if not dev.poe or res.count == 0:
        return
    budget = dev.poe.budget_w
    res.poe_budget_w = budget
    res.poe_per_switch_w = res.poe_load_w / res.count if res.count else 0
    ratio = res.poe_per_switch_w / budget if budget else math.inf
    if ratio > 1:
        ctx.check(
            Severity.ERROR,
            "POE_OVER",
            "check.poe_over",
            "check.poe_over_hint",
            res.key,
            cat=label,
            load=round(res.poe_per_switch_w),
            budget=round(budget),
        )
    elif ratio > ctx.rules.poe_warn_ratio:
        ctx.check(
            Severity.WARNING,
            "POE_NEAR",
            "check.poe_near",
            "check.poe_near_hint",
            res.key,
            cat=label,
            load=round(res.poe_per_switch_w),
            budget=round(budget),
            pct=round(ratio * 100),
        )
    single = dev.poe.budget_single_psu_w
    if single and res.poe_per_switch_w > single:
        ctx.check(
            Severity.INFO,
            "POE_SINGLE_PSU",
            "check.poe_single_psu",
            "",
            res.key,
            cat=label,
            load=round(res.poe_per_switch_w),
            single=round(single),
        )


def size_wifi(ctx: _Ctx) -> CategoryResult:
    cat = ctx.catalog.categories["wifi_switch"]
    res = CategoryResult(key="wifi_switch", endpoints=ctx.counts.aps, endpoints_per_switch=cat.endpoints_per_switch)
    if ctx.counts.aps <= 0:
        return res
    res.count = ceil_div(ctx.counts.aps, cat.endpoints_per_switch)
    res.model, res.premium = pick_variant(cat, res.count, ctx)
    load = 0.0
    bt = 0
    for g in ctx.counts.ap_groups:
        ap = ctx.catalog.models.get(ap_model_for(g, ctx.catalog))
        if ap and ap.ap:
            load += g.qty * ap.ap.power_w
            if ap.ap.poe_class == "bt":
                bt += g.qty
    res.poe_load_w = load
    res.bt_needed = bt
    label = ctx.t.pick(cat.label)

    dev = ctx.catalog.device(res.model)
    bt_capacity = res.count * (dev.poe.ports_bt if dev.poe else 0)
    if bt > bt_capacity:
        premium_dev = ctx.catalog.device(cat.premium)
        premium_bt = res.count * (premium_dev.poe.ports_bt if premium_dev.poe else 0)
        if ctx.rules.bt_auto_upgrade and not res.premium and premium_bt >= bt:
            ctx.check(
                Severity.INFO,
                "BT_UPGRADE",
                "check.bt_upgrade",
                "",
                res.key,
                bt=bt,
                base=res.model,
                cap=bt_capacity,
                premium=cat.premium,
            )
            res.model, res.premium, res.upgraded_for_bt = cat.premium, True, True
            dev = premium_dev
        else:
            ctx.check(
                Severity.WARNING,
                "BT_PORTS",
                "check.bt_ports",
                "check.bt_ports_hint",
                res.key,
                bt=bt,
                model=res.model,
                cap=bt_capacity,
                premium=cat.premium,
            )

    if dev.poe and dev.poe.budget_w and ctx.rules.poe_autoscale:
        needed = ceil_div(load, dev.poe.budget_w)
        if needed > res.count:
            ctx.check(
                Severity.INFO,
                "POE_AUTOSCALE",
                "check.poe_autoscale",
                "",
                res.key,
                cat=label,
                before=res.count,
                after=needed,
            )
            res.count = needed
    _override_count(ctx, res)
    _poe_checks(ctx, res, dev, label)
    return res


def size_access(ctx: _Ctx) -> CategoryResult:
    cat = ctx.catalog.categories["access_switch"]
    res = CategoryResult(
        key="access_switch", endpoints=ctx.counts.sockets, endpoints_per_switch=cat.endpoints_per_switch
    )
    if ctx.counts.sockets <= 0:
        return res
    res.count = ceil_div(ctx.counts.sockets, cat.endpoints_per_switch)
    res.model, res.premium = pick_variant(cat, res.count, ctx)
    _override_count(ctx, res)
    return res


def camera_watts(ctx: _Ctx) -> float:
    w = ctx.site.camera_watts
    return float(w if w is not None else ctx.rules.camera_watts_default)


def size_cameras(ctx: _Ctx) -> CategoryResult:
    cat = ctx.catalog.categories["camera_switch"]
    res = CategoryResult(
        key="camera_switch", endpoints=ctx.counts.cameras, endpoints_per_switch=cat.endpoints_per_switch
    )
    if ctx.counts.cameras <= 0:
        return res
    res.count = ceil_div(ctx.counts.cameras, cat.endpoints_per_switch)
    res.model, res.premium = pick_variant(cat, res.count, ctx)
    res.poe_load_w = ctx.counts.cameras * camera_watts(ctx)
    dev = ctx.catalog.device(res.model)
    label = ctx.t.pick(cat.label)
    if dev.poe and dev.poe.budget_w and ctx.rules.poe_autoscale:
        needed = ceil_div(res.poe_load_w, dev.poe.budget_w)
        if needed > res.count:
            ctx.check(
                Severity.INFO,
                "POE_AUTOSCALE",
                "check.poe_autoscale",
                "",
                res.key,
                cat=label,
                before=res.count,
                after=needed,
            )
            res.count = needed
    _override_count(ctx, res)
    _poe_checks(ctx, res, dev, label)
    return res


# =========================================================================================
# aggregation / core
# =========================================================================================
def size_core(ctx: _Ctx, edge_count: int, fw_count_hint: int) -> CategoryResult:
    cat = ctx.catalog.categories["core_switch"]
    res = CategoryResult(key="core_switch", endpoints=edge_count, endpoints_per_switch=cat.endpoints_per_switch)
    mode = ctx.site.aggregation
    floors = ctx.floor.floors if ctx.floor else ctx.site.floors
    auto_need = edge_count > ctx.rules.core_min_switches and floors > ctx.rules.core_min_floors
    need = mode == "yes" or (mode == "auto" and auto_need)
    if not need:
        if mode == "no" and auto_need:
            ctx.check(
                Severity.WARNING,
                "AGG_OFF_MANY",
                "check.agg_off_many",
                "check.agg_off_many_hint",
                "core_switch",
                n=edge_count,
            )
        return res
    res.model = cat.base
    dev = ctx.catalog.device(res.model)
    ports = dev.ports.count if dev.ports else cat.endpoints_per_switch
    redundant = ctx.tier.core_redundant
    links = 2 if (ctx.tier.dual_uplinks and redundant) else 1
    res.uplinks_per_switch = links
    if redundant:
        free = ports - ctx.rules.core_icl_links - max(fw_count_hint, 1)
        capacity = (free * 2) // links if free > 0 else 0
    else:
        free = ports - max(fw_count_hint, 1)
        capacity = free // links if free > 0 else 0
    groups = max(1, ceil_div(edge_count, capacity)) if capacity > 0 and ctx.rules.core_port_check else 1
    per_group = 2 if redundant else 1
    res.count = apply_count_override(ctx, "core_switch", res.model, groups * per_group)
    res.premium = redundant
    if groups > 1:
        ctx.check(
            Severity.WARNING,
            "CORE_PORTS",
            "check.core_ports",
            "check.core_ports_hint",
            "core_switch",
            edge=edge_count,
            cap=capacity,
            model=res.model,
            groups=groups,
        )
    return res


# =========================================================================================
# firewall ladder
# =========================================================================================
def pick_firewall(ctx: _Ctx, total_switches: int, total_aps: int, requires_10g: bool) -> FirewallChoice:
    t = ctx.t
    version = ctx.site.fortios_version or ctx.rules.fortios_default
    count = 2 if ctx.tier.fw_ha else 1
    skipped: list[str] = []
    inspected = ctx.site.inspected_mbps or 0
    headroom = ctx.rules.fw_throughput_headroom
    for model in ctx.catalog.firewall_ladder:
        fw = ctx.catalog.device(model).firewall
        assert fw is not None
        limit = fw.switch_limit(version)
        if requires_10g and fw.ports_10g <= 0:
            skipped.append(t.t("fw.skip_10g", model=model))
            continue
        if total_switches > limit:
            skipped.append(t.t("fw.skip_switches", model=model, limit=limit, need=total_switches))
            continue
        if total_aps > fw.max_aps:
            skipped.append(t.t("fw.skip_aps", model=model, limit=fw.max_aps, need=total_aps))
            continue
        usable_mbps = fw.throughput_gbps.threat * 1000 * (1 - headroom)
        if inspected and inspected > usable_mbps:
            skipped.append(t.t("fw.skip_throughput", model=model, tp=fw.throughput_gbps.threat, need=inspected))
            continue
        reasons = [t.t("reason.fw_switch_limit", limit=limit, need=total_switches)]
        if total_aps and fw.max_aps < 10**6:
            reasons.append(t.t("reason.fw_ap_limit", limit=fw.max_aps, need=total_aps))
        if requires_10g:
            reasons.append(t.t("reason.fw_10g"))
        if inspected:
            reasons.append(
                t.t(
                    "reason.fw_throughput",
                    tp=fw.throughput_gbps.threat,
                    need=round(inspected / 1000, 2),
                    pct=round(inspected / (fw.throughput_gbps.threat * 10)),
                )
            )
        return FirewallChoice(
            model=model,
            count=count,
            fits=True,
            switch_limit=limit,
            ap_limit=fw.max_aps,
            reasons=reasons,
            skipped=skipped,
        )
    return FirewallChoice(
        model=t.pick(ctx.catalog.firewall_fallback),
        count=count,
        fits=False,
        reasons=[t.t("reason.fw_none", need=total_switches)],
        skipped=skipped,
    )


# =========================================================================================
# main entry point
# =========================================================================================
def addon_enabled(site: SiteInput, key: str, tier: Tier) -> bool:
    value = site.addons.get(key)
    if value is not None:
        return bool(value)
    extended = site.mode == "extended"
    if key in ("cabling", "rack", "transceivers"):
        return True
    if key == "spares":
        return extended and tier.spare_percent > 0
    if key == "ups":
        return False
    if key == "management":
        return extended and (tier.fw_ha or tier.ups)
    return extended


def resolve_addons(site: SiteInput, tier: Tier) -> dict[str, bool]:
    return {k: addon_enabled(site, k, tier) for k in ADDON_KEYS}


def size_site(site: SiteInput, catalog: Catalog, lang: str = "uk", floor: FloorContext | None = None) -> SiteResult:
    """Run the full sizing for one location, or for one floor of a location (``floor``).

    A floor without the firewall gets neither the firewall nor the core: they are sized once, on
    the firewall floor, for the switches and APs of every floor (``floor.others``).
    """
    t = Translator(lang)
    tier = catalog.tier(site.tier)
    checks: list[Check] = []
    counts = apply_reserve(site, catalog)

    if site.redundant_psu is None:
        dual_psu, confirmed = tier.dual_psu, False
    else:
        dual_psu, confirmed = site.redundant_psu, True
    ctx = _Ctx(site=site, catalog=catalog, t=t, tier=tier, counts=counts, dual_psu=dual_psu, checks=checks, floor=floor)
    ctx.manual_devices = [
        (ex.model, catalog.models[ex.model])
        for ex in site.layout.extras
        if ex.kind == "device" and ex.model in catalog.models
    ]
    addons = resolve_addons(site, tier)

    if catalog.rules.variant_mode == "dual_psu" and not confirmed and tier.dual_psu:
        ctx.check(
            Severity.INFO,
            "PSU_CONFIRM",
            "check.psu_confirm",
            "check.psu_confirm_hint",
            "",
            action="confirm_psu",
            tier=site.tier,
            tier_label=t.pick(tier.label),
        )

    if counts.sockets == 0 and counts.cameras == 0 and counts.aps == 0:
        ctx.check(Severity.INFO, "NO_INPUT", "check.no_input")

    categories = {
        "wifi_switch": size_wifi(ctx),
        "access_switch": size_access(ctx),
        "camera_switch": size_cameras(ctx),
    }
    edge = sum(c.count for c in categories.values())
    fw_count_hint = 2 if tier.fw_ha else 1
    if ctx.has_firewall:
        core = size_core(ctx, edge + ctx.other("switches"), fw_count_hint)
    else:
        core = CategoryResult(key="core_switch")
    total_switches = edge + core.count

    firewall: FirewallChoice | None = None
    # switches / APs placed into cabinets by hand (and those of the other floors) are managed
    # by the same FortiGate
    managed_switches = total_switches + ctx.manual_count("switch") + ctx.other("switches")
    managed_aps = counts.aps + ctx.manual_count("ap") + ctx.other("aps")
    if managed_switches > 0 and ctx.has_firewall:
        firewall = pick_firewall(ctx, managed_switches, managed_aps, requires_10g=core.count > 0)
        firewall.count = apply_count_override(ctx, "firewall", firewall.model, firewall.count)
        if not firewall.fits:
            ctx.check(
                Severity.ERROR, "FW_NONE", "check.fw_none", "check.fw_none_hint", "firewall", need=managed_switches
            )
        else:
            fw_dev = catalog.device(firewall.model)
            if fw_dev.firewall and fw_dev.firewall.max_switches_fortios:
                ov = fw_dev.firewall.max_switches_fortios[-1]
                version = site.fortios_version or catalog.rules.fortios_default
                ctx.check(
                    Severity.INFO,
                    "FW_FORTIOS",
                    "check.fw_fortios",
                    "",
                    "firewall",
                    model=firewall.model,
                    base=fw_dev.firewall.max_switches,
                    value=ov.value,
                    min=ov.min_version,
                    version=version,
                )
            if fw_dev.firewall and fw_dev.firewall.form_factor == "desktop" and tier.fw_ha:
                ctx.check(Severity.INFO, "FW_HA_SKU", "check.fw_ha_sku", "", "firewall", model=firewall.model)

    _uplink_checks(ctx, categories, core, firewall)

    bom: list[BomLine] = []
    power = compute_power(ctx, categories, core, firewall)
    rack = compute_rack(ctx, categories, core, firewall, power, addons)
    if _counts_from_racks(rack, categories, core, firewall):
        power = compute_power(ctx, categories, core, firewall)
    _bom_equipment(ctx, bom, categories, core, firewall)
    _bom_tier_lines(ctx, bom, power, addons, rack)
    if addons["transceivers"]:
        _bom_transceivers(ctx, bom, categories, core, firewall, rack)
    if addons["cabling"]:
        _bom_cabling(ctx, bom, rack)
    if addons["rack"] and rack.plans:
        _bom_rack(ctx, bom, rack)
    _bom_rack_devices(ctx, bom, rack)
    if addons["licensing"]:
        _bom_licensing(ctx, bom, categories, core, firewall)
    if addons["spares"]:
        _bom_spares(ctx, bom)
    if addons["management"]:
        bom.append(
            BomLine(
                group="reference",
                category=t.t("cat.management"),
                model="FortiManager / FortiAnalyzer",
                qty=None,
                reason=t.t("reason.management"),
                tags=["addon", "reference"],
            )
        )

    if total_switches > 0 or counts.aps > 0:
        bom.append(
            BomLine(
                group="reference",
                category=t.t("cat.reference_sla"),
                model="—",
                qty=None,
                reason=t.t(
                    "reason.reference_sla",
                    tier=f"{t.pick(tier.label)} ({t.pick(tier.description)})",
                    sla=t.pick(tier.sla),
                ),
                tags=["reference"],
            )
        )

    _general_checks(ctx, bom, rack)
    _finalize_bom(ctx, bom)
    _bom_custom(ctx, bom)

    ip_plan = build_ip_plan(ctx, sum(c.count for c in categories.values()) + core.count, firewall)

    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    checks.sort(key=lambda c: order[c.severity])
    return SiteResult(
        input=site,
        counts=counts,
        categories=categories,
        core=core,
        firewall=firewall,
        tier_id=site.tier,
        dual_psu=dual_psu,
        dual_psu_confirmed=confirmed,
        fortios_version=site.fortios_version or catalog.rules.fortios_default,
        bom=bom,
        checks=checks,
        power=power,
        rack=rack,
        ip_plan=ip_plan,
        addons=addons,
    )


# =========================================================================================
# BoM builders
# =========================================================================================
def _bom_equipment(
    ctx: _Ctx,
    bom: list[BomLine],
    categories: dict[str, CategoryResult],
    core: CategoryResult,
    fw: FirewallChoice | None,
) -> None:
    t, cat_defs = ctx.t, ctx.catalog.categories

    for g in ctx.counts.ap_groups:
        model = ap_model_for(g, ctx.catalog)
        zone = ctx.catalog.ap_zones.get(g.zone)
        zone_label = t.pick(zone.label) if zone else g.zone
        title = g.name or zone_label
        dev = ctx.catalog.models.get(model)
        details = []
        if dev and dev.ap:
            details.append(t.t("detail.ap_power", w=dev.ap.power_w, cls=dev.ap.poe_class))
        if zone and model != zone.model:
            details.append(t.t("detail.ap_override", zone_model=zone.model))
        if ctx.counts.reserve_factor > 1:
            details.append(t.t("detail.reserve_applied", pct=round((ctx.counts.reserve_factor - 1) * 100)))
        bom.append(
            BomLine(
                group="ap",
                category=t.t("cat.ap", zone=title),
                model=model,
                qty=g.qty,
                reason=t.t("reason.ap", zone=zone_label),
                details=details,
            )
        )

    wifi = categories["wifi_switch"]
    if wifi.count:
        dev = ctx.catalog.device(wifi.model)
        note = _variant_note(ctx, dev, wifi.premium and not wifi.upgraded_for_bt)
        reason = t.t("reason.wifi_switch", ports=cat_defs["wifi_switch"].endpoints_per_switch, note=note)
        details = [
            t.t(
                "detail.poe_load",
                load=round(wifi.poe_load_w),
                per=round(wifi.poe_load_w / wifi.count),
                budget=round(dev.poe.budget_w) if dev.poe else 0,
            )
        ]
        tags = []
        if wifi.upgraded_for_bt:
            reason += " " + t.t("reason.bt_upgrade", bt=t.plural("plural.aps", wifi.bt_needed))
            tags.append("auto")
        if wifi.bt_needed:
            details.append(
                t.t("detail.bt_ports", bt=wifi.bt_needed, cap=wifi.count * (dev.poe.ports_bt if dev.poe else 0))
            )
        if wifi.premium:
            tags.append("psu")
        bom.append(
            BomLine(
                group="wifi_switch",
                category=t.pick(cat_defs["wifi_switch"].label),
                model=wifi.model,
                qty=wifi.count,
                reason=reason,
                details=details,
                tags=tags,
            )
        )

    acc = categories["access_switch"]
    if acc.count:
        dev = ctx.catalog.device(acc.model)
        note = _variant_note(ctx, dev, acc.premium)
        up = dev.uplinks
        bom.append(
            BomLine(
                group="access_switch",
                category=t.pick(cat_defs["access_switch"].label),
                model=acc.model,
                qty=acc.count,
                reason=t.t(
                    "reason.access_switch",
                    ports=cat_defs["access_switch"].endpoints_per_switch,
                    uplink=f"{up.count}× {up.speed_gbps:g}G {up.type}" if up else "",
                    note=note,
                ),
                details=[t.t("detail.endpoints", n=acc.endpoints, per=acc.endpoints_per_switch)],
                tags=["psu"] if acc.premium else [],
            )
        )

    cam = categories["camera_switch"]
    if cam.count:
        dev = ctx.catalog.device(cam.model)
        note = _variant_note(ctx, dev, cam.premium)
        per = math.ceil(ctx.counts.cameras / cam.count)
        est = per * camera_watts(ctx)
        budget = dev.poe.budget_w if dev.poe else 0
        poe_note = (
            t.t("reason.camera_poe_ok", budget=round(budget), per=per, est=round(est))
            if est <= budget
            else t.t("reason.camera_poe_over", budget=round(budget), est=round(est))
        )
        bom.append(
            BomLine(
                group="camera_switch",
                category=t.pick(cat_defs["camera_switch"].label),
                model=cam.model,
                qty=cam.count,
                reason=t.t(
                    "reason.camera_switch",
                    ports=cat_defs["camera_switch"].endpoints_per_switch,
                    poe=poe_note,
                    note=note,
                ),
                details=[t.t("detail.camera_watts", w=camera_watts(ctx))],
                tags=["psu"] if cam.premium else [],
            )
        )

    if core.count:
        edge_total = sum(c.count for c in categories.values())
        if ctx.tier.core_redundant:
            reason = t.t(
                "reason.core_redundant", n=t.plural("plural.switches", edge_total), model=core.model, qty=core.count
            )
            tags = ["n+1", "tier"]
        else:
            reason = t.t("reason.core", n=t.plural("plural.switches", edge_total))
            tags = []
        if ctx.site.aggregation == "yes":
            details = [t.t("detail.agg_forced")]
        else:
            details = [t.t("detail.agg_auto", thr=ctx.rules.aggregation_auto_threshold)]
        bom.append(
            BomLine(
                group="core_switch",
                category=t.pick(cat_defs["core_switch"].label),
                model=core.model,
                qty=core.count,
                reason=reason,
                details=details,
                tags=tags,
            )
        )

    if fw is not None and fw.count:
        reason = " ".join(fw.reasons)
        tags = []
        if ctx.tier.fw_ha:
            reason += " " + t.t("reason.fw_ha")
            tags = ["ha", "tier"]
        bom.append(
            BomLine(
                group="firewall",
                category=t.t("cat.firewall"),
                model=fw.model,
                qty=fw.count,
                reason=reason,
                details=list(fw.skipped),
                tags=tags,
            )
        )


def _bom_tier_lines(
    ctx: _Ctx, bom: list[BomLine], power: PowerSummary, addons: dict[str, bool], rack: RackSummary
) -> None:
    t = ctx.t
    edge_or_fw = any(line.group in ("firewall", "wifi_switch", "access_switch", "camera_switch") for line in bom)
    if not edge_or_fw:
        return
    in_racks = [it.id for p in rack.plans for it in p.items]
    ups_in_racks = sum(1 for i in in_racks if i.startswith("ups:"))
    if (ctx.tier.ups or addons["ups"]) and (ups_in_racks or not rack.plans):
        if ctx.rules.power_model == "legacy":
            bom.append(
                BomLine(
                    group="power",
                    category=t.t("cat.ups"),
                    model=t.t("model.ups_todo"),
                    qty=1,
                    reason=t.t("reason.ups_legacy", w=round(power.legacy_w)),
                    tags=["tier"] if ctx.tier.ups else ["addon"],
                )
            )
        else:
            qty = 1
            model = power.ups_model
            ups_dev = ctx.catalog.models.get(model)
            if ups_dev and ups_dev.ups_va and power.ups_va > ups_dev.ups_va:
                qty = math.ceil(power.ups_va / ups_dev.ups_va)
            if rack.plans:
                qty = ups_in_racks  # what is left in the cabinets (the user may delete a UPS)
            bom.append(
                BomLine(
                    group="power",
                    category=t.t("cat.ups"),
                    model=model or t.t("model.ups_todo"),
                    qty=qty,
                    reason=t.t(
                        "reason.ups",
                        w=round(power.total_w),
                        eq=round(power.equipment_w),
                        poe=round(power.poe_w),
                        va=power.ups_va,
                        head=round(ctx.rules.ups_headroom * 100),
                    ),
                    details=[t.t("detail.ups_legacy", w=round(power.legacy_w))],
                    tags=["tier"] if ctx.tier.ups else ["addon"],
                )
            )
    if ctx.tier.oob and ctx.has_firewall and ("oob" in in_racks or not rack.plans):
        bom.append(
            BomLine(
                group="power", category=t.t("cat.oob"), model="OOB-LTE", qty=1, reason=t.t("reason.oob"), tags=["tier"]
            )
        )


def _bom_transceivers(
    ctx: _Ctx,
    bom: list[BomLine],
    categories: dict[str, CategoryResult],
    core: CategoryResult,
    fw: FirewallChoice | None,
    rack: RackSummary,
) -> None:
    """DACs per number of switches on the floor, a transceiver at every fibre link end."""
    t, rules = ctx.t, ctx.rules
    switches = sum(c.count for c in categories.values()) + core.count
    fw_count = fw.count if fw else 0
    fw_dev = ctx.catalog.models.get(fw.model) if fw else None
    fw_has_10g = bool(fw_dev and fw_dev.firewall and fw_dev.firewall.ports_10g > 0)
    ps = rack.passive
    fspec = ctx.catalog.passive.fiber.get(ps.fiber_type)

    def add(model: str, qty: int, reason: str) -> None:
        if qty > 0:
            bom.append(
                BomLine(
                    group="transceiver",
                    category=t.t("cat.transceivers"),
                    model=model,
                    qty=qty,
                    reason=reason,
                    details=[
                        t.t("detail.dac_rule", short=rules.dac_short_per_switches, long=rules.dac_long_per_switches)
                    ],
                    tags=["addon"],
                )
            )

    if switches:
        add(
            rules.dac_short_model,
            ceil_div(switches, rules.dac_short_per_switches),
            t.t("reason.dac_short_n", per=rules.dac_short_per_switches, n=switches),
        )
        add(
            rules.dac_long_model,
            ceil_div(switches, rules.dac_long_per_switches),
            t.t("reason.dac_long_n", per=rules.dac_long_per_switches, n=switches),
        )
    if ps.fiber_ends:
        add(
            fspec.transceiver if fspec else "FN-TRAN-SFP+SR",
            ps.fiber_ends,
            t.t("reason.sr_odf", n=ps.fiber_ends, fiber=t.pick(fspec.label) if fspec else ""),
        )
    if fw_count and not fw_has_10g and not core.count and switches:
        add("FN-TRAN-GC", fw_count, t.t("reason.gc", model=fw.model if fw else ""))


def _finalize_bom(ctx: _Ctx, bom: list[BomLine]) -> None:
    """Stable keys, catalog data (prices, codes, units) and the user's manual quantities/prices."""
    t, catalog = ctx.t, ctx.catalog
    seen: dict[str, int] = {}
    for line in bom:
        base = f"{line.group}:{line.model}"
        seen[base] = n = seen.get(base, 0) + 1
        line.key = base if n == 1 else f"{base}#{n}"
        dev = catalog.models.get(line.model)
        if dev is not None:
            line.unit_price = dev.price
            line.price_min = dev.price_min
            line.code = dev.code
            line.unit = dev.unit
            if not line.description:
                line.description = t.pick(dev.name)
        line.calc_qty = ctx.calc_counts.get(line.key, line.qty)
        ov = ctx.site.bom.overrides.get(line.key)
        if ov is None:
            continue
        if ov.qty is not None and line.qty is not None:
            line.qty = ov.qty
        if ov.price is not None:
            line.unit_price = ov.price
        if ov.price_min is not None:
            line.price_min = ov.price_min
        if line.qty != line.calc_qty or ov.price is not None or ov.price_min is not None:
            line.manual = True
            line.tags.append("manual")


def _bom_custom(ctx: _Ctx, bom: list[BomLine]) -> None:
    """Lines the user added by hand (from the catalog or free text)."""
    t = ctx.t
    for c in ctx.site.bom.custom:
        dev = ctx.catalog.models.get(c.model)
        bom.append(
            BomLine(
                group="custom",
                category=t.t(f"section.{c.section}"),
                model=c.model or c.name or "—",
                qty=c.qty,
                reason=t.t("reason.custom"),
                description=c.name or (t.pick(dev.name) if dev else ""),
                tags=["manual"],
                unit_price=c.price if c.price is not None else (dev.price if dev else None),
                price_min=c.price_min if c.price_min is not None else (dev.price_min if dev else None),
                key=f"custom:{c.id}",
                code=c.code or (dev.code if dev else ""),
                unit=c.unit or (dev.unit if dev else "шт."),
                calc_qty=c.qty,
                manual=True,
            )
        )


def _bom_cabling(ctx: _Ctx, bom: list[BomLine], rack: RackSummary) -> None:
    t, pr, ps = ctx.t, ctx.catalog.passive, rack.passive
    if ps.copper_links <= 0 and ps.fiber_links <= 0 and ps.housings_12 <= 0:
        return
    avg = ctx.site.avg_cable_run_m or ctx.rules.avg_cable_run_m_default
    copper = t.t("cat.copper")

    def add(group: str, category: str, model: str, qty: int, reason: str, details: list[str] | None = None) -> None:
        if qty > 0:
            bom.append(
                BomLine(
                    group=group,
                    category=category,
                    model=model,
                    qty=qty,
                    reason=reason,
                    details=details or [],
                    tags=["addon"],
                )
            )

    if ps.copper_links:
        add(
            "cabling",
            copper,
            pr.cable,
            ps.cable_drums,
            t.t(
                "reason.cable",
                n=ps.copper_links,
                avg=avg,
                slack=pr.cable_slack_m,
                m=ps.cable_m,
                box=pr.cable_drum_m,
            ),
            [t.t("detail.cable_cat6a")],
        )
        add(
            "cabling",
            copper,
            pr.jack,
            ps.jack_packs,
            t.t("reason.jacks", n=ps.copper_links, jacks=ps.jacks, pack=pr.jack_pack),
        )
        add(
            "cabling",
            copper,
            pr.cord_rack,
            ps.cords_rack,
            t.t("reason.cords_rack", links=ps.copper_links, dev=ps.device_links),
        )
        add("cabling", copper, pr.cord_user, ps.cords_user, t.t("reason.cords_user", n=ps.sockets))
    # panels and organizers: exactly what stands in the cabinets
    add("cabling", copper, pr.panel, ps.panels, t.t("reason.patch_panels_rack", n=ps.panels))
    add("cabling", copper, pr.manager, ps.managers, t.t("reason.cable_managers"))

    fspec = ctx.catalog.passive.fiber.get(ps.fiber_type)
    if (ps.fiber_links or ps.housings_12) and fspec is not None:
        fiber = t.t("cat.fiber")
        flabel = t.pick(fspec.label)
        choice = (
            t.t("detail.fiber_auto_links", limit=fspec.max_10g_m, type=flabel)
            if ps.fiber_auto
            else t.t("detail.fiber_manual", type=flabel)
        )
        add(
            "fiber",
            fiber,
            fspec.cable,
            ps.fiber_m,
            t.t(
                "reason.fiber_cable_links",
                links=ps.fiber_links,
                f=fspec.cable_fibers,
                type=flabel,
                slack=pr.fiber_slack_m,
                m=ps.fiber_m,
            ),
            [choice],
        )
        add("fiber", fiber, fspec.housing_12, ps.housings_12, t.t("reason.odf", n=ps.housings_12))
        add("fiber", fiber, pr.splice_protector, ps.splices, t.t("reason.splices", n=ps.splices))
        add("fiber", fiber, fspec.cord, ps.fiber_cords, t.t("reason.fiber_cords_odf", n=ps.fiber_cords))


def _bom_rack(ctx: _Ctx, bom: list[BomLine], rack: RackSummary) -> None:
    t, pr = ctx.t, ctx.catalog.passive
    by_model: dict[str, list[str]] = {}
    for plan in rack.plans:
        if plan.model:
            by_model.setdefault(plan.model, []).append(plan.name)
    for model, names in by_model.items():
        plans = [p for p in rack.plans if p.model == model]
        used = sum(p.used_u for p in plans)
        size = plans[0].size_u
        bom.append(
            BomLine(
                group="rack",
                category=t.t("cat.rack"),
                model=model,
                qty=len(names),
                reason=t.t(
                    "reason.rack",
                    names=", ".join(names),
                    used=used,
                    size=size,
                    pct=round(ctx.rules.rack_spare_ratio * 100),
                ),
                details=[t.t("detail.rack_plan", name=p.name, used=p.used_u, free=p.free_u) for p in plans],
                tags=["addon"],
            )
        )
    if rack.passive.pdus:
        bom.append(
            BomLine(
                group="rack",
                category=t.t("cat.rack"),
                model=pr.pdu,
                qty=rack.passive.pdus,
                reason=t.t("reason.pdu", n=pr.pdu_outlets) + (" " + t.t("reason.pdu_ab") if ctx.dual_psu else ""),
                tags=["addon"],
            )
        )


def _bom_rack_devices(ctx: _Ctx, bom: list[BomLine], rack: RackSummary) -> None:
    """Devices the user put into a cabinet by hand: catalog ones (with code and price) and custom ones."""
    t = ctx.t
    found: dict[str, list[str]] = {}
    for plan in rack.plans:
        for it in plan.items:
            if not it.extra:
                continue
            if it.group == "device" and it.model:
                found.setdefault(it.model, []).append(plan.tag)
            elif it.group == "custom" and it.label:
                found.setdefault(it.label, []).append(plan.tag)
    for model, tags in found.items():
        bom.append(
            BomLine(
                group="rack_device",
                category=t.t("cat.rack_device"),
                model=model,
                qty=len(tags),
                reason=t.t("reason.rack_device", racks=", ".join(sorted(set(tags)))),
                tags=["manual"],
                manual=True,
            )
        )


def _bom_licensing(
    ctx: _Ctx,
    bom: list[BomLine],
    categories: dict[str, CategoryResult],
    core: CategoryResult,
    fw: FirewallChoice | None,
) -> None:
    t = ctx.t
    bundle = ctx.tier.fortiguard
    if bundle != "none" and fw is not None and fw.fits and fw.count:
        fw_dev = ctx.catalog.device(fw.model)
        sku = ""
        if fw_dev.firewall and fw_dev.firewall.sku_code:
            sku = f"FC-10-{fw_dev.firewall.sku_code}-{FORTIGUARD_SKU.get(bundle, '950')}-02-DD"
        bom.append(
            BomLine(
                group="license",
                category=t.t("cat.license"),
                model="FG-BUNDLE",
                qty=fw.count,
                description=t.t("license.bundle_desc", bundle=t.t(f"bundle.{bundle}"), model=fw.model),
                reason=t.t(
                    "reason.bundle",
                    bundle=t.t(f"bundle.{bundle}"),
                    tier=t.pick(ctx.tier.label),
                    content=t.t(f"bundle.{bundle}.content"),
                ),
                details=[t.t("detail.sku_hint", sku=sku)] if sku else [],
                tags=["license", "addon"],
            )
        )
    devices = sum(c.count for c in categories.values()) + core.count + ctx.counts.aps
    if ctx.tier.forticare != "none" and devices:
        level = t.t(f"forticare.{ctx.tier.forticare}")
        bom.append(
            BomLine(
                group="license",
                category=t.t("cat.license"),
                model="FC-SUPPORT",
                qty=devices,
                description=t.t("license.forticare_desc", level=level),
                reason=t.t("reason.forticare", level=level, sw=devices - ctx.counts.aps, ap=ctx.counts.aps),
                tags=["license", "addon"],
            )
        )


def _bom_spares(ctx: _Ctx, bom: list[BomLine]) -> None:
    t = ctx.t
    pct = ctx.tier.spare_percent
    if pct <= 0:
        return
    spares: dict[str, int] = {}
    for line in bom:
        if line.group in ("ap", "wifi_switch", "access_switch", "camera_switch") and line.qty:
            spares[line.model] = spares.get(line.model, 0) + line.qty
    for model, qty in spares.items():
        n = max(1, math.ceil(qty * pct / 100))
        bom.append(
            BomLine(
                group="spare",
                category=t.t("cat.spare"),
                model=model,
                qty=n,
                reason=t.t("reason.spare", pct=f"{pct:g}", qty=qty, tier=t.pick(ctx.tier.label)),
                tags=["spare", "tier"],
            )
        )


# =========================================================================================
# power, rack, uplinks
# =========================================================================================
def _counts_from_racks(
    rack: RackSummary, categories: dict[str, CategoryResult], core: CategoryResult, fw: FirewallChoice | None
) -> bool:
    """Equipment counts follow the cabinets: a switch or firewall the user deleted is not ordered.

    Returns True when anything changed.
    """
    placed: dict[str, int] = {}
    for plan in rack.plans:
        for it in plan.items:
            placed[it.group] = placed.get(it.group, 0) + 1
    changed = False
    for res in (*categories.values(), core):
        n = placed.get(res.key, 0)
        if n < res.count:
            res.count = n
            changed = True
    if fw is not None and fw.fits:
        n = placed.get("firewall", 0)
        if n < fw.count:
            fw.count = n
            changed = True
    return changed


def compute_power(
    ctx: _Ctx, categories: dict[str, CategoryResult], core: CategoryResult, fw: FirewallChoice | None
) -> PowerSummary:
    r = ctx.rules
    ps = PowerSummary()
    lp = r.legacy_power_w
    ps.legacy_w = (
        (categories["wifi_switch"].count + categories["access_switch"].count) * lp.switch_base
        + categories["camera_switch"].count * lp.switch_poe
        + core.count * lp.switch_base
        + (fw.count if fw else 0) * lp.firewall
    )
    eq = 0.0
    for res in (*categories.values(), core):
        if res.count and res.model in ctx.catalog.models:
            dev = ctx.catalog.device(res.model)
            w = (dev.power_base_w or 0) * res.count
            eq += w
            ps.breakdown.append((res.model, res.count, w))
    if fw and fw.fits:
        dev = ctx.catalog.device(fw.model)
        w = (dev.power_max_w or dev.power_base_w or 0) * fw.count
        eq += w
        ps.breakdown.append((fw.model, fw.count, w))
    manual: dict[str, int] = {}
    for model, dev in ctx.manual_devices:
        if not dev.ups_va:  # a hand-placed UPS feeds the cabinet, it does not load it
            manual[model] = manual.get(model, 0) + 1
    for model, n in manual.items():
        dev = ctx.catalog.device(model)
        if dev.kind == "firewall":
            per = dev.power_max_w or dev.power_base_w or 0
            ps.legacy_w += lp.firewall * n
        else:
            per = dev.power_base_w or dev.power_max_w or (dev.ap.power_w if dev.ap else 0)
            if dev.kind == "switch":
                ps.legacy_w += lp.switch_base * n
        if per:
            eq += per * n
            ps.breakdown.append((model, n, per * n))
    poe = categories["wifi_switch"].poe_load_w + categories["camera_switch"].poe_load_w
    ps.equipment_w = eq
    ps.poe_w = poe
    ps.total_w = eq + poe / r.poe_psu_efficiency
    if r.power_model == "legacy":
        ps.total_w = ps.legacy_w
    ps.heat_btu = ps.total_w * 3.412
    ps.ups_va = (
        int(math.ceil(ps.total_w / r.ups_power_factor * (1 + r.ups_headroom) / 100.0) * 100) if ps.total_w else 0
    )
    ups_models = ctx.catalog.ups_models()
    for model, dev in ups_models:
        if (dev.ups_va or 0) >= ps.ups_va:
            ps.ups_model = model
            break
    else:
        if ups_models:
            ps.ups_model = ups_models[-1][0]
    return ps


def compute_rack(
    ctx: _Ctx,
    categories: dict[str, CategoryResult],
    core: CategoryResult,
    fw: FirewallChoice | None,
    power: PowerSummary,
    addons: dict[str, bool],
) -> RackSummary:
    return plan_passive(ctx, categories, core, fw, power, addons)


def _uplink_checks(
    ctx: _Ctx, categories: dict[str, CategoryResult], core: CategoryResult, fw: FirewallChoice | None
) -> None:
    """Oversubscription per edge category: downlink capacity / uplink capacity."""
    fw_dev = ctx.catalog.models.get(fw.model) if fw and fw.fits else None
    if core.count:
        peer_speed = ctx.catalog.device(core.model).ports.speed_gbps  # type: ignore[union-attr]
    elif fw_dev and fw_dev.firewall and fw_dev.firewall.ports_10g > 0:
        peer_speed = 10.0
    else:
        peer_speed = 1.0
    links = core.uplinks_per_switch if core.count else 1
    for res in categories.values():
        if not res.count:
            continue
        res.uplinks_per_switch = links
        dev = ctx.catalog.device(res.model)
        if not dev.ports or not dev.uplinks:
            continue
        per = math.ceil(res.endpoints / res.count)
        if res.key == "wifi_switch":
            ep_speed = min(dev.ports.speed_gbps, _avg_ap_uplink(ctx))
        else:
            ep_speed = min(dev.ports.speed_gbps, 1.0)
        down = per * ep_speed
        up = links * min(dev.uplinks.speed_gbps, peer_speed)
        res.oversubscription = down / up if up else 0
        if res.oversubscription > ctx.rules.oversubscription_warn:
            ctx.check(
                Severity.WARNING,
                "OVERSUB",
                "check.oversub",
                "check.oversub_hint",
                res.key,
                cat=ctx.t.pick(ctx.catalog.categories[res.key].label),
                ratio=round(res.oversubscription, 1),
                limit=ctx.rules.oversubscription_warn,
            )


def _avg_ap_uplink(ctx: _Ctx) -> float:
    total = 0.0
    n = 0
    for g in ctx.counts.ap_groups:
        dev = ctx.catalog.models.get(ap_model_for(g, ctx.catalog))
        if dev and dev.ap:
            total += dev.ap.uplink_gbps * g.qty
            n += g.qty
    return total / n if n else 1.0


def _general_checks(ctx: _Ctx, bom: list[BomLine], rack: RackSummary) -> None:
    t = ctx.t
    seen: set[str] = set()
    third_party: list[str] = []
    for line in bom:
        dev = ctx.catalog.models.get(line.model)
        if dev is None or line.model in seen:
            continue
        seen.add(line.model)
        if dev.lifecycle != "active":
            line.tags.append("eoo")
            ctx.check(Severity.INFO, "EOO", "check.eoo", "check.eoo_hint", line.group, model=line.model)
        if dev.verified == "third_party" and dev.kind in ("switch", "firewall", "ap"):
            third_party.append(line.model)
            line.tags.append("unverified")
    if third_party:
        ctx.check(Severity.INFO, "UNVERIFIED", "check.unverified", "", "", models=", ".join(third_party))

    if ctx.site.reserve:
        ctx.check(Severity.INFO, "RESERVE", "check.reserve", "", "", pct=round((ctx.counts.reserve_factor - 1) * 100))
    if rack.idf_count > 1:
        ctx.check(
            Severity.WARNING,
            "IDF",
            "check.idf",
            "check.idf_hint",
            "rack",
            run=ctx.site.max_cable_run_m,
            limit=ctx.rules.copper_max_m,
            n=t.plural("plural.closets", rack.idf_count),
        )
    for plan in rack.plans:
        if plan.used_u > plan.size_u:
            ctx.check(
                Severity.ERROR,
                "RACK_FULL",
                "check.rack_full",
                "check.rack_full_hint",
                "rack",
                name=plan.name,
                used=plan.used_u,
                size=plan.size_u,
            )
    for problem in rack.layout_problems:
        ctx.checks.append(Check(Severity.WARNING, "RACK_LAYOUT", problem, t.t("check.rack_layout_hint"), "rack"))
    if ctx.tier.dual_wan and any(line.group == "firewall" for line in bom):
        ctx.check(Severity.INFO, "DUAL_WAN", "check.dual_wan", "", "firewall")

    hd = ctx.rules.high_density_clients_per_ap
    for g in ctx.counts.ap_groups:
        model = ap_model_for(g, ctx.catalog)
        zone = ctx.catalog.ap_zones.get(g.zone)
        clients = g.clients_per_ap if g.clients_per_ap is not None else (zone.clients_per_ap if zone else 0)
        hd_zone = ctx.catalog.ap_zones.get("high_density")
        if clients > hd and hd_zone and model != hd_zone.model:
            ctx.check(
                Severity.INFO,
                "HIGH_DENSITY",
                "check.high_density",
                "",
                "ap",
                zone=g.name or (t.pick(zone.label) if zone else g.zone),
                clients=clients,
                thr=hd,
                model=hd_zone.model,
            )

    s = ctx.site
    if s.cameras > 5000 or s.sockets > 20000 or s.total_aps_raw > 5000:
        ctx.check(Severity.WARNING, "SUSPICIOUS", "check.suspicious")
    if s.wifi_clients_expected and s.total_aps_raw and s.wifi_clients_expected / s.total_aps_raw > 60:
        ctx.check(
            Severity.WARNING,
            "CLIENTS_PER_AP",
            "check.clients_per_ap",
            "",
            "ap",
            per=round(s.wifi_clients_expected / s.total_aps_raw),
        )


# =========================================================================================
# IP plan
# =========================================================================================
def build_ip_plan(ctx: _Ctx, total_switches: int, fw: FirewallChoice | None) -> IpPlan:
    t = ctx.t
    rules = ctx.rules.ip
    ip = ctx.site.ip
    counts = ctx.counts
    wifi_hosts = counts.wifi_clients or counts.aps * ctx.rules.wifi_clients_per_ap_default
    fw_count = fw.count if fw else 0
    # the firewall floor plans the VLANs of the whole location
    sources = {
        "sockets": counts.sockets + ctx.other("sockets"),
        "wifi": wifi_hosts + ctx.other("wifi"),
        "guest": ctx.site.guest_clients,
        "cameras": counts.cameras + ctx.other("cameras"),
        "iot": ctx.site.iot_devices,
        "mgmt": total_switches
        + fw_count
        + counts.aps
        + ctx.manual_count("switch", "firewall", "ap")
        + ctx.other("switches")
        + ctx.other("aps"),
    }
    requests: list[SegmentRequest] = []
    for seg in rules.segments:
        if seg.source == "voice":  # IP phones are not used any more
            continue
        hosts = sources.get(seg.source, 0)
        explicit = ip.segments.get(seg.id)
        enabled = explicit if explicit is not None else hosts > 0
        if not enabled:
            continue
        vlan = ip.vlan_overrides.get(seg.id, seg.vlan)
        name = ip.name_overrides.get(seg.id) or t.pick(seg.label)
        requests.append(SegmentRequest(id=seg.id, name=name, vlan=vlan, hosts=hosts, dhcp=seg.dhcp))
    for c in ip.custom:
        requests.append(SegmentRequest(id=c.id, name=c.name, vlan=c.vlan, hosts=c.hosts, dhcp=c.dhcp, custom=True))
    texts = {
        "note": t.t("ip.note", pct=round(rules.buffer * 100)),
        "no_dhcp": t.t("ip.static"),
        "overflow": t.t("ip.overflow"),
        "bad_network": t.t("ip.bad_network"),
        "bad_template": t.t("ip.bad_template"),
        "too_small": t.t("ip.too_small", need="{need}", cap="{cap}"),
        "overlap": t.t("ip.overlap", a="{a}", b="{b}"),
    }
    if ctx.site.location_id is not None:
        plan = plan_by_template(requests, rules, ctx.site.location_id, ip.prefix_overrides, texts)
    else:
        plan = plan_segments(requests, rules, ip.base_network, texts, ip.prefix_overrides)
    vlans: dict[int, str] = {}
    for seg in plan.segments:
        if seg.vlan in vlans:
            ctx.check(
                Severity.ERROR, "VLAN_DUP", "check.vlan_dup", "", "ip", vlan=seg.vlan, a=vlans[seg.vlan], b=seg.name
            )
        vlans[seg.vlan] = seg.name
        if seg.too_small:
            ctx.check(
                Severity.WARNING,
                "IP_SMALL",
                "check.ip_small",
                "check.ip_small_hint",
                "ip",
                name=seg.name,
                vlan=seg.vlan,
            )
    if plan.error:
        ctx.check(Severity.ERROR, "IP_PLAN", "check.ip_plan", "", "ip", error=plan.error)
    return plan


def fortios_at_least(version: str, minimum: str) -> bool:
    return parse_version(version) >= parse_version(minimum)


__all__ = [
    "EDGE_CATEGORIES",
    "addon_enabled",
    "ap_model_for",
    "apply_reserve",
    "pick_firewall",
    "size_site",
]
