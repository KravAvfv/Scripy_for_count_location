"""The sizing engine: ``SiteInput`` + ``Catalog`` -> ``SiteResult``.

Pure, deterministic and side-effect free; safe to call on every keystroke. All tunable
numbers come from ``catalog.rules`` / ``catalog.tiers``; all user-facing text comes from the
i18n tables, so the same engine serves the GUI, the CLI and the exporters.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from ..i18n import Translator
from .catalog import EDGE_CATEGORIES, Catalog, Category, Device, Tier, parse_version
from .ipplan import SegmentRequest, plan_segments
from .models import (
    ADDON_KEYS,
    ApGroup,
    BomLine,
    CategoryResult,
    Check,
    EffectiveCounts,
    FirewallChoice,
    IpPlan,
    PowerSummary,
    RackSummary,
    Severity,
    SiteInput,
    SiteResult,
)

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
    _poe_checks(ctx, res, dev, label)
    return res


# =========================================================================================
# aggregation / core
# =========================================================================================
def size_core(ctx: _Ctx, edge_count: int, fw_count_hint: int) -> CategoryResult:
    cat = ctx.catalog.categories["core_switch"]
    res = CategoryResult(key="core_switch", endpoints=edge_count, endpoints_per_switch=cat.endpoints_per_switch)
    mode = ctx.site.aggregation
    need = mode == "yes" or (mode == "auto" and edge_count >= ctx.rules.aggregation_auto_threshold)
    if not need:
        if mode == "no" and edge_count > ctx.rules.aggregation_auto_threshold:
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
    res.count = groups * per_group
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
    if key == "spares":
        return extended and tier.spare_percent > 0
    if key == "ups":
        return False
    if key == "management":
        return extended and (tier.fw_ha or tier.ups)
    return extended


def resolve_addons(site: SiteInput, tier: Tier) -> dict[str, bool]:
    return {k: addon_enabled(site, k, tier) for k in ADDON_KEYS}


def size_site(site: SiteInput, catalog: Catalog, lang: str = "uk") -> SiteResult:
    """Run the full sizing for one location."""
    t = Translator(lang)
    tier = catalog.tier(site.tier)
    checks: list[Check] = []
    counts = apply_reserve(site, catalog)

    if site.redundant_psu is None:
        dual_psu, confirmed = tier.dual_psu, False
    else:
        dual_psu, confirmed = site.redundant_psu, True
    ctx = _Ctx(site=site, catalog=catalog, t=t, tier=tier, counts=counts, dual_psu=dual_psu, checks=checks)
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
    core = size_core(ctx, edge, fw_count_hint)
    total_switches = edge + core.count

    firewall: FirewallChoice | None = None
    if total_switches > 0:
        firewall = pick_firewall(ctx, total_switches, counts.aps, requires_10g=core.count > 0)
        if not firewall.fits:
            ctx.check(Severity.ERROR, "FW_NONE", "check.fw_none", "check.fw_none_hint", "firewall", need=total_switches)
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
    _bom_equipment(ctx, bom, categories, core, firewall)
    power = compute_power(ctx, categories, core, firewall)
    rack = compute_rack(ctx, categories, core, firewall, power, addons)
    _bom_tier_lines(ctx, bom, power, addons)
    if addons["transceivers"]:
        _bom_transceivers(ctx, bom, categories, core, firewall, rack)
    if addons["cabling"]:
        _bom_cabling(ctx, bom, rack)
    if addons["rack"] and total_switches > 0:
        _bom_rack(ctx, bom, rack)
    if addons["licensing"] and firewall is not None:
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
    for line in bom:
        dev = catalog.models.get(line.model)
        if dev is not None:
            line.unit_price = dev.price
            if not line.description:
                line.description = t.pick(dev.name)

    ip_plan = build_ip_plan(ctx, total_switches, firewall) if site.mode == "extended" else None

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

    if fw is not None:
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


def _bom_tier_lines(ctx: _Ctx, bom: list[BomLine], power: PowerSummary, addons: dict[str, bool]) -> None:
    t = ctx.t
    edge_or_fw = any(line.group in ("firewall", "wifi_switch", "access_switch", "camera_switch") for line in bom)
    if not edge_or_fw:
        return
    if ctx.tier.ups or addons["ups"]:
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
    if ctx.tier.oob:
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
    t = ctx.t
    edge = sum(c.count for c in categories.values())
    if edge == 0 and core.count == 0:
        return
    fw_count = fw.count if fw else 0
    fw_dev = ctx.catalog.models.get(fw.model) if fw else None
    fw_has_10g = bool(fw_dev and fw_dev.firewall and fw_dev.firewall.ports_10g > 0)
    remote = rack.idf_count > 1
    dac = sr = gc = 0
    details: list[str] = []
    if core.count:
        links_edge = sum(c.count * core.uplinks_per_switch for c in categories.values())
        if remote:
            sr += links_edge * 2
        else:
            dac += links_edge
        dac += core.count * max(fw_count, 1) if fw_has_10g else 0
        icl = ctx.rules.core_icl_links * (core.count // 2) if ctx.tier.core_redundant else 0
        dac += icl
        details.append(t.t("detail.links_core", edge=links_edge, fw=core.count * max(fw_count, 1), icl=icl))
    else:
        if fw_has_10g:
            links = edge
            if remote:
                sr += links * 2
            else:
                dac += links
        else:
            gc += edge
        details.append(t.t("detail.links_direct", edge=edge))
    if dac:
        bom.append(
            BomLine(
                group="transceiver",
                category=t.t("cat.transceivers"),
                model="FN-CABLE-SFP+3",
                qty=dac,
                reason=t.t("reason.dac"),
                details=details,
                tags=["addon"],
            )
        )
    if sr:
        bom.append(
            BomLine(
                group="transceiver",
                category=t.t("cat.transceivers"),
                model="FN-TRAN-SFP+SR",
                qty=sr,
                reason=t.t("reason.sr", idf=rack.idf_count),
                details=details,
                tags=["addon"],
            )
        )
    if gc:
        bom.append(
            BomLine(
                group="transceiver",
                category=t.t("cat.transceivers"),
                model="FN-TRAN-GC",
                qty=gc,
                reason=t.t("reason.gc", model=fw.model if fw else ""),
                details=details,
                tags=["addon"],
            )
        )


def _bom_cabling(ctx: _Ctx, bom: list[BomLine], rack: RackSummary) -> None:
    t = ctx.t
    if rack.copper_endpoints <= 0:
        return
    avg = ctx.site.avg_cable_run_m or ctx.rules.avg_cable_run_m_default
    bom.append(
        BomLine(
            group="cabling",
            category=t.t("cat.cabling"),
            model="PP-24-C6A",
            qty=rack.patch_panels,
            reason=t.t("reason.patch_panels", n=rack.copper_endpoints, ports=ctx.rules.patch_panel_ports),
            tags=["addon"],
        )
    )
    bom.append(
        BomLine(
            group="cabling",
            category=t.t("cat.cabling"),
            model="PC-C6A-2M",
            qty=rack.copper_endpoints,
            reason=t.t("reason.patch_cords"),
            tags=["addon"],
        )
    )
    bom.append(
        BomLine(
            group="cabling",
            category=t.t("cat.cabling"),
            model="CBL-C6A-305",
            qty=rack.cable_boxes,
            reason=t.t("reason.cable", n=rack.copper_endpoints, avg=avg, m=rack.cable_m, box=ctx.rules.cable_box_m),
            tags=["addon"],
        )
    )
    managers = rack.units_managers
    if managers:
        bom.append(
            BomLine(
                group="cabling",
                category=t.t("cat.cabling"),
                model="CM-1U",
                qty=managers,
                reason=t.t("reason.cable_managers"),
                tags=["addon"],
            )
        )


def _bom_rack(ctx: _Ctx, bom: list[BomLine], rack: RackSummary) -> None:
    t = ctx.t
    if rack.rack_model:
        bom.append(
            BomLine(
                group="rack",
                category=t.t("cat.rack"),
                model=rack.rack_model,
                qty=rack.rack_count,
                reason=t.t(
                    "reason.rack" if rack.rack_count == 1 else "reason.rack_multi",
                    used=rack.units_total,
                    spare=rack.units_with_spare,
                    size=rack.rack_size_u,
                    n=rack.rack_count,
                    pct=round(ctx.rules.rack_spare_ratio * 100),
                ),
                tags=["addon"],
            )
        )
    if rack.idf_count > 1:
        small = ctx.catalog.rack_models()[0][0] if ctx.catalog.rack_models() else "RACK-12U"
        bom.append(
            BomLine(
                group="rack",
                category=t.t("cat.rack"),
                model=small,
                qty=rack.idf_count - 1,
                reason=t.t("reason.rack_idf", n=rack.idf_count - 1),
                tags=["addon"],
            )
        )


def _bom_licensing(
    ctx: _Ctx, bom: list[BomLine], categories: dict[str, CategoryResult], core: CategoryResult, fw: FirewallChoice
) -> None:
    t = ctx.t
    bundle = ctx.tier.fortiguard
    if bundle != "none" and fw.fits:
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
    r = ctx.rules
    rs = RackSummary()
    for res in (*categories.values(), core):
        if res.count and res.model in ctx.catalog.models:
            rs.units_equipment += ctx.catalog.device(res.model).rack_units * res.count
    if fw and fw.fits:
        rs.units_equipment += ctx.catalog.device(fw.model).rack_units * fw.count
    rs.copper_endpoints = ctx.counts.sockets + ctx.counts.cameras + ctx.counts.aps
    rs.patch_panels = ceil_div(rs.copper_endpoints, r.patch_panel_ports)
    rs.units_panels = rs.patch_panels
    rs.units_managers = math.ceil(rs.patch_panels * r.cable_manager_per_panel)
    if ctx.tier.ups or addons.get("ups"):
        ups = ctx.catalog.models.get(power.ups_model)
        rs.units_ups = ups.rack_units if ups else 2
    if ctx.tier.oob:
        rs.units_other += 1
    rs.units_total = rs.units_equipment + rs.units_panels + rs.units_managers + rs.units_ups + rs.units_other
    rs.units_with_spare = math.ceil(rs.units_total * (1 + r.rack_spare_ratio))
    racks = ctx.catalog.rack_models()
    for model, dev in racks:
        if (dev.rack_size_u or 0) >= rs.units_with_spare:
            rs.rack_model, rs.rack_size_u = model, dev.rack_size_u or 0
            break
    else:
        if racks:
            rs.rack_model, rs.rack_size_u = racks[-1][0], racks[-1][1].rack_size_u or 0
    if rs.rack_size_u:
        rs.rack_count = max(1, math.ceil(rs.units_with_spare / rs.rack_size_u))
    run = ctx.site.max_cable_run_m
    rs.idf_count = max(1, math.ceil(run / r.copper_max_m)) if run else 1
    avg = ctx.site.avg_cable_run_m or r.avg_cable_run_m_default
    rs.cable_m = rs.copper_endpoints * avg
    rs.cable_boxes = ceil_div(rs.cable_m, r.cable_box_m)
    return rs


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
    counts = ctx.counts
    wifi_hosts = counts.wifi_clients or counts.aps * ctx.rules.wifi_clients_per_ap_default
    fw_count = fw.count if fw else 0
    sources = {
        "sockets": counts.sockets,
        "voice": ctx.site.voice_phones,
        "wifi": wifi_hosts,
        "guest": ctx.site.guest_clients,
        "cameras": counts.cameras,
        "iot": ctx.site.iot_devices,
        "mgmt": total_switches + fw_count + counts.aps,
    }
    requests: list[SegmentRequest] = []
    for seg in rules.segments:
        hosts = sources.get(seg.source, 0)
        explicit = ctx.site.ip.segments.get(seg.id)
        enabled = explicit if explicit is not None else hosts > 0
        if not enabled or hosts <= 0:
            continue
        vlan = ctx.site.ip.vlan_overrides.get(seg.id, seg.vlan)
        requests.append(SegmentRequest(id=seg.id, name=t.pick(seg.label), vlan=vlan, hosts=hosts, dhcp=seg.dhcp))
    texts = {
        "note": t.t("ip.note", pct=round(rules.buffer * 100)),
        "no_dhcp": t.t("ip.static"),
        "overflow": t.t("ip.overflow"),
        "bad_network": t.t("ip.bad_network"),
    }
    plan = plan_segments(requests, rules, ctx.site.ip.base_network, texts)
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
