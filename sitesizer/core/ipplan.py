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


def plan_segments(
    requests: list[SegmentRequest],
    rules: IpRules,
    base_network: str = "",
    texts: dict[str, str] | None = None,
) -> IpPlan:
    """Size each segment and, if ``base_network`` is given, carve aligned, non-overlapping
    subnets out of it (largest first so alignment wastes nothing). Output keeps request order.

    ``texts`` supplies localized notes: ``note``, ``no_dhcp``, ``overflow``, ``bad_network``.
    """
    t = texts or {}
    plan = IpPlan(base_network=base_network.strip())
    results: dict[str, IpSegmentResult] = {}
    for req in requests:
        size = suggest_subnet(req.hosts, rules.buffer, rules.smallest_prefix)
        results[req.id] = IpSegmentResult(
            id=req.id,
            name=req.name,
            vlan=req.vlan,
            hosts=req.hosts,
            prefix=size.prefix,
            capacity=size.capacity,
            note=t.get("note", ""),
        )

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
        seg.network = str(net)
        first, last = _usable_bounds(net)
        if first is not None and last is not None:
            seg.gateway = str(first)
            if req.dhcp:
                usable = int(last) - int(first) + 1
                offset = 1 + rules.static_reserve if usable > rules.static_reserve + 2 else 1
                dhcp_first = first + min(offset, usable - 1)
                seg.dhcp_range = f"{dhcp_first} – {last}" if usable > 1 else str(first)
            else:
                seg.dhcp_range = t.get("no_dhcp", "static")
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
