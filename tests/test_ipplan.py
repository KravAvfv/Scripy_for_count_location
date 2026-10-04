from __future__ import annotations

import ipaddress
import itertools

import pytest

from sitesizer.core.catalog import Catalog
from sitesizer.core.ipplan import SegmentRequest, plan_segments, suggest_subnet
from sitesizer.core.sizing import size_site

from .conftest import make_site


@pytest.mark.parametrize(
    ("hosts", "prefix", "capacity"),
    [
        (0, 30, 2),  # zero is treated as one host
        (1, 30, 2),
        (2, 29, 6),  # ceil(2.6)+2 = 5 → /29
        (10, 28, 14),  # 13+2 = 15 → /28
        (11, 27, 30),  # ceil(14.3)+2 = 17 → /27
        (100, 24, 254),
        (195, 24, 254),  # ceil(253.5)+2 = 256 → /24 exactly
        (196, 23, 510),
        (900, 21, 2046),
    ],
)
def test_suggest_subnet(hosts: int, prefix: int, capacity: int) -> None:
    s = suggest_subnet(hosts)
    assert (s.prefix, s.capacity) == (prefix, capacity)


def test_smallest_prefix_cap() -> None:
    assert suggest_subnet(1, smallest_prefix=28).prefix == 28


def _reqs() -> list[SegmentRequest]:
    return [
        SegmentRequest("data", "Data", 10, 120, True),
        SegmentRequest("wifi", "Wi-Fi", 30, 600, True),
        SegmentRequest("cctv", "CCTV", 50, 70, False),
        SegmentRequest("mgmt", "Mgmt", 99, 40, False),
    ]


def test_carving_is_non_overlapping_and_aligned(catalog: Catalog) -> None:
    plan = plan_segments(_reqs(), catalog.rules.ip, "10.50.0.0/16")
    assert not plan.error
    nets = [ipaddress.IPv4Network(s.network) for s in plan.segments]
    base = ipaddress.IPv4Network("10.50.0.0/16")
    for a, b in itertools.combinations(nets, 2):
        assert not a.overlaps(b)
    for seg, net in zip(plan.segments, nets, strict=True):
        assert net.subnet_of(base)
        assert net.prefixlen == seg.prefix
        assert seg.gateway == str(net.network_address + 1)
    assert [s.id for s in plan.segments] == ["data", "wifi", "cctv", "mgmt"]  # input order kept


def test_dhcp_range_skips_static_block(catalog: Catalog) -> None:
    plan = plan_segments(_reqs(), catalog.rules.ip, "10.0.0.0/16")
    data = plan.segments[0]
    net = ipaddress.IPv4Network(data.network)
    first, last = data.dhcp_range.split(" – ")
    assert ipaddress.IPv4Address(first) == net.network_address + 1 + 1 + catalog.rules.ip.static_reserve
    assert ipaddress.IPv4Address(last) == net.broadcast_address - 1
    assert plan.segments[2].dhcp_range == "static"


def test_overflow_reported(catalog: Catalog) -> None:
    plan = plan_segments(_reqs(), catalog.rules.ip, "192.168.1.0/24", {"overflow": "too small"})
    assert plan.error == "too small"


def test_bad_network(catalog: Catalog) -> None:
    plan = plan_segments(_reqs(), catalog.rules.ip, "10.300.0.0/16", {"bad_network": "bad"})
    assert plan.error == "bad"
    assert all(not s.network for s in plan.segments)


def test_tiny_segments(catalog: Catalog) -> None:
    plan = plan_segments([SegmentRequest("x", "X", 5, 1, True)], catalog.rules.ip, "10.0.0.0/29")
    seg = plan.segments[0]
    assert seg.prefix == 30 and seg.network == "10.0.0.0/30"
    assert seg.gateway == "10.0.0.1" and seg.dhcp_range.endswith("10.0.0.2")


def test_engine_ip_plan_optional_segments(catalog: Catalog) -> None:
    r = size_site(
        make_site(mode="extended", sockets=20, guest_clients=30, voice_phones=0, ip={"base_network": "10.1.0.0/16"}),
        catalog,
    )
    assert r.ip_plan is not None
    ids = [s.id for s in r.ip_plan.segments]
    assert "guest" in ids and "voice" not in ids
    r2 = size_site(make_site(mode="extended", sockets=20, guest_clients=30, ip={"segments": {"guest": False}}), catalog)
    assert r2.ip_plan is not None and "guest" not in [s.id for s in r2.ip_plan.segments]


def test_vlan_override(catalog: Catalog) -> None:
    r = size_site(make_site(mode="extended", sockets=20, ip={"vlan_overrides": {"data": 110}}), catalog)
    assert r.ip_plan is not None and r.ip_plan.segments[0].vlan == 110


def test_engine_ip_error_check(catalog: Catalog) -> None:
    r = size_site(make_site(mode="extended", sockets=2000, ip={"base_network": "10.0.0.0/24"}), catalog)
    assert any(c.code == "IP_PLAN" for c in r.checks)
