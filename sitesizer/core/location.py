"""One location, several floors.

A project is one location; each entry is a floor with its own sockets, cameras, Wi-Fi and
cabinets. The firewall (and the core) is sized once for the whole location and stands on the
firewall floor (the project's hub); every other floor gets an optical patch panel towards it.
:func:`size_location` sizes every floor and :func:`combine` adds them up into one result for the
specification and the exports.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .catalog import Catalog
from .models import (
    BomLine,
    CategoryResult,
    Check,
    EffectiveCounts,
    FloorContext,
    PassiveSummary,
    PowerSummary,
    RackSummary,
    SiteInput,
    SiteResult,
)
from .sizing import size_site

LOCATION_FIELDS = (
    "location_code",
    "location_id",
    "tier",
    "fortios_version",
    "redundant_psu",
    "aggregation",
    "reserve",
    "reserve_percent",
    "ip",
)
"""Inputs shared by every floor of a location (editing one floor changes them on all)."""


@dataclass
class LocationResult:
    floors: list[tuple[str, SiteResult]] = field(default_factory=list)
    """(entry id, result) of every floor, in project order."""
    combined: SiteResult | None = None
    fw_id: str = ""

    def floor(self, entry_id: str) -> SiteResult | None:
        return next((r for i, r in self.floors if i == entry_id), None)


def head_tag(site: SiteInput) -> str:
    """Tag of a floor's main cabinet (``7A``), as its other floors' ODFs are labelled."""
    props = site.layout.props.get("mdf-1")
    floor = props.floor if props and props.floor is not None else site.floor
    letter = (props.letter.strip()[:3] if props and props.letter else "") or "A"
    return f"{floor}{letter}"


def size_location(
    entries: list[tuple[str, SiteInput, bool]], catalog: Catalog, lang: str = "uk", name: str = ""
) -> LocationResult:
    """Size every floor: ``entries`` are (id, input, has the firewall); ``name`` names the location."""
    out = LocationResult()
    if not entries:
        return out
    if len(entries) == 1:
        eid, site, _fw = entries[0]
        r = size_site(site, catalog, lang)
        out.floors = [(eid, r)]
        out.combined = r
        out.fw_id = eid
        return out
    fw_index = next((i for i, (_e, _s, fw) in enumerate(entries) if fw), 0)
    fw_id, fw_site, _ = entries[fw_index]
    wifi_default = catalog.rules.wifi_clients_per_ap_default
    results: dict[str, SiteResult] = {}
    others: dict[str, int] = {}
    remote: list[tuple[int, str]] = []
    for i, (eid, site, _fw) in enumerate(entries):
        if i == fw_index:
            continue
        ctx = FloorContext(
            has_firewall=False,
            floors=len(entries),
            fw_floor=fw_site.floor,
            fw_tag=head_tag(fw_site),
        )
        r = size_site(site, catalog, lang, floor=ctx)
        results[eid] = r
        tot = {
            "switches": r.edge_switch_count + r.core.count,
            "aps": r.counts.aps,
            "sockets": r.counts.sockets,
            "cameras": r.counts.cameras,
            "wifi": r.counts.wifi_clients or r.counts.aps * wifi_default,
        }
        for k, v in tot.items():
            others[k] = others.get(k, 0) + v
        remote.append((site.floor, head_tag(site)))
    fw_ctx = FloorContext(has_firewall=True, floors=len(entries), others=others, fw_floor=fw_site.floor, remote=remote)
    results[fw_id] = size_site(fw_site, catalog, lang, floor=fw_ctx)
    out.floors = [(eid, results[eid]) for eid, _s, _f in entries]
    out.fw_id = fw_id
    out.combined = combine([(site.name, results[eid]) for eid, site, _f in entries], results[fw_id], len(entries), name)
    return out


def _merge_bom(floors: list[tuple[str, SiteResult]]) -> list[BomLine]:
    """Sum the floors' lines by key; a line found on several floors explains its per-floor split."""
    merged: dict[str, BomLine] = {}
    parts: dict[str, list[tuple[str, BomLine]]] = {}
    order: list[str] = []
    for floor_name, r in floors:
        for line in r.bom:
            key = line.key or f"{line.group}:{line.model}"
            parts.setdefault(key, []).append((floor_name, line))
            have = merged.get(key)
            if have is None:
                merged[key] = copy.deepcopy(line)
                order.append(key)
                continue
            if line.qty is not None:
                have.qty = (have.qty or 0) + line.qty
            if line.calc_qty is not None:
                have.calc_qty = (have.calc_qty or 0) + line.calc_qty
            have.manual = have.manual or line.manual
            for tag in line.tags:
                if tag not in have.tags:
                    have.tags.append(tag)
    for key, items in parts.items():
        line = merged[key]
        if len(items) > 1 and line.qty is not None:
            line.reason = " · ".join(f"{n}: {ln.qty}" for n, ln in items)
            line.details = [f"{n}: {ln.reason}" for n, ln in items]
    return [merged[k] for k in order]


def combine(floors: list[tuple[str, SiteResult]], fw_result: SiteResult, n_floors: int, name: str = "") -> SiteResult:
    """Add the floors up into one result: specification, cabinets, power, checks.

    The firewall, the core, the IP plan and the tier come from the firewall floor.
    """
    results = [r for _n, r in floors]
    # the firewall floor's lines first: its firewall, core and licences head the specification
    fw_name = next((n for n, r in floors if r is fw_result), "")
    bom = _merge_bom([(fw_name, fw_result), *[(n, r) for n, r in floors if r is not fw_result]])

    # a finding of every floor is shown once; the others say which floor they are about
    everywhere = set.intersection(*[{c.message for c in r.checks} for r in results]) if results else set()
    checks: list[Check] = []
    seen: set[str] = set()
    for floor_name, r in floors:
        for c in r.checks:
            text = c.message if c.message in everywhere else f"{floor_name}: {c.message}"
            if text in seen:
                continue
            seen.add(text)
            checks.append(copy.copy(c))
            checks[-1].message = text
    order = {"error": 0, "warning": 1, "info": 2}
    checks.sort(key=lambda c: order.get(c.severity.value, 3))

    counts = EffectiveCounts(reserve_factor=fw_result.counts.reserve_factor)
    for r in results:
        counts.sockets += r.counts.sockets
        counts.cameras += r.counts.cameras
        counts.aps += r.counts.aps
        counts.ap_groups += r.counts.ap_groups
        if r.counts.wifi_clients:
            counts.wifi_clients = (counts.wifi_clients or 0) + r.counts.wifi_clients

    categories: dict[str, CategoryResult] = {}
    for key, cat in fw_result.categories.items():
        merged = copy.copy(cat)
        merged.count = sum(r.categories[key].count for r in results)
        merged.endpoints = sum(r.categories[key].endpoints for r in results)
        merged.poe_load_w = sum(r.categories[key].poe_load_w for r in results)
        if not merged.model:
            merged.model = next((r.categories[key].model for r in results if r.categories[key].model), "")
        categories[key] = merged

    power = PowerSummary(ups_model=fw_result.power.ups_model)
    for r in results:
        p = r.power
        power.equipment_w += p.equipment_w
        power.poe_w += p.poe_w
        power.total_w += p.total_w
        power.heat_btu += p.heat_btu
        power.ups_va += p.ups_va
        power.legacy_w += p.legacy_w
        power.breakdown += p.breakdown

    rack = RackSummary()
    ps = PassiveSummary(fiber_type=fw_result.rack.passive.fiber_type, fiber_auto=fw_result.rack.passive.fiber_auto)
    rack.passive = ps
    for floor_name, r in floors:
        rs = r.rack
        rack.plans += rs.plans
        rack.rooms += [f"{floor_name} · {room}" for room in rs.rooms]
        rack.layout_problems += rs.layout_problems
        for attr in (
            "units_equipment",
            "units_panels",
            "units_managers",
            "units_ups",
            "units_other",
            "units_total",
            "units_with_spare",
            "patch_panels",
            "copper_endpoints",
            "cable_m",
            "cable_boxes",
        ):
            setattr(rack, attr, getattr(rack, attr) + getattr(rs, attr))
        for attr, value in vars(rs.passive).items():
            if isinstance(value, int) and not isinstance(value, bool):
                setattr(ps, attr, getattr(ps, attr) + value)
        ps.backbone_m += rs.passive.backbone_m
    rack.idf_count = len(rack.rooms)
    rack.rack_count = len(rack.plans)
    rack.rack_model, rack.rack_size_u = fw_result.rack.rack_model, fw_result.rack.rack_size_u

    site = fw_result.input.model_copy(update={"floors": n_floors, "name": name or fw_result.input.name})
    return SiteResult(
        input=site,
        counts=counts,
        categories=categories,
        core=fw_result.core,
        firewall=fw_result.firewall,
        tier_id=fw_result.tier_id,
        dual_psu=fw_result.dual_psu,
        dual_psu_confirmed=fw_result.dual_psu_confirmed,
        fortios_version=fw_result.fortios_version,
        bom=bom,
        checks=checks,
        power=power,
        rack=rack,
        ip_plan=fw_result.ip_plan,
        addons=fw_result.addons,
    )
