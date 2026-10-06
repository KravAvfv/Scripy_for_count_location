"""IP / VLAN planning: subnet sizing (prototype formula) and non-overlapping carving."""

from __future__ import annotations

import ipaddress
import math
from dataclasses import dataclass

from .catalog import IpRules
from .models import IpPlan, IpSegmentResult


@dataclass(frozen=True)
class SubnetSize:
    prefix: int
    capacity: int


def suggest_subnet(host_count: int, buffer: float = 0.3, smallest_prefix: int = 30) -> SubnetSize:
    """Smallest subnet for ``host_count`` hosts plus ``buffer`` growth.

    Same formula as the console prototype: ``needed = ceil(max(h, 1) * (1 + buffer)) + 2``
    (network + broadcast), rounded up to a power of two. ``smallest_prefix`` caps the
    prefix length (default /30, like the prototype).
    """
    needed = math.ceil(max(host_count, 1) * (1 + buffer)) + 2
    bits = max(0, math.ceil(math.log2(needed))) if needed > 1 else 0
    while 2**bits < needed:  # guard against float rounding
        bits += 1
    bits = max(bits, 32 - smallest_prefix)
    prefix = 32 - bits
    return SubnetSize(prefix=prefix, capacity=max(2**bits - 2, 0))


@dataclass
class SegmentRequest:
    id: str
    name: str
    vlan: int
    hosts: int
    dhcp: bool
    custom: bool = False


def _sized(req: SegmentRequest, rules: IpRules, note: str, prefix: int | None = None) -> IpSegmentResult:
    size = suggest_subnet(req.hosts, rules.buffer, rules.smallest_prefix)
    pfx = prefix if prefix and 0 < prefix <= 32 else size.prefix
    return IpSegmentResult(
        id=req.id,
        name=req.name,
        vlan=req.vlan,
        hosts=req.hosts,
        prefix=pfx,
        capacity=_capacity(pfx),
        note=note,
        custom=req.custom,
        dhcp=req.dhcp,
    )


def _capacity(prefix: int) -> int:
    if prefix >= 31:
        return 2 ** (32 - prefix)
    return 2 ** (32 - prefix) - 2


def _fill_addresses(seg: IpSegmentResult, net: ipaddress.IPv4Network, rules: IpRules, t: dict[str, str]) -> None:
    seg.network = str(net)
    seg.mask = str(net.netmask)
    seg.prefix = net.prefixlen
    seg.capacity = _capacity(net.prefixlen)
    first, last = _usable_bounds(net)
    if first is None or last is None:
        return
    seg.gateway = str(first)
    if seg.dhcp:
        usable = int(last) - int(first) + 1
        offset = 1 + rules.static_reserve if usable > rules.static_reserve + 2 else 1
        dhcp_first = first + min(offset, usable - 1)
        seg.dhcp_range = f"{dhcp_first} – {last}" if usable > 1 else str(first)
    else:
        seg.dhcp_range = t.get("no_dhcp", "static")


def plan_by_template(
    requests: list[SegmentRequest],
    rules: IpRules,
    location_id: int,
    prefixes: dict[str, int] | None = None,
    texts: dict[str, str] | None = None,
) -> IpPlan:
    """One subnet per VLAN from ``rules.id_template`` (default ``10.{id}.{vlan}.0/24``).

    ``location_id`` is the site's second octet. ``prefixes`` overrides the mask per segment
    (e.g. a /23 for a large Wi-Fi VLAN). Segments that are too small or overlap are reported.
    """
    t = texts or {}
    prefixes = prefixes or {}
    plan = IpPlan(location_id=location_id, template=rules.id_template)
    nets: list[tuple[ipaddress.IPv4Network, IpSegmentResult]] = []
    for req in requests:
        seg = _sized(req, rules, t.get("note", ""))
        plan.segments.append(seg)
        try:
            net = ipaddress.IPv4Network(rules.id_template.format(id=location_id, vlan=req.vlan), strict=False)
            if prefixes.get(req.id):
                net = ipaddress.IPv4Network((net.network_address, prefixes[req.id]), strict=False)
        except (ValueError, KeyError, IndexError):
            seg.note = t.get("bad_template", "VLAN does not fit the address template")
            continue
        _fill_addresses(seg, net, rules, t)
        need = math.ceil(max(req.hosts, 0) * (1 + rules.buffer))
        if need > seg.capacity:
            seg.note = t.get("too_small", "too small").format(need=need, cap=seg.capacity)
            seg.too_small = True
        for other_net, other in nets:
            if net.overlaps(other_net):
                plan.error = t.get("overlap", "overlap").format(a=other.name, b=seg.name)
        nets.append((net, seg))
        plan.used_addresses += net.num_addresses
    plan.base_network = rules.id_template.replace("{id}", str(location_id)).replace("{vlan}", "VLAN")
    return plan


def plan_segments(
    requests: list[SegmentRequest],
    rules: IpRules,
    base_network: str = "",
    texts: dict[str, str] | None = None,
    prefixes: dict[str, int] | None = None,
) -> IpPlan:
    """Size each segment and, if ``base_network`` is given, carve aligned, non-overlapping
    subnets out of it (largest first so alignment wastes nothing). Output keeps request order.

    ``texts`` supplies localized notes: ``note``, ``no_dhcp``, ``overflow``, ``bad_network``.
    """
    t = texts or {}
    plan = IpPlan(base_network=base_network.strip())
    results: dict[str, IpSegmentResult] = {}
    for req in requests:
        results[req.id] = _sized(req, rules, t.get("note", ""), (prefixes or {}).get(req.id))

    if not plan.base_network or not results:
        plan.segments = [results[r.id] for r in requests]
        return plan

    try:
        base = ipaddress.IPv4Network(plan.base_network, strict=False)
    except (ValueError, ipaddress.AddressValueError):
        plan.error = t.get("bad_network", "invalid network")
        plan.segments = [results[r.id] for r in requests]
        return plan
    plan.base_network = str(base)

    cursor = int(base.network_address)
    end = int(base.broadcast_address)
    order = sorted(requests, key=lambda r: (results[r.id].prefix, r.vlan))
    for req in order:
        seg = results[req.id]
        block = 2 ** (32 - seg.prefix)
        start = (cursor + block - 1) // block * block  # align
        if start + block - 1 > end:
            plan.error = t.get("overflow", "base network too small")
            break
        net = ipaddress.IPv4Network((start, seg.prefix))
        _fill_addresses(seg, net, rules, t)
        cursor = start + block
        plan.used_addresses += block
    plan.segments = [results[r.id] for r in requests]
    return plan


def _usable_bounds(
    net: ipaddress.IPv4Network,
) -> tuple[ipaddress.IPv4Address | None, ipaddress.IPv4Address | None]:
    """First and last usable host address of ``net``."""
    if net.prefixlen >= 31:
        return net.network_address, net.broadcast_address
    return net.network_address + 1, net.broadcast_address - 1
