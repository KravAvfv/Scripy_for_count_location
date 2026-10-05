"""Passive infrastructure: structured cabling, fibre backbone, rack power and cabinet layout.

Turns the sized equipment into closets (MDF + remote IDFs), lays every closet out in 24U/42U
cabinets (switch + its patch panels + cable manager kept together), and counts the copper and
fibre parts. Part numbers come from ``catalog.passive`` (Corning by default).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .catalog import FiberSpec
from .models import CategoryResult, FirewallChoice, PassiveSummary, PowerSummary, RackItem, RackPlan, RackSummary

if TYPE_CHECKING:
    from .sizing import _Ctx


def _ceil_div(a: float, b: float) -> int:
    return math.ceil(a / b) if a > 0 and b > 0 else 0


@dataclass
class _Block:
    """Items that must stay together in one cabinet (e.g. patch panels + manager + switch)."""

    items: list[tuple[int, str, str, str]] = field(default_factory=list)
    """(height U, label, colour group, model)"""
    cords: int = 0
    """Power cords per feed (A/B)."""

    @property
    def height(self) -> int:
        return sum(h for h, *_ in self.items)


@dataclass
class _Closet:
    index: int
    top: list[_Block] = field(default_factory=list)
    mid: list[_Block] = field(default_factory=list)
    bottom: list[_Block] = field(default_factory=list)
    uplinks: int = 0
    """Links from this closet's switches towards the core / firewall."""


def fiber_choice(ctx: _Ctx, backbone: list[int]) -> tuple[str, FiberSpec | None, bool]:
    """Return (key, spec, chosen automatically)."""
    fibers = ctx.catalog.passive.fiber
    if not fibers:
        return "", None, True
    wanted = ctx.site.fiber_type if ctx.site.fiber_type != "auto" else ctx.catalog.passive.fiber_default
    if wanted != "auto" and wanted in fibers:
        return wanted, fibers[wanted], ctx.site.fiber_type == "auto"
    longest = max(backbone, default=0) + ctx.catalog.passive.fiber_slack_m
    for key, spec in sorted(fibers.items(), key=lambda kv: kv[1].max_10g_m):
        if longest <= spec.max_10g_m:
            return key, spec, True
    key = max(fibers, key=lambda k: fibers[k].max_10g_m)
    return key, fibers[key], True


def plan_passive(
    ctx: _Ctx,
    categories: dict[str, CategoryResult],
    core: CategoryResult,
    fw: FirewallChoice | None,
    power: PowerSummary,
    addons: dict[str, bool],
) -> RackSummary:
    t, cat, r, pr = ctx.t, ctx.catalog, ctx.rules, ctx.catalog.passive
    rs = RackSummary()
    ps = PassiveSummary()
    rs.passive = ps

    # ---- closets ------------------------------------------------------------------------
    run = ctx.site.max_cable_run_m
    n_closets = max(1, math.ceil(run / r.copper_max_m)) if run else 1
    rs.idf_count = n_closets
    closets = [_Closet(i) for i in range(n_closets)]
    if n_closets > 1:
        ps.backbone_m = [
            ctx.site.fiber_backbone_m or max(1, round(k * (run or 0) / n_closets)) for k in range(1, n_closets)
        ]
    links_per_switch = core.uplinks_per_switch if core.count else 1
    dual_feed = ctx.dual_psu

    def psu_cords(model: str) -> int:
        dev = cat.models.get(model)
        return dev.psu.count if dev and dev.psu else 1

    def units(model: str, default: int = 1) -> int:
        dev = cat.models.get(model)
        return dev.rack_units if dev and dev.rack_units else default

    # ---- switch blocks (assigned to the least loaded closet) ----------------------------
    load = [0] * n_closets
    for key in ("wifi_switch", "access_switch", "camera_switch"):
        res = categories[key]
        if not res.count:
            continue
        short = t.pick(cat.categories[key].short) or key
        base, extra = divmod(res.endpoints, res.count)
        for i in range(res.count):
            ep = base + (1 if i < extra else 0)
            ci = min(range(n_closets), key=lambda k: (load[k], k))
            load[ci] += 1
            block = _Block(cords=1)
            panels = _ceil_div(ep, pr.panel_ports)
            for _ in range(panels):
                block.items.append((units(pr.panel), "", "panel", pr.panel))
            managers = math.ceil(panels * r.cable_manager_per_panel) if panels else 0
            for _ in range(managers):
                block.items.append((1, t.t("rack.manager"), "manager", pr.manager))
            block.items.append((units(res.model), f"{res.model} · {short} #{i + 1}", key, res.model))
            ps.panels += panels
            ps.managers += managers
            closets[ci].mid.append(block)
            closets[ci].uplinks += links_per_switch

    # ---- fibre backbone -----------------------------------------------------------------
    fkey, fspec, fauto = fiber_choice(ctx, ps.backbone_m)
    ps.fiber_type, ps.fiber_auto = fkey, fauto
    mdf = closets[0]
    if fspec is not None and n_closets > 1:
        flabel = t.pick(fspec.label).split(" ")[0]
        for k in range(1, n_closets):
            links = closets[k].uplinks
            if not links:
                continue
            fibers = math.ceil(links * 2 * (1 + pr.fiber_spare_ratio))
            cables = _ceil_div(fibers, fspec.cable_fibers)
            n24, n12 = divmod(cables, 2) if fspec.cable_fibers == 12 else (0, cables)
            idf_name = t.t("rack.idf", n=k)
            for target, peer in ((mdf, idf_name), (closets[k], t.t("rack.mdf"))):
                block = _Block()
                for _ in range(n24):
                    block.items.append(
                        (
                            units(fspec.housing_24),
                            t.t("rack.fiber", f=24, type=flabel, to=peer),
                            "fiber",
                            fspec.housing_24,
                        )
                    )
                for _ in range(n12):
                    block.items.append(
                        (
                            units(fspec.housing_12),
                            t.t("rack.fiber", f=12, type=flabel, to=peer),
                            "fiber",
                            fspec.housing_12,
                        )
                    )
                target.top.append(block)
            ps.housings_24 += 2 * n24
            ps.housings_12 += 2 * n12
            ps.fiber_links += links
            ps.fiber_cables += cables
            ps.fiber_cores += cables * fspec.cable_fibers
            ps.fiber_m += cables * (ps.backbone_m[k - 1] + pr.fiber_slack_m)
            ps.splices += cables * fspec.cable_fibers * 2
            ps.fiber_cords += links * 2

    # ---- MDF core equipment -------------------------------------------------------------
    head = _Block()
    if ctx.tier.oob:
        head.items.append((units("OOB-LTE"), t.t("rack.oob"), "power", "OOB-LTE"))
        head.cords += 1
    if fw and fw.fits:
        for i in range(fw.count):
            suffix = f" #{i + 1}" if fw.count > 1 else ""
            head.items.append((units(fw.model), fw.model + suffix, "firewall", fw.model))
            head.cords += 1
    for i in range(core.count):
        head.items.append((units(core.model), f"{core.model} #{i + 1}", "core_switch", core.model))
        head.cords += 1
    if head.items:
        mdf.top.insert(0, head)
    if ctx.tier.ups or addons.get("ups"):
        ups_dev = cat.models.get(power.ups_model)
        qty = 1
        if ups_dev and ups_dev.ups_va and power.ups_va > ups_dev.ups_va:
            qty = math.ceil(power.ups_va / ups_dev.ups_va)
        block = _Block()
        for i in range(qty):
            suffix = f" #{i + 1}" if qty > 1 else ""
            block.items.append(
                (
                    (ups_dev.rack_units if ups_dev else 2) or 2,
                    (power.ups_model or "UPS") + suffix,
                    "power",
                    power.ups_model,
                )
            )
        mdf.bottom.append(block)
        rs.units_ups = block.height

    # ---- cabinets -----------------------------------------------------------------------
    racks = cat.rack_models()
    sizes = sorted({dev.rack_size_u or 0 for _, dev in racks if dev.rack_size_u})
    model_for = {dev.rack_size_u: model for model, dev in racks}
    feeds = 2 if dual_feed else 1

    for closet in closets:
        blocks_u = sum(b.height for b in (*closet.top, *closet.mid, *closet.bottom))
        if not blocks_u:
            continue
        need = math.ceil((blocks_u + feeds) * (1 + r.rack_spare_ratio))
        size = _pick_size(ctx.site.rack_size_u, need, sizes)
        usable = max(1, size - math.floor(size * r.rack_spare_ratio / (1 + r.rack_spare_ratio)))
        groups = _fill(closet, usable, feeds)
        if len(groups) > 1:
            # spread evenly instead of leaving the last cabinet almost empty
            total = blocks_u + feeds * len(groups)
            balanced = _fill(closet, min(usable, math.ceil(total / len(groups)) + 1), feeds)
            if len(balanced) == len(groups):
                groups = balanced
        for gi, group in enumerate(groups):
            if closet.index == 0:
                name = t.t("rack.mdf") + (f"-{gi + 1}" if len(groups) > 1 else "")
            else:
                name = t.t("rack.idf", n=closet.index) + (f".{gi + 1}" if len(groups) > 1 else "")
            plan = RackPlan(
                name=name, role="mdf" if closet.index == 0 else "idf", size_u=size, model=model_for.get(size, "")
            )
            cursor = size
            cords = 0
            for block in group:
                cords += block.cords
                for h, label, grp, model in block.items:
                    plan.items.append(RackItem(u=cursor - h + 1, height=h, label=label, group=grp, model=model))
                    cursor -= h
            bottom = 1
            if gi == 0:
                for block in closet.bottom:
                    for h, label, grp, model in block.items:
                        plan.items.append(RackItem(u=bottom, height=h, label=label, group=grp, model=model))
                        bottom += h
            pdus = feeds * max(1, _ceil_div(cords, pr.pdu_outlets)) if cords else 0
            for i in range(pdus):
                feed = "AB"[i % 2] if feeds == 2 else "A"
                plan.items.append(
                    RackItem(
                        u=bottom,
                        height=1,
                        label=t.t("rack.pdu", feed=feed, n=pr.pdu_outlets),
                        group="pdu",
                        model=pr.pdu,
                    )
                )
                bottom += 1
            ps.pdus += pdus
            plan.items.sort(key=lambda it: -it.u)
            panel_no = 0
            for it in plan.items:
                if it.group == "panel":
                    panel_no += 1
                    it.label = t.t("rack.panel", n=panel_no, ports=pr.panel_ports)
            rs.plans.append(plan)

    # ---- copper -------------------------------------------------------------------------
    counts = ctx.counts
    ps.sockets = counts.sockets
    ps.device_links = counts.cameras + counts.aps
    ps.copper_links = ps.sockets + ps.device_links
    avg = ctx.site.avg_cable_run_m or r.avg_cable_run_m_default
    ps.cable_m = ps.copper_links * (avg + pr.cable_slack_m)
    ps.cable_drums = _ceil_div(ps.cable_m, pr.cable_drum_m)
    ps.jacks = ps.copper_links * 2
    ps.jack_packs = _ceil_div(ps.jacks, pr.jack_pack)
    ps.outlets = _ceil_div(ps.sockets, pr.outlet_ports) + ps.device_links
    ps.cords_rack = ps.copper_links + ps.device_links
    ps.cords_user = ps.sockets

    # ---- summary (kept compatible with the previous RackSummary fields) -----------------
    equipment_groups = {"wifi_switch", "access_switch", "camera_switch", "core_switch", "firewall"}
    for plan in rs.plans:
        for it in plan.items:
            if it.group in equipment_groups:
                rs.units_equipment += it.height
            elif it.group == "panel":
                rs.units_panels += it.height
            elif it.group == "manager":
                rs.units_managers += it.height
            elif not (it.group == "power" and power.ups_model and it.model == power.ups_model):
                rs.units_other += it.height
    rs.units_total = sum(p.used_u for p in rs.plans)
    rs.units_with_spare = math.ceil(rs.units_total * (1 + r.rack_spare_ratio))
    mdf_plans = [p for p in rs.plans if p.role == "mdf"]
    if mdf_plans:
        rs.rack_model, rs.rack_size_u, rs.rack_count = mdf_plans[0].model, mdf_plans[0].size_u, len(mdf_plans)
    elif sizes:
        rs.rack_size_u, rs.rack_model = sizes[0], model_for.get(sizes[0], "")
    rs.patch_panels = ps.panels
    rs.copper_endpoints = ps.copper_links
    rs.cable_m = ps.cable_m
    rs.cable_boxes = ps.cable_drums
    return rs


def _fill(closet: _Closet, limit: int, feeds: int) -> list[list[_Block]]:
    """Greedy fill: the first cabinet holds the top blocks and the bottom reserve (UPS)."""
    groups: list[list[_Block]] = [list(closet.top)]
    used = sum(b.height for b in (*closet.top, *closet.bottom)) + feeds
    for block in closet.mid:
        if used + block.height > limit and groups[-1]:
            groups.append([])
            used = feeds
        groups[-1].append(block)
        used += block.height
    return groups


def _pick_size(preferred: int, need: int, sizes: list[int]) -> int:
    if not sizes:
        return preferred or 42
    if preferred:
        fitting = [s for s in sizes if s >= preferred]
        return fitting[0] if fitting else sizes[-1]
    for s in sizes:
        if s >= need:
            return s
    return sizes[-1]
