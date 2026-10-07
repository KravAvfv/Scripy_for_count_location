"""Passive infrastructure: structured cabling, fibre backbone, rack power and cabinet layout.

Turns the sized equipment into closets (MDF + remote IDFs) and lays every closet out in 24U/42U
cabinets following the house standard (top to bottom)::

    ODF / fibre panels
    firewall(s), core switch(es)
    organizer · patch panels · organizer · switch · organizer · patch panels · organizer · switch …
    PDUs, UPS

Every switch sits between two organizers and every group of patch panels sits between two
organizers; a 48-port switch gets one panel above and one below it. The user's manual layout
(:class:`~sitesizer.core.models.RackLayout`: dragged items, added/removed cabinets, extra
organizers…) is applied on top, then devices get their names (``BO123-5B-ASW01``) and the
copper and fibre parts are counted. Part numbers come from ``catalog.passive`` (Corning by default).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .catalog import FiberSpec
from .models import (
    CategoryResult,
    FirewallChoice,
    PassiveSummary,
    PowerSummary,
    RackItem,
    RackLayout,
    RackPlan,
    RackSummary,
)

if TYPE_CHECKING:
    from ..i18n import Translator
    from .sizing import _Ctx

EDGE_KEYS = ("wifi_switch", "access_switch", "camera_switch")
ROLE_CODES = {
    "wifi_switch": "WSW",
    "access_switch": "ASW",
    "camera_switch": "VSW",
    "core_switch": "CSW",
    "firewall": "FW",
}
"""Device name role codes: ``<site>-<floor><cabinet>-<ROLE><NN>``."""
PASSIVE_GROUPS = ("panel", "manager", "fiber", "pdu", "shelf", "blank", "custom", "device")
"""Item groups the user may delete from a cabinet (active equipment can only be moved)."""
EXTRA_GROUPS = {
    "manager": "manager",
    "panel": "panel",
    "shelf": "shelf",
    "blank": "blank",
    "odf": "fiber",
    "device": "device",
}
DEFAULT_USER_RACK_U = 42


def _ceil_div(a: float, b: float) -> int:
    return math.ceil(a / b) if a > 0 and b > 0 else 0


@dataclass
class _Unit:
    id: str
    height: int
    label: str
    group: str
    model: str = ""


@dataclass
class _Switch:
    id: str
    key: str
    model: str
    height: int
    panels: int


@dataclass
class _Closet:
    index: int
    switches: list[_Switch] = field(default_factory=list)
    top: list[_Unit] = field(default_factory=list)
    """Fibre housings (first cabinet of the closet)."""
    head: list[_Unit] = field(default_factory=list)
    """Firewalls, core switches, OOB router (MDF only)."""
    bottom: list[_Unit] = field(default_factory=list)
    """UPS (MDF only)."""
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


# =========================================================================================
# stack pattern: organizer · panels · organizer · switch · organizer · panels …
# =========================================================================================
def stack_units(switches: list[_Switch], panel_model: str, panel_u: int, manager_model: str) -> list[_Unit]:
    """Top-down units for a run of switches with their patch panels and organizers."""
    tokens: list[list[_Unit]] = []
    pending: list[_Unit] = []
    for sw in switches:
        upper = math.ceil(sw.panels / 2)
        panels = [_Unit(f"panel:{sw.id}:{k}", panel_u, "", "panel", panel_model) for k in range(1, sw.panels + 1)]
        group = pending + panels[:upper]
        if group:
            tokens.append(group)
        tokens.append([_Unit(sw.id, sw.height, "", sw.key, sw.model)])
        pending = panels[upper:]
    if pending:
        tokens.append(pending)
    out: list[_Unit] = []
    for tok in tokens:
        out.append(_Unit(f"org:{tok[0].id}", 1, "", "manager", manager_model))
        out.extend(tok)
    return out


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

    def units(model: str, default: int = 1) -> int:
        dev = cat.models.get(model)
        return dev.rack_units if dev and dev.rack_units else default

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

    # ---- switches (assigned to the least loaded closet) ---------------------------------
    load = [0] * n_closets
    for key in EDGE_KEYS:
        res = categories[key]
        if not res.count:
            continue
        base, extra = divmod(res.endpoints, res.count)
        for i in range(res.count):
            ep = base + (1 if i < extra else 0)
            ci = min(range(n_closets), key=lambda k: (load[k], k))
            load[ci] += 1
            panels = _ceil_div(ep, pr.panel_ports)
            closets[ci].switches.append(_Switch(f"{key}:{i + 1}", key, res.model, units(res.model), panels))
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
            for side, target, peer in ((0, mdf, idf_name), (1, closets[k], t.t("rack.mdf"))):
                for j in range(n24):
                    target.top.append(
                        _Unit(
                            f"fiber:{k}:{side}:24:{j + 1}",
                            units(fspec.housing_24),
                            t.t("rack.fiber", f=24, type=flabel, to=peer),
                            "fiber",
                            fspec.housing_24,
                        )
                    )
                for j in range(n12):
                    target.top.append(
                        _Unit(
                            f"fiber:{k}:{side}:12:{j + 1}",
                            units(fspec.housing_12),
                            t.t("rack.fiber", f=12, type=flabel, to=peer),
                            "fiber",
                            fspec.housing_12,
                        )
                    )
            ps.housings_24 += 2 * n24
            ps.housings_12 += 2 * n12
            ps.fiber_links += links
            ps.fiber_cables += cables
            ps.fiber_cores += cables * fspec.cable_fibers
            ps.fiber_m += cables * (ps.backbone_m[k - 1] + pr.fiber_slack_m)
            ps.splices += cables * fspec.cable_fibers * 2
            ps.fiber_cords += links * 2

    # ---- MDF core equipment -------------------------------------------------------------
    if ctx.tier.oob:
        mdf.head.append(_Unit("oob", units("OOB-LTE"), t.t("rack.oob"), "power", "OOB-LTE"))
    if fw and fw.fits:
        for i in range(fw.count):
            mdf.head.append(_Unit(f"firewall:{i + 1}", units(fw.model), fw.model, "firewall", fw.model))
    for i in range(core.count):
        mdf.head.append(_Unit(f"core_switch:{i + 1}", units(core.model), core.model, "core_switch", core.model))
    if ctx.tier.ups or addons.get("ups"):
        ups_dev = cat.models.get(power.ups_model)
        qty = 1
        if ups_dev and ups_dev.ups_va and power.ups_va > ups_dev.ups_va:
            qty = math.ceil(power.ups_va / ups_dev.ups_va)
        for i in range(qty):
            suffix = f" #{i + 1}" if qty > 1 else ""
            mdf.bottom.append(
                _Unit(
                    f"ups:{i + 1}",
                    (ups_dev.rack_units if ups_dev else 2) or 2,
                    (power.ups_model or "UPS") + suffix,
                    "power",
                    power.ups_model,
                )
            )
        rs.units_ups = sum(u.height for u in mdf.bottom)

    # ---- cabinets -----------------------------------------------------------------------
    racks = cat.rack_models()
    sizes = sorted({dev.rack_size_u or 0 for _, dev in racks if dev.rack_size_u})
    model_for = {dev.rack_size_u: model for model, dev in racks}
    feeds = 2 if ctx.dual_psu else 1
    panel_u = units(pr.panel)

    def cabinet_u(closet: _Closet, sws: list[_Switch], first: bool) -> int:
        body = sum(u.height for u in stack_units(sws, pr.panel, panel_u, pr.manager))
        extra = sum(u.height for u in (*closet.top, *closet.head, *closet.bottom)) if first else 0
        cords = len(sws) + (len(closet.head) if first else 0)
        pdus = feeds * max(1, _ceil_div(cords, pr.pdu_outlets)) if cords else 0
        return body + extra + pdus

    plans: list[RackPlan] = []
    for closet in closets:
        if not (closet.switches or closet.top or closet.head or closet.bottom):
            continue
        need = math.ceil(cabinet_u(closet, closet.switches, True) * (1 + r.rack_spare_ratio))
        size = _pick_size(ctx.site.rack_size_u, need, sizes)
        usable = max(1, size - math.floor(size * r.rack_spare_ratio / (1 + r.rack_spare_ratio)))
        groups = _fill(closet, usable, cabinet_u)
        if len(groups) > 1:
            # spread evenly instead of leaving the last cabinet almost empty
            per = math.ceil(len(closet.switches) / len(groups))
            balanced = [closet.switches[i : i + per] for i in range(0, len(closet.switches), per)]
            if len(balanced) == len(groups) and all(
                cabinet_u(closet, g, gi == 0) <= usable for gi, g in enumerate(balanced)
            ):
                groups = balanced
        for gi, sws in enumerate(groups):
            first = gi == 0
            if closet.index == 0:
                key = f"mdf-{gi + 1}"
                name = t.t("rack.mdf") + (f"-{gi + 1}" if len(groups) > 1 else "")
            else:
                key = f"idf{closet.index}-{gi + 1}"
                name = t.t("rack.idf", n=closet.index) + (f".{gi + 1}" if len(groups) > 1 else "")
            plan = RackPlan(
                name=name,
                role="mdf" if closet.index == 0 else "idf",
                size_u=size,
                model=model_for.get(size, ""),
                key=key,
            )
            seq = ([*closet.top, *closet.head] if first else []) + stack_units(sws, pr.panel, panel_u, pr.manager)
            cursor = size
            for unit in seq:
                plan.items.append(_item(unit, cursor - unit.height + 1))
                cursor -= unit.height
            bottom = 1
            if first:
                for unit in closet.bottom:
                    plan.items.append(_item(unit, bottom))
                    bottom += unit.height
            cords = len(sws) + (len(closet.head) if first else 0)
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
                        id=f"pdu:{key}:{i + 1}",
                    )
                )
                bottom += 1
            plans.append(plan)

    for i, plan in enumerate(plans):
        plan.letter = _letter(i)

    # ---- manual layout, names -----------------------------------------------------------
    rs.layout_problems = apply_layout(
        plans, ctx.site.layout, model_for, ctx.site.rack_size_u, t, {"manager": pr.manager, "panel": pr.panel}
    )
    if not ctx.site.layout.is_empty:
        rs.layout_problems += top_up_pdus(plans, feeds, pr.pdu_outlets, pr.pdu, set(ctx.site.layout.hidden), t)
    name_items(plans, ctx.site.layout, ctx.site.location_code, t, pr.panel_ports)
    for plan in plans:
        plan.items.sort(key=lambda it: -it.u)
    rs.plans = plans
    for plan in plans:
        for it in plan.items:
            if it.group == "pdu":
                ps.pdus += 1
            elif it.group == "panel" and it.model:
                ps.panels += 1
            elif it.group == "manager" and it.model:
                ps.managers += 1

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
    equipment_groups = {*EDGE_KEYS, "core_switch", "firewall"}
    for plan in rs.plans:
        for it in plan.items:
            if it.group in equipment_groups:
                rs.units_equipment += it.height
            elif it.group == "panel":
                rs.units_panels += it.height
            elif it.group == "manager":
                rs.units_managers += it.height
            elif not (it.group == "power" and it.id.startswith("ups:")):
                rs.units_other += it.height
    rs.units_total = sum(p.used_u for p in rs.plans)
    rs.units_with_spare = math.ceil(rs.units_total * (1 + r.rack_spare_ratio))
    mdf_plans = [p for p in rs.plans if p.role != "idf"]
    if mdf_plans:
        rs.rack_model, rs.rack_size_u, rs.rack_count = mdf_plans[0].model, mdf_plans[0].size_u, len(mdf_plans)
    elif sizes:
        rs.rack_size_u, rs.rack_model = sizes[0], model_for.get(sizes[0], "")
    rs.patch_panels = ps.panels
    rs.copper_endpoints = ps.copper_links
    rs.cable_m = ps.cable_m
    rs.cable_boxes = ps.cable_drums
    return rs


def _item(unit: _Unit, u: int) -> RackItem:
    return RackItem(u=u, height=unit.height, label=unit.label, group=unit.group, model=unit.model, id=unit.id)


def _letter(i: int) -> str:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return letters[i] if i < len(letters) else f"{letters[i // 26 - 1]}{letters[i % 26]}"


def _fill(closet: _Closet, limit: int, cabinet_u) -> list[list[_Switch]]:  # type: ignore[no-untyped-def]
    """Greedy fill: the first cabinet also holds the fibre panels, the core equipment and the UPS."""
    groups: list[list[_Switch]] = [[]]
    for sw in closet.switches:
        trial = [*groups[-1], sw]
        if groups[-1] and cabinet_u(closet, trial, len(groups) == 1) > limit:
            groups.append([sw])
        else:
            groups[-1] = trial
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


# =========================================================================================
# manual layout
# =========================================================================================
def _overlaps(a: RackItem, b: RackItem) -> bool:
    return a.u <= b.top and b.u <= a.top


def _fits(plan: RackPlan, it: RackItem) -> bool:
    return it.u >= 1 and it.top <= plan.size_u and not any(_overlaps(it, o) for o in plan.items if o is not it)


def _nearest_slot(plan: RackPlan, height: int, near: int) -> int | None:
    occupied = [False] * (plan.size_u + 2)
    for it in plan.items:
        for u in range(max(1, it.u), min(plan.size_u, it.top) + 1):
            occupied[u] = True
    best: int | None = None
    for lo in range(1, plan.size_u - height + 2):
        if all(not occupied[u] for u in range(lo, lo + height)) and (best is None or abs(lo - near) < abs(best - near)):
            best = lo
    return best


def apply_layout(
    plans: list[RackPlan],
    layout: RackLayout,
    model_for: dict[int | None, str],
    preferred_u: int,
    t: Translator,
    extra_models: dict[str, str] | None = None,
) -> list[str]:
    """Apply the user's manual layout to the automatic ``plans`` (in place).

    ``extra_models`` gives the part number of hand-added organizers / patch panels so they are
    counted in the bill of materials like the automatic ones.

    Returns human-readable problems (overlaps, items that no longer fit) for the checks panel.
    """
    problems: list[str] = []
    if layout.is_empty:
        return problems
    by_key = {p.key: p for p in plans}
    home: dict[str, str] = {}
    orphans: list[RackItem] = []
    for key in layout.removed:
        plan = by_key.pop(key, None)
        if plan is not None:
            for it in plan.items:
                home[it.id] = ""
            orphans.extend(it for it in plan.items if not it.id.startswith("pdu:"))
            plans.remove(plan)
    for key in layout.added:
        if key in by_key:
            continue
        props = layout.props.get(key)
        size = (props.size_u if props and props.size_u else None) or preferred_u or DEFAULT_USER_RACK_U
        plan = RackPlan(name="", role="user", size_u=size, model=model_for.get(size, ""), key=key)
        plans.append(plan)
        by_key[key] = plan
    used_letters = {p.letter for p in plans if p.role != "user"}
    n = 0
    for plan in plans:
        if plan.role == "user":
            while _letter(n) in used_letters:
                n += 1
            plan.letter = _letter(n)
            used_letters.add(plan.letter)
    for key, props in layout.props.items():
        plan = by_key.get(key)
        if plan is None:
            continue
        if props.size_u:
            plan.size_u = props.size_u
            plan.model = model_for.get(props.size_u, "")
        if props.floor is not None:
            plan.floor = props.floor
        if props.letter:
            plan.letter = props.letter.strip()[:3] or plan.letter

    hidden = set(layout.hidden)
    for plan in plans:
        for it in plan.items:
            home[it.id] = plan.key
        plan.items = [it for it in plan.items if not (it.id in hidden and it.group in PASSIVE_GROUPS)]
    orphans = [it for it in orphans if not (it.id in hidden and it.group in PASSIVE_GROUPS)]

    # move every item to where the user dropped it; the rest keeps its automatic place
    all_items: dict[str, RackItem] = {it.id: it for plan in plans for it in plan.items}
    all_items.update({it.id: it for it in orphans})
    manual: dict[str, list[RackItem]] = {p.key: [] for p in plans}
    for item_id, pos in layout.positions.items():
        moved = all_items.get(item_id)
        target = by_key.get(pos.rack)
        if moved is None or target is None:
            continue
        moved.u = pos.u
        moved.manual = True
        manual[target.key].append(moved)
    lost: list[RackItem] = []
    for ex in layout.extras:
        target = by_key.get(ex.rack)
        group = EXTRA_GROUPS.get(ex.kind, "custom")
        model = ex.model if ex.kind == "device" else (extra_models or {}).get(ex.kind, "")
        item = RackItem(
            u=ex.u,
            height=ex.height,
            label=ex.label or (ex.model if ex.kind == "device" else "") or t.t(f"rack.extra.{ex.kind}"),
            group=group,
            model=model,
            id=ex.id,
            manual=True,
            extra=True,
        )
        if target is None:
            # its cabinet is gone (removed, or no longer produced by the sizing): keep the item
            # in another cabinet so that it is still counted
            lost.append(item)
        else:
            manual[target.key].append(item)
    manual_ids = {it.id for items in manual.values() for it in items}
    displaced: list[RackItem] = [it for it in orphans if it.id not in manual_ids] + lost
    for plan in plans:
        auto = [it for it in plan.items if it.id not in manual_ids]
        plan.items = []
        for it in manual[plan.key]:
            if it.u < 1 or it.top > plan.size_u:
                it.u = max(1, min(it.u, plan.size_u - it.height + 1))
            if any(_overlaps(it, o) for o in plan.items):
                problems.append(t.t("check.rack_overlap", item=it.label or it.model or it.id, name=plan.tag))
            plan.items.append(it)
        for it in auto:
            if _fits(plan, it):
                plan.items.append(it)
            else:
                displaced.append(it)
    for it in displaced:
        candidates = [by_key[home[it.id]]] if home.get(it.id) in by_key else []
        candidates += [p for p in plans if p not in candidates]
        for plan in candidates:
            slot = _nearest_slot(plan, it.height, it.u)
            if slot is not None:
                it.u = slot
                plan.items.append(it)
                break
        else:
            target = candidates[0] if candidates else None
            if target is None:
                continue
            problems.append(t.t("check.rack_no_space", item=it.label or it.model or it.id))
            it.u = max(1, min(it.u, target.size_u - it.height + 1))
            target.items.append(it)
    return problems


POWERED_GROUPS = {*EDGE_KEYS, "core_switch", "firewall", "device"}
"""Rack items that take a power cord from a PDU."""


def top_up_pdus(
    plans: list[RackPlan], feeds: int, outlets: int, model: str, hidden: set[str], t: Translator
) -> list[str]:
    """Add PDUs where the manual layout put more powered devices into a cabinet than its PDUs can feed.

    PDUs the user deleted stay deleted; existing ones are never removed.
    """
    problems: list[str] = []
    ids = {it.id for plan in plans for it in plan.items}
    for plan in plans:
        cords = sum(
            1
            for it in plan.items
            if it.group in POWERED_GROUPS or (it.group == "power" and not it.id.startswith("ups:"))
        )
        need = feeds * max(1, _ceil_div(cords, outlets)) if cords else 0
        for i in range(1, need + 1):
            item_id = f"pdu:{plan.key}:{i}"
            if item_id in ids or item_id in hidden:
                continue
            slot = _nearest_slot(plan, 1, 1)
            feed = "AB"[(i - 1) % 2] if feeds == 2 else "A"
            label = t.t("rack.pdu", feed=feed, n=outlets)
            if slot is None:
                problems.append(t.t("check.rack_no_space", item=label))
                continue
            plan.items.append(RackItem(u=slot, height=1, label=label, group="pdu", model=model, id=item_id))
            ids.add(item_id)
    return problems


def name_items(plans: list[RackPlan], layout: RackLayout, code: str, t: Translator, panel_ports: int) -> None:
    """Cabinet titles and device / patch panel labels, numbered top-down per cabinet."""
    code = code.strip()
    user_no = 0
    for plan in plans:
        props = layout.props.get(plan.key)
        if plan.role == "user":
            user_no += 1
        if props and props.name:
            plan.name = props.name
        elif code:
            plan.name = t.t("rack.title_code", code=code, floor=plan.floor, letter=plan.letter)
        elif plan.role == "user":
            plan.name = t.t("rack.user", n=user_no)
        prefix = f"{code}-{plan.tag}-" if code else ""
        counters: dict[str, int] = {}
        wifi_panels = sum(1 for it in plan.items if it.group == "panel" and it.id.startswith("panel:wifi_switch"))
        for it in sorted(plan.items, key=lambda i: -i.u):
            if it.extra:
                continue
            role = ROLE_CODES.get(it.group)
            if role:
                counters[role] = counters.get(role, 0) + 1
                it.label = f"{prefix}{role}{counters[role]:02d}"
            elif it.group == "panel" and it.id.startswith("panel:"):
                kind = it.id.split(":")[1]
                counters["pp:" + kind] = n = counters.get("pp:" + kind, 0) + 1
                if kind == "wifi_switch":
                    it.label = t.t("rack.pp_wifi") + (f" №{n}" if wifi_panels > 1 else "")
                elif kind == "camera_switch":
                    it.label = t.t("rack.pp_video", n=n)
                else:
                    it.label = t.t("rack.pp", n=n)
            elif it.group == "manager" and not it.label:
                it.label = t.t("rack.manager")
            elif it.group == "panel" and not it.label:
                it.label = t.t("rack.panel", n="", ports=panel_ports)
