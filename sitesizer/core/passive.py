"""Passive infrastructure: structured cabling, fibre backbone, rack power and cabinet layout.

Turns the sized equipment into closets (MDF + remote IDFs) and lays every closet out in 24U/42U
cabinets following the house standard (top to bottom)::

    ODF · organizer                         (one per fibre link of the cabinet)
    firewall(s), core switch(es)
    panel · organizer · Wi-Fi switch        (one panel, one organizer)
    panel · organizer · switch · organizer · panel   (two panels, two organizers)
    …
    video switches (VSW) with their panels   (at the bottom, 5U kept free under them if possible)
    PDUs, UPS

Fibre links (an optical patch panel at each end): every further cabinet of a telecom room to the
room's first cabinet, a remote room to the main one, and the main cabinet of a floor to the
firewall floor. The user's manual layout (:class:`~sitesizer.core.models.RackLayout`: dragged
items, added/removed cabinets, extra organizers, deleted items — active ones too) is applied on
top, then devices get their names (``BO123-5B-ASW01``) and the copper and fibre parts are counted
from what is actually in the cabinets. Part numbers come from ``catalog.passive``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .catalog import Catalog, FiberSpec
from .models import (
    CategoryResult,
    FirewallChoice,
    FloorContext,
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
RACK_ORDER = ("wifi_switch", "camera_switch", "access_switch")
"""Top-down order of the switches in a cabinet: firewall · core (DSW) · WSW · VSW · ASW."""
POE_KEYS = ("wifi_switch", "camera_switch")
"""PoE switches stand with the firewall and the core; access switches get cabinets of their own."""
ROLE_CODES = {
    "wifi_switch": "WSW",
    "access_switch": "ASW",
    "camera_switch": "VSW",
    "core_switch": "DSW",
    "firewall": "FW",
}
"""Device name role codes: ``<site>-<floor><cabinet>-<ROLE><NN>``."""
PASSIVE_GROUPS = ("panel", "manager", "fiber", "pdu", "shelf", "blank", "custom", "device")
"""Passive item groups (anything in a cabinet may be deleted, active equipment too)."""
EXTRA_GROUPS = {
    "manager": "manager",
    "panel": "panel",
    "shelf": "shelf",
    "blank": "blank",
    "odf": "fiber",
    "device": "device",
}
DEFAULT_USER_RACK_U = 42
VIDEO_GAP_U = 5
"""Free units kept under the video switches (VSW) at the bottom of a cabinet, when there is room."""
ISP = "isp"
"""Peer of the ODF where the provider's fibre comes in (the firewall's cabinet)."""


def _is_video(item_id: str) -> bool:
    """A video switch (VSW) or one of its patch panels / organizers."""
    return "camera_switch:" in item_id


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
    head: list[_Unit] = field(default_factory=list)
    """Firewalls, core switches, OOB router (MDF only)."""
    bottom: list[_Unit] = field(default_factory=list)
    """UPS (MDF only)."""


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
# stack pattern: panel · organizer · switch · organizer · panel …
# =========================================================================================
def stack_units(switches: list[_Switch], panel_model: str, panel_u: int, manager_model: str) -> list[_Unit]:
    """Top-down units for a run of switches with their patch panels and organizers.

    One panel: panel · organizer · switch. Two panels: panel · organizer · switch · organizer · panel.
    """
    out: list[_Unit] = []
    for sw in switches:
        upper = math.ceil(sw.panels / 2)
        panels = [_Unit(f"panel:{sw.id}:{k}", panel_u, "", "panel", panel_model) for k in range(1, sw.panels + 1)]
        out.extend(panels[:upper])
        out.append(_Unit(f"org:{sw.id}", 1, "", "manager", manager_model))
        out.append(_Unit(sw.id, sw.height, "", sw.key, sw.model))
        if panels[upper:]:
            out.append(_Unit(f"org:{sw.id}:2", 1, "", "manager", manager_model))
            out.extend(panels[upper:])
    return out


def odf_units(key: str, peers: list[str], model: str, height: int, manager_model: str) -> list[_Unit]:
    """An optical patch panel and its organizer for every fibre link of cabinet ``key``."""
    out: list[_Unit] = []
    for peer in peers:
        out.append(_Unit(f"odf:{key}:{peer}", height, "", "fiber", model))
        out.append(_Unit(f"org:odf:{key}:{peer}", 1, "", "manager", manager_model))
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
    fctx = ctx.floor
    rs = RackSummary()
    ps = PassiveSummary()
    rs.passive = ps

    def units(model: str, default: int = 1) -> int:
        dev = cat.models.get(model)
        return dev.rack_units if dev and dev.rack_units else default

    # ---- closets ------------------------------------------------------------------------
    run = ctx.site.max_cable_run_m
    n_closets = ctx.site.closets or (max(1, math.ceil(run / r.copper_max_m)) if run else 1)
    if ctx.site.per_room:
        n_closets = ctx.site.room_count()
    rs.idf_count = n_closets
    closets = [_Closet(i) for i in range(n_closets)]
    if n_closets > 1:
        # without a cable run every remote closet is assumed one copper reach further away
        ps.backbone_m = [
            ctx.site.fiber_backbone_m or max(1, round(k * run / n_closets) if run else k * r.copper_max_m)
            for k in range(1, n_closets)
        ]

    # ---- switches (assigned to the least loaded closet) ---------------------------------
    load = [0] * n_closets
    for key in RACK_ORDER:
        res = categories[key]
        if not res.count:
            continue
        ports = cat.categories[key].endpoints_per_switch if key in cat.categories else pr.panel_ports
        panels = pr.panels_per_switch.get(key, _ceil_div(ports, pr.panel_ports))
        if res.per_room is not None:
            # every room's switches stand in its own cabinets (numbered on, room after room)
            n = 0
            for ci, count in enumerate(res.per_room[:n_closets]):
                model = res.room_models[ci] if ci < len(res.room_models) else res.model
                for _ in range(count):
                    n += 1
                    closets[ci].switches.append(_Switch(f"{key}:{n}", key, model, units(model), panels))
            continue
        for i in range(res.count):
            ci = min(range(n_closets), key=lambda k: (load[k], k))
            load[ci] += 1
            closets[ci].switches.append(_Switch(f"{key}:{i + 1}", key, res.model, units(res.model), panels))

    # ---- fibre type (estimated from the longest run) ------------------------------------
    runs = list(ps.backbone_m)
    if fctx is not None and not fctx.has_firewall:
        runs.append(floor_run(ctx.site.floor, fctx.fw_floor, pr.floor_height_m))
    if fctx is not None and fctx.has_firewall:
        runs += [floor_run(n, ctx.site.floor, pr.floor_height_m) for n, _tag in fctx.remote]
    fkey, fspec, fauto = fiber_choice(ctx, runs)
    ps.fiber_type, ps.fiber_auto = fkey, fauto
    odf_model = fspec.housing_12 if fspec else ""
    odf_u = units(odf_model)
    mdf = closets[0]

    # ---- MDF core equipment -------------------------------------------------------------
    if ctx.tier.oob and (fctx is None or fctx.has_firewall):
        mdf.head.append(_Unit("oob", units("OOB-LTE"), t.t("rack.oob"), "power", "OOB-LTE"))
    if fw and fw.fits:
        for i in range(fw.count):
            mdf.head.append(_Unit(f"firewall:{i + 1}", units(fw.model), fw.model, "firewall", fw.model))
    for i in range(core.count):
        mdf.head.append(_Unit(f"core_switch:{i + 1}", units(core.model), core.model, "core_switch", core.model))
    role_of = device_roles(cat)
    for closet in closets:
        room_in = ctx.site.room(closet.index)
        # further devices chosen for the room on the location page: under the firewall / core
        for j, d in enumerate(room_in.devices):
            dev = cat.models.get(d.model)
            if dev is None:
                continue
            for k in range(d.qty):
                group = role_of(d.model, d.label) or "device"
                label = d.label or ("" if group in ROLE_CODES else d.model)
                label += f" #{k + 1}" if label and d.qty > 1 else ""
                closet.head.append(_Unit(f"dev:{closet.index}:{j}:{k + 1}", units(d.model), label, group, d.model))
        # UPS: the main room by tier / add-on (sized by power), any room by the user's choice
        auto_ups = closet.index == 0 and bool(ctx.tier.ups or addons.get("ups"))
        model = room_in.ups_model if room_in.ups_model in cat.models else power.ups_model
        ups_dev = cat.models.get(model)
        if room_in.ups_count is not None:
            qty = room_in.ups_count
        elif auto_ups:
            qty = 1
            if model == power.ups_model and ups_dev and ups_dev.ups_va and power.ups_va > ups_dev.ups_va:
                qty = math.ceil(power.ups_va / ups_dev.ups_va)
        else:
            qty = 0
        for i in range(qty):
            suffix = f" #{i + 1}" if qty > 1 else ""
            uid = f"ups:{i + 1}" if closet.index == 0 else f"ups:r{closet.index}:{i + 1}"
            closet.bottom.append(
                _Unit(uid, (ups_dev.rack_units if ups_dev else 2) or 2, (model or "UPS") + suffix, "power", model)
            )
    rs.units_ups = sum(u.height for c in closets for u in c.bottom)

    # ---- cabinets -----------------------------------------------------------------------
    racks = cat.rack_models()
    sizes = sorted({dev.rack_size_u or 0 for _, dev in racks if dev.rack_size_u})
    auto_sizes = [s for s in sizes if s >= r.rack_auto_min_u] or sizes  # wall cabinets only by hand
    model_for = {dev.rack_size_u: model for model, dev in racks}
    feeds = 2 if ctx.dual_psu else 1
    panel_u = units(pr.panel)
    odf_pair = (odf_u + 1) if odf_model else 0

    def cabinet_u(closet: _Closet, sws: list[_Switch], first: bool) -> int:
        body = sum(u.height for u in stack_units(sws, pr.panel, panel_u, pr.manager))
        extra = sum(u.height for u in (*closet.head, *closet.bottom)) if first else 0
        cords = len(sws) + (len(closet.head) if first else 0)
        pdus = feeds * max(1, _ceil_div(cords, pr.pdu_outlets)) if cords else 0
        isp = first and any(u.group == "firewall" for u in closet.head)  # the provider's ODF
        odfs = first_odfs.get(closet.index, 2) if first else 1
        return body + extra + pdus + odf_pair * (odfs + (1 if isp else 0))

    # the first cabinet of a room has an ODF towards each further cabinet and one upstream
    first_odfs: dict[int, int] = {}
    upstream = 1 + (len(fctx.remote) if fctx is not None and fctx.has_firewall else 0)

    layouts: list[tuple[_Closet, list[list[_Switch]], int]] = []
    for closet in closets:
        if not (closet.switches or closet.head or closet.bottom or ctx.site.closets):
            continue  # an empty closet still gets a cabinet when the user asked for that many
        need = math.ceil(cabinet_u(closet, closet.switches, True) * (1 + r.rack_spare_ratio))
        size = _pick_size(ctx.site.rack_size_u, need, sizes if ctx.site.rack_size_u else auto_sizes)
        usable = max(1, size - math.floor(size * r.rack_spare_ratio / (1 + r.rack_spare_ratio)))
        for _ in range(6):
            groups = _fill(closet, usable, cabinet_u)
            if len(groups) > 1:
                groups = _split_poe(closet, groups, usable, cabinet_u) or _balance(closet, groups, usable, cabinet_u)
            odfs = max(2, len(groups) - 1 + upstream)
            if odfs <= first_odfs.get(closet.index, 2):
                break
            first_odfs[closet.index] = odfs  # more cabinets than reserved for: lay the room out again
        layouts.append((closet, groups, size))

    # fibre links of the automatic structure (re-checked after the manual layout)
    def cab_key(closet: _Closet, gi: int) -> str:
        return f"mdf-{gi + 1}" if closet.index == 0 else f"idf{closet.index}-{gi + 1}"

    peers: dict[str, list[str]] = {}
    structure = [
        (cab_key(c, gi), c.index, True)  # every cabinet gets its optical patch panel
        for c, groups, _s in layouts
        for gi, sws in enumerate(groups)
    ]
    if odf_model:
        peers = cabinet_links(structure, fctx)
        fw_closet = next((c for c, _g, _s in layouts if any(u.group == "firewall" for u in c.head)), None)
        if fw_closet is not None:
            # the provider's fibre comes into the firewall's cabinet
            peers.setdefault(cab_key(fw_closet, 0), []).insert(0, ISP)

    plans: list[RackPlan] = []
    for closet, groups, size in layouts:
        for gi, sws in enumerate(groups):
            first = gi == 0
            key = cab_key(closet, gi)
            if closet.index == 0:
                name = t.t("rack.mdf") + (f"-{gi + 1}" if len(groups) > 1 else "")
            else:
                name = t.t("rack.idf", n=closet.index) + (f".{gi + 1}" if len(groups) > 1 else "")
            plan = RackPlan(
                name=name,
                role="mdf" if closet.index == 0 else "idf",
                size_u=size,
                model=model_for.get(size, ""),
                key=key,
                floor=ctx.site.floor,
                room=closet.index,
            )
            seq = odf_units(key, peers.get(key, []), odf_model, odf_u, pr.manager)
            seq += (list(closet.head) if first else []) + stack_units(sws, pr.panel, panel_u, pr.manager)
            low = [unit for unit in seq if _is_video(unit.id)]  # VSW stand at the bottom of the cabinet
            seq = [unit for unit in seq if not _is_video(unit.id)]
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
            if low:
                block = sum(unit.height for unit in low)
                gap = max(0, min(VIDEO_GAP_U, cursor - (bottom - 1) - block))
                cursor = bottom + gap + block - 1
                for unit in low:
                    plan.items.append(_item(unit, cursor - unit.height + 1))
                    cursor -= unit.height
            plans.append(plan)

    for i, plan in enumerate(plans):
        plan.letter = _letter(i)

    # ---- manual layout, fibre links, names ----------------------------------------------
    rs.rooms = room_names(n_closets, ctx.site.layout, t)
    rs.layout_problems = apply_layout(
        plans,
        ctx.site.layout,
        model_for,
        ctx.site.rack_size_u,
        t,
        {"manager": pr.manager, "panel": pr.panel, "odf": odf_model},
        rooms=n_closets,
        floor=ctx.site.floor,
        role_of=role_of,
    )
    change_models(plans, ctx.site.layout, cat)
    plans.sort(key=lambda p: p.room)  # a room's cabinets side by side (stable: keeps their order)
    links: list[tuple[str, str]] = []
    fibers: dict[tuple[str, str], int] = {}
    if odf_model and fspec is not None:
        fw_fibers = ctx.site.fiber_fw_fibers or pr.fiber_fw_fibers
        rack_fibers = ctx.site.fiber_rack_fibers or pr.fiber_rack_fibers
        links, problems, fibers = link_cabinets(
            plans,
            ctx.site.layout,
            fctx,
            fspec.housing_for,
            odf_u,
            pr.manager,
            t,
            fw_fibers=fw_fibers,
            rack_fibers=rack_fibers,
        )
        rs.layout_problems += problems
    if not ctx.site.layout.is_empty:
        rs.layout_problems += top_up_pdus(plans, feeds, pr.pdu_outlets, pr.pdu, set(ctx.site.layout.hidden), t)
    name_items(plans, ctx.site.layout, ctx.site.location_code, t, pr.panel_ports)
    assign_endpoints(plans, ctx.counts, cat)
    for plan in plans:
        plan.items.sort(key=lambda it: -it.u)
    rs.plans = plans
    active = {*EDGE_KEYS, "core_switch", "firewall"}
    for plan in plans:
        for it in plan.items:
            ps.mount_sets += pr.mount_kit_active if it.group in active else pr.mount_kit_other
            if it.group == "pdu":
                ps.pdus += 1
            elif it.group == "panel" and it.model:
                ps.panels += 1
            elif it.group == "manager" and it.model:
                ps.managers += 1
            elif it.group == "fiber" and it.model:
                ps.housing_models[it.model] = ps.housing_models.get(it.model, 0) + 1
                if fspec is not None and it.model == fspec.housing_24:
                    ps.housings_24 += 1
                else:
                    ps.housings_12 += 1

    # ---- fibre: one 12-fibre cable per link, counted on the side that uplinks -----------
    if fspec is not None:
        by_key = {p.key: p for p in plans}
        for child, parent in links:
            if parent.startswith("floor"):
                continue  # the other floor counts that cable
            if parent == "fw":
                length = floor_run(ctx.site.floor, fctx.fw_floor if fctx else ctx.site.floor, pr.floor_height_m)
            else:
                a, b = by_key.get(child), by_key.get(parent)
                room = max(a.room if a else 0, b.room if b else 0)
                if a and b and a.room == b.room:
                    length = pr.fiber_cabinet_m
                else:
                    length = ps.backbone_m[room - 1] if 0 < room <= len(ps.backbone_m) else r.copper_max_m
            n = fibers.get((child, parent), fspec.cable_fibers)
            cable = fspec.cable_for(n)
            ps.fiber_links += 1
            ps.fiber_cables += 1
            ps.fiber_cores += n
            ps.fiber_m += length + pr.fiber_slack_m
            ps.cable_models[cable] = ps.cable_models.get(cable, 0) + length + pr.fiber_slack_m
            ps.cable_links[cable] = ps.cable_links.get(cable, 0) + 1
        housings = ps.housings_12 + ps.housings_24
        ps.fiber_ends = housings
        # every fibre of a cable is spliced at each of its ends (a hand-added ODF: a 12-fibre one)
        ends = {f"odf:{a}:{b}": n for (a, b), n in fibers.items()} | {f"odf:{b}:{a}": n for (a, b), n in fibers.items()}
        ps.splices = sum(
            ends.get(it.id, fspec.cable_fibers)
            for p in plans
            for it in p.items
            if it.group == "fiber" and it.model and not it.id.endswith(f":{ISP}")  # the provider splices its own
        )
        ps.fiber_cords = housings

    # ---- copper -------------------------------------------------------------------------
    counts = ctx.counts
    ps.sockets = counts.sockets
    ps.device_links = counts.cameras + counts.vsw_extra + counts.aps
    ps.copper_links = ps.sockets + ps.device_links
    avg = ctx.site.avg_cable_run_m or r.avg_cable_run_m_default
    ps.cable_m = ps.copper_links * (avg + pr.cable_slack_m)
    ps.cable_drums = _ceil_div(ps.cable_m, pr.cable_drum_m)
    ps.jacks = ps.copper_links * 2
    ps.jack_packs = _ceil_div(ps.jacks, pr.jack_pack)
    ps.outlets = _ceil_div(ps.sockets, pr.outlet_ports) + ps.device_links
    # patch panel ↔ switch: a 0.25 m cord on every copper port of the switches in the cabinets
    ps.cords_panel = sum(
        dev.ports.count
        for p in rs.plans
        for it in p.items
        if it.group in EDGE_KEYS
        and (dev := cat.models.get(it.model)) is not None
        and dev.ports is not None
        and "SFP" not in dev.ports.type.upper()
    )
    ps.cords_rack = ps.device_links  # 1 m at the APs and cameras
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
        rs.rack_size_u, rs.rack_model = auto_sizes[0], model_for.get(auto_sizes[0], "")
    rs.patch_panels = ps.panels
    rs.copper_endpoints = ps.copper_links
    rs.cable_m = ps.cable_m
    rs.cable_boxes = ps.cable_drums
    return rs


LABEL_ROLES = (
    ("DSW", "core_switch"),
    ("CSW", "core_switch"),
    ("CORE", "core_switch"),
    ("WSW", "wifi_switch"),
    ("VSW", "camera_switch"),
    ("ASW", "access_switch"),
    ("FW", "firewall"),
)


def device_roles(cat: Any) -> Callable[[str, str], str]:
    """``(model, label) -> role`` of a device put into a cabinet by hand ("" = not a network device).

    A FortiGate is a firewall; a switch is the core or an edge switch when the catalog's categories use
    that model, otherwise its name decides (``…-DSW01`` core, ``…-VSW01`` video…), else an access switch.
    """
    by_model: dict[str, str] = {}
    for key in ("core_switch", "wifi_switch", "camera_switch", "access_switch"):
        c = cat.categories.get(key)
        if c is not None:
            by_model.setdefault(c.base, key)
            by_model.setdefault(c.premium, key)

    def role(model: str, label: str = "") -> str:
        dev = cat.models.get(model)
        if dev is None:
            return ""
        if dev.kind == "firewall":
            return "firewall"
        if dev.kind != "switch":
            return ""
        name = label.upper()
        for code, key in LABEL_ROLES:
            if re.search(rf"(^|[^A-Z]){code}\d*($|[^A-Z])", name):
                return key
        return by_model.get(model, "access_switch")

    return role


def copper_ports(model: str, cat: Any, default: int = 48) -> int:
    dev = cat.models.get(model)
    return dev.ports.count if dev is not None and dev.ports is not None else default


def assign_endpoints(plans: list[RackPlan], counts: Any, cat: Any) -> None:
    """Spread every telecom room's endpoints over its switches (``RackItem.endpoints``).

    Sockets and cameras fill the switches in their cabinet order; APs are spread evenly (neighbouring
    APs alternate switches, see ``core.ap_wiring``); Ajax, access control and other devices take the
    last copper ports of the room's last video switch.
    """
    pools: list[tuple[int | None, int, int, int, int, int, int]]
    if counts.rooms:
        pools = [(i, rc.sockets, rc.cameras, rc.aps, rc.other, rc.ajax, rc.skud) for i, rc in enumerate(counts.rooms)]
    else:
        pools = [(None, counts.sockets, counts.cameras, counts.aps, counts.other, counts.ajax, counts.skud)]

    def switches(room: int | None, key: str) -> list[RackItem]:
        here = [p for p in plans if room is None or p.room == room]
        return [it for p in here for it in sorted(p.items, key=lambda i: -i.u) if it.group == key]

    for room, sockets, cameras, aps, other, ajax, skud in pools:
        for key in ("access_switch", "wifi_switch", "camera_switch"):
            for it in switches(room, key):
                it.endpoints = []
        sws = switches(room, "access_switch")
        left = sockets
        for it in sws:
            n = min(left, copper_ports(it.model, cat)) if it is not sws[-1] else left
            if n:
                it.endpoints.append(("sockets", n))
            left -= n
        sws = switches(room, "wifi_switch")
        for j, it in enumerate(sws):
            n = (aps - j + len(sws) - 1) // len(sws)  # round robin: AP k goes to switch k mod n
            if n > 0:
                it.endpoints.append(("aps", n))
        sws = switches(room, "camera_switch")
        if not sws:
            continue
        tail = [(k, n) for k, n in (("other", other), ("ajax", ajax), ("skud", skud)) if n]
        left = cameras
        for it in sws:
            cap = copper_ports(it.model, cat) - (sum(n for _k, n in tail) if it is sws[-1] else 0)
            n = min(left, max(0, cap)) if it is not sws[-1] else left
            if n:
                it.endpoints.append(("cameras", n))
            left -= n
        sws[-1].endpoints += tail


def floor_run(floor: int, other: int, per_floor_m: int) -> int:
    """Fibre run between two floors (at least one floor height)."""
    return max(1, abs(floor - other)) * per_floor_m


def cabinet_links(cabinets: list[tuple[str, int, bool]], fctx: FloorContext | None) -> dict[str, list[str]]:
    """Fibre links of every cabinet: ``key -> peers`` (a peer is a cabinet key, ``fw`` or ``floor<N>``).

    ``cabinets`` are (key, room, has active equipment) in display order. A further cabinet of a
    room links to the room's first cabinet; the first cabinet of a remote room links to the main
    one; the main cabinet of a floor without the firewall links to the firewall floor, and the
    firewall floor's main cabinet gets one link per other floor.
    """
    out: dict[str, list[str]] = {k: [] for k, _r, _a in cabinets}
    pairs = cabinet_link_pairs(cabinets, fctx)
    for a, b in pairs:
        out[a].append(b)
        if b in out:
            out[b].append(a)
    return out


def cabinet_link_pairs(cabinets: list[tuple[str, int, bool]], fctx: FloorContext | None) -> list[tuple[str, str]]:
    """(child, parent) fibre links; see :func:`cabinet_links`."""
    heads: dict[int, str] = {}
    room_active: dict[int, bool] = {}
    for key, room, active in cabinets:
        heads.setdefault(room, key)
        room_active[room] = room_active.get(room, False) or active
    if not heads:
        return []
    main = heads.get(0) or heads[min(heads)]
    pairs: list[tuple[str, str]] = []
    for room in sorted(heads):
        head = heads[room]
        if head != main and room_active[room]:
            pairs.append((head, main))
    for key, room, active in cabinets:
        if key != heads[room] and active:
            pairs.append((key, heads[room]))
    if fctx is not None:
        if not fctx.has_firewall and any(room_active.values()):
            pairs.append((main, "fw"))
        if fctx.has_firewall:
            pairs += [(main, f"floor{n}") for n, _tag in fctx.remote]
    return pairs


def link_cabinets(
    plans: list[RackPlan],
    layout: RackLayout,
    fctx: FloorContext | None,
    housing: Callable[[int], str],
    height: int,
    manager_model: str,
    t: Translator,
    fw_fibers: int = 24,
    rack_fibers: int = 12,
) -> tuple[list[tuple[str, str]], list[str], dict[tuple[str, str], int]]:
    """Put an ODF (+ organizer) at both ends of every fibre link of the final cabinets.

    A link that ends at the firewall's cabinet (or goes to the firewall floor) is a
    ``fw_fibers`` cable, any other one a ``rack_fibers`` cable; ``housing(fibres)`` gives the
    ODF for it. Automatic ODFs whose link no longer exists (a cabinet was removed or emptied)
    are dropped; missing ones go to the highest free units. ODFs the user deleted stay deleted.
    Returns the (child, parent) links, the placement problems and the fibres of every link.
    """
    problems: list[str] = []
    # every cabinet has an optical patch panel with an organizer, an empty one too
    cabinets = [(p.key, p.room, True) for p in plans]
    pairs = cabinet_link_pairs(cabinets, fctx)
    by_key = {p.key: p for p in plans}
    fw_racks = {p.key for p in plans if any(it.group == "firewall" for it in p.items)}
    fibers = {
        (a, b): fw_fibers if (b in ("fw",) or b.startswith("floor") or a in fw_racks or b in fw_racks) else rack_fibers
        for a, b in pairs
    }
    ends: list[tuple[str, str]] = []
    end_fibers: dict[tuple[str, str], int] = {}
    for a, b in pairs:
        ends.append((a, b))
        end_fibers[(a, b)] = fibers[(a, b)]
        if b in by_key:
            ends.append((b, a))
            end_fibers[(b, a)] = fibers[(a, b)]
    for key in fw_racks:
        # the provider's fibre (not a cable of ours): its ODF stands in the firewall's cabinet
        ends.insert(0, (key, ISP))
        end_fibers[(key, ISP)] = fw_fibers
    want_ids = {f"odf:{k}:{peer}" for k, peer in ends} | {f"org:odf:{k}:{peer}" for k, peer in ends}
    for plan in plans:
        plan.items = [
            it
            for it in plan.items
            if it.extra or not (it.id.startswith("odf:") or it.id.startswith("org:odf:")) or it.id in want_ids
        ]
    present = {it.id for p in plans for it in p.items}
    hidden = set(layout.hidden)
    tags = {p.key: p.tag for p in plans}
    remote = dict(fctx.remote) if fctx else {}
    for key, peer in ends:
        plan = by_key[key]
        if peer == ISP:
            to = t.t("rack.odf_isp")
        elif peer == "fw":
            to = fctx.fw_tag if fctx else ""
        elif peer.startswith("floor"):
            to = remote.get(int(peer[5:]), peer[5:])
        else:
            to = tags.get(peer, peer)
        odf_id, org_id = f"odf:{key}:{peer}", f"org:odf:{key}:{peer}"
        label = t.t("rack.odf", to=to)
        model = housing(end_fibers[(key, peer)])
        for it in (it for p in plans for it in p.items if it.id == odf_id):
            it.label = label
            it.model = model
        for item_id, h, group, m, lab in (
            (odf_id, height, "fiber", model, label),
            (org_id, 1, "manager", manager_model, ""),
        ):
            if item_id in present or item_id in hidden:
                continue
            slot = plan.free_slot(h)
            if slot is None:
                problems.append(t.t("check.rack_no_space", item=lab or t.t("rack.manager")))
                continue
            plan.items.append(RackItem(u=slot, height=h, label=lab, group=group, model=m, id=item_id))
    for plan in plans:
        odfs_on_top(plan)
    return pairs, problems, fibers


def _is_odf(it: RackItem) -> bool:
    return it.id.startswith(("odf:", "org:odf:"))


def odfs_on_top(plan: RackPlan) -> None:
    """Optical patch panels (each with its organizer under it) always at the top of the cabinet.

    When an automatic ODF ended up below other automatic equipment, the automatic items are laid
    out again top-down: the provider's ODF, the other ODFs, then everything else in its order.
    Items the user placed, PDUs, UPS and the video switches at the bottom keep their units; if it
    does not fit, nothing moves.
    """
    auto = [
        it
        for it in plan.items
        if not it.manual and not it.extra and not it.id.startswith(("pdu:", "ups:")) and not _is_video(it.id)
    ]
    odf = [it for it in auto if _is_odf(it)]
    others = [it for it in auto if not _is_odf(it)]
    if not odf or not others or max(it.top for it in others) < min(it.u for it in odf):
        return
    panels = sorted(
        (it for it in odf if it.id.startswith("odf:")), key=lambda it: (not it.id.endswith(f":{ISP}"), -it.u)
    )
    orgs = {it.id: it for it in odf if it.id.startswith("org:odf:")}
    seq: list[RackItem] = []
    for it in panels:
        seq.append(it)
        org = orgs.pop("org:" + it.id, None)
        if org is not None:
            seq.append(org)
    seq += sorted(orgs.values(), key=lambda it: -it.u)
    seq += sorted(others, key=lambda it: -it.u)
    keep = {id(it) for it in seq}
    taken = {u for it in plan.items if id(it) not in keep for u in range(it.u, it.top + 1)}
    cursor = plan.size_u
    new_u: list[int] = []
    for it in seq:
        while cursor - it.height + 1 >= 1 and any(u in taken for u in range(cursor - it.height + 1, cursor + 1)):
            cursor -= 1
        if cursor - it.height + 1 < 1:
            return  # no room to lay it out again: leave the cabinet as it is
        new_u.append(cursor - it.height + 1)
        cursor -= it.height
    for it, u in zip(seq, new_u, strict=True):
        it.u = u


def change_models(plans: list[RackPlan], layout: RackLayout, catalog: Catalog) -> None:
    """Models the user picked for items in the cabinets: same name, same place.

    A model of another height takes it when the units above the item are free, else the item keeps
    its height.
    """
    if not layout.models:
        return
    for plan in plans:
        for it in plan.items:
            model = layout.models.get(it.id)
            if not model or model == it.model:
                continue
            it.model = model
            dev = catalog.models.get(model)
            height = dev.rack_units if dev is not None and dev.rack_units else it.height
            if height != it.height and it.u + height - 1 <= plan.size_u:
                span = set(range(it.u, it.u + height))
                if not any(o is not it and span & set(range(o.u, o.top + 1)) for o in plan.items):
                    it.height = height


def room_names(n: int, layout: RackLayout, t: Translator) -> list[str]:
    """``Комутаційна 1 (MDF)``, ``Комутаційна 2 (IDF-1)``… unless the user named the room."""
    out = []
    for i in range(n):
        role = t.t("rack.mdf") if i == 0 else t.t("rack.idf", n=i)
        out.append(layout.room_names.get(str(i)) or t.t("rack.room", n=i + 1, role=role))
    return out


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


def _balance(closet: _Closet, groups: list[list[_Switch]], limit: int, cabinet_u) -> list[list[_Switch]]:  # type: ignore[no-untyped-def]
    """Spread the switches evenly instead of leaving the last cabinet almost empty."""
    per = math.ceil(len(closet.switches) / len(groups))
    balanced = [closet.switches[i : i + per] for i in range(0, len(closet.switches), per)]
    if len(balanced) == len(groups) and all(cabinet_u(closet, g, gi == 0) <= limit for gi, g in enumerate(balanced)):
        return balanced
    return groups


def _split_poe(closet: _Closet, groups: list[list[_Switch]], limit: int, cabinet_u) -> list[list[_Switch]] | None:  # type: ignore[no-untyped-def]
    """Best practice for a room with several cabinets: firewall, core and the PoE switches (WSW, VSW)
    in the first cabinet, the access switches (ASW) in the others.

    ``None`` when the PoE switches do not fit the first cabinet or the split would need more
    cabinets than the plain fill.
    """
    poe = [sw for sw in closet.switches if sw.key in POE_KEYS]
    access = [sw for sw in closet.switches if sw.key not in POE_KEYS]
    if not poe or not access or cabinet_u(closet, poe, True) > limit:
        return None
    rest: list[list[_Switch]] = [[]]
    for sw in access:
        trial = [*rest[-1], sw]
        if rest[-1] and cabinet_u(closet, trial, False) > limit:
            rest.append([sw])
        else:
            rest[-1] = trial
    if any(cabinet_u(closet, g, False) > limit for g in rest) or 1 + len(rest) > len(groups):
        return None
    per = math.ceil(len(access) / len(rest))
    balanced = [access[i : i + per] for i in range(0, len(access), per)]
    if len(balanced) == len(rest) and all(cabinet_u(closet, g, False) <= limit for g in balanced):
        rest = balanced
    return [poe, *rest]


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
    rooms: int = 1,
    floor: int = 1,
    role_of: Callable[[str, str], str] | None = None,
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
            # PDUs and fibre panels belong to the cabinet; the rest moves to another one
            orphans.extend(it for it in plan.items if not it.id.startswith(("pdu:", "odf:", "org:odf:")))
            plans.remove(plan)
    for key in layout.added:
        if key in by_key:
            continue
        props = layout.props.get(key)
        size = (props.size_u if props and props.size_u else None) or preferred_u or DEFAULT_USER_RACK_U
        room = min(props.room or 0, rooms - 1) if props else 0
        plan = RackPlan(
            name="", role="user", size_u=size, model=model_for.get(size, ""), key=key, floor=floor, room=room
        )
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
        if props.room is not None and plan.role != "user":
            plan.room = min(props.room, rooms - 1)
        if props.letter:
            plan.letter = props.letter.strip()[:3] or plan.letter

    hidden = set(layout.hidden)
    for plan in plans:
        for it in plan.items:
            home[it.id] = plan.key
        plan.items = [it for it in plan.items if it.id not in hidden]
    orphans = [it for it in orphans if it.id not in hidden]

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
        if ex.kind == "device" and model:
            # a FortiGate / switch put in by hand is a real network device: named, in the topology
            if ex.role == "device":
                role = ""  # the user said: not a network device
            else:
                role = ex.role if ex.role in ROLE_CODES else (role_of(model, ex.label) if role_of else "")
            group = role or group
        default = "" if group in ROLE_CODES else (ex.model if ex.kind == "device" else "")
        item = RackItem(
            u=ex.u,
            height=ex.height,
            label=ex.label or default or ("" if group in ROLE_CODES else t.t(f"rack.extra.{ex.kind}")),
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
            role = ROLE_CODES.get(it.group)
            if it.extra and not (role and not it.label):
                continue  # named by the user
            if role:
                counters[role] = counters.get(role, 0) + 1
                if it.label and it.id.startswith("dev:"):
                    continue  # a room device the user named on the location page
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
        for it in plan.items:
            if not it.extra and layout.labels.get(it.id):
                it.label = layout.labels[it.id]


def drop_room(layout: dict[str, Any], room: int, cabinets: set[str]) -> None:
    """Edit a raw ``RackLayout`` dict after removing telecom room ``room`` (≥ 1).

    ``cabinets`` are the keys of the cabinets standing in that room. Everything that referred to
    them is dropped (hand-added items go to another cabinet), and the automatic cabinets, fibre
    panels and names of the rooms after it are renumbered (``idf3-1`` → ``idf2-1``) so that the
    manual layout keeps pointing at the same things.
    """

    def key(k: str) -> str | None:
        m = re.fullmatch(r"idf(\d+)-(\d+)", k)
        if m:
            j = int(m.group(1))
            return None if j == room else f"idf{j - 1 if j > room else j}-{m.group(2)}"
        return None if k in cabinets else k

    def item(i: str) -> str | None:
        m = re.match(r"fiber:(\d+):(.*)", i)
        if m:
            j = int(m.group(1))
            return None if j == room else f"fiber:{j - 1 if j > room else j}:{m.group(2)}"
        m = re.match(r"pdu:(.+):(\d+)$", i)
        if m:
            k = key(m.group(1))
            return None if k is None else f"pdu:{k}:{m.group(2)}"
        return i

    layout["added"] = [k for k in layout.get("added", []) if k not in cabinets]
    layout["removed"] = [k2 for k in layout.get("removed", []) if (k2 := key(k))]
    props = {}
    for k, v in layout.get("props", {}).items():
        k2 = key(k)
        if k2 is None:
            continue
        if (v.get("room") or 0) > room:
            v = {**v, "room": v["room"] - 1}
        props[k2] = v
    layout["props"] = props
    positions = {}
    for i, pos in layout.get("positions", {}).items():
        i2, rack = item(i), key(pos.get("rack", ""))
        if i2 and rack:
            positions[i2] = {**pos, "rack": rack}
    layout["positions"] = positions
    layout["hidden"] = [i2 for i in layout.get("hidden", []) if (i2 := item(i))]
    for ex in layout.get("extras", []):
        ex["rack"] = key(ex.get("rack", "")) or ""  # "" = no cabinet: placed in another one
    layout["labels"] = {i2: v for i, v in layout.get("labels", {}).items() if (i2 := item(i))}
    names = {}
    for k, v in layout.get("room_names", {}).items():
        j = int(k)
        if j != room:
            names[str(j - 1 if j > room else j)] = v
    layout["room_names"] = names


def drop_room_inputs(site: dict[str, Any], room: int) -> None:
    """Edit a raw ``SiteInput`` dict after removing telecom room ``room`` (≥ 1): its endpoints and
    Wi-Fi zones go away, the rooms after it move up by one."""
    rooms = site.get("rooms") or []
    if room < len(rooms):
        rooms.pop(room)
    site["rooms"] = rooms
    groups = []
    for g in site.get("ap_groups") or []:
        r = g.get("room") or 0
        if r == room:
            continue
        groups.append({**g, "room": r - 1 if r > room else r})
    site["ap_groups"] = groups
