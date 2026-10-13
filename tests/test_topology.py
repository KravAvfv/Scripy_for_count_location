"""Device-level topology: HA FortiGates, MCLAG core, access stacks; the user's edits; draw.io."""

from __future__ import annotations

import os
from pathlib import Path
from xml.etree import ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sitesizer.core.catalog import Catalog
from sitesizer.core.models import TopologyEdits
from sitesizer.core.sizing import size_site
from sitesizer.core.topology import build_topology
from sitesizer.exporters.drawio import export_drawio, topology_drawio
from sitesizer.exporters.topology import TopologyDiagram

from .conftest import make_site

BIG = dict(sockets=480, cameras=60, tier=1, aggregation="yes", location_code="BO123", aps=[("corridor", 30)])


def kinds(topo, kind: str) -> list[tuple[str, str]]:
    return [(lk.a, lk.b) for lk in topo.links if lk.kind == kind]


def test_whiteboard_design(catalog: Catalog) -> None:
    """2 FortiGates (HA), 2 cores (ISL), each FortiGate to each core, stacks: top → core 1, bottom → core 2."""
    r = size_site(make_site(**BIG), catalog)
    topo = build_topology(r, catalog)
    fws = [n for n in topo.nodes if n.kind == "firewall"]
    cores = [n for n in topo.nodes if n.kind == "core_switch"]
    assert len(fws) == 2 and len(cores) == 2
    assert kinds(topo, "ha") == [(fws[0].id, fws[1].id)]
    assert kinds(topo, "isl") == [(cores[0].id, cores[1].id)]
    assert sorted(kinds(topo, "uplink")) == sorted((c.id, f.id) for c in cores for f in fws)
    # names come from the cabinets
    assert fws[0].label == "BO123-1A-FW01" and cores[0].label == "BO123-1A-DSW01"
    # PoE switches have their own stack, apart from the access ones
    first = topo.groups[0]
    assert all(topo.node(i).kind in ("wifi_switch", "camera_switch") for i in first.nodes)  # type: ignore[union-attr]
    for g in topo.groups:
        col = g.nodes
        assert len(col) <= topo.group_size
        ups = {(lk.a, lk.b) for lk in topo.links if lk.kind == "fortilink" and lk.a in col}
        if len(col) > 1:
            assert ups == {(col[0], cores[0].id), (col[-1], cores[1].id)}
            assert len([lk for lk in topo.links if lk.kind == "stack" and lk.a in col]) == len(col) - 1
    # every link has a route that starts and ends on its devices
    for lk in topo.links:
        a, b = topo.node(lk.a), topo.node(lk.b)
        assert a is not None and b is not None and len(lk.points) >= 2
        for node, (x, y) in ((a, lk.points[0]), (b, lk.points[-1])):
            r_ = node.rect
            assert r_.x - 0.5 <= x <= r_.right + 0.5 and r_.y - 0.5 <= y <= r_.bottom + 0.5


def test_dual_homed_and_group_size(catalog: Catalog) -> None:
    r = size_site(make_site(**BIG), catalog)
    topo = build_topology(r, catalog, edits=TopologyEdits(access="dual", group_size=3))
    switches = [n for n in topo.nodes if n.group]
    assert not kinds(topo, "stack")
    assert len(kinds(topo, "fortilink")) == 2 * len(switches)
    assert all(len(g.nodes) <= 3 for g in topo.groups)


def test_without_core_the_firewall_takes_the_stacks(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=96, location_code="BO1"), catalog)
    topo = build_topology(r, catalog)
    fw = next(n for n in topo.nodes if n.kind == "firewall")
    assert not any(n.kind == "core_switch" for n in topo.nodes)
    assert {b for _a, b in kinds(topo, "fortilink")} == {fw.id}


def test_user_edits(catalog: Catalog) -> None:
    r = size_site(make_site(**BIG), catalog)
    auto = build_topology(r, catalog)
    sw = next(n for n in auto.nodes if n.kind == "access_switch")
    stack = next(lk for lk in auto.links if lk.kind == "stack")
    other = next(g for g in auto.groups if sw.id not in g.nodes)
    ed = TopologyEdits.model_validate(
        {
            "positions": {sw.id: {"x": 900, "y": 40}},
            "hidden": [stack.id],
            "links": [{"id": "u-1", "a": sw.id, "b": auto.nodes[1].id, "kind": "custom", "label": "резерв"}],
            "labels": {sw.id: "Склад-1"},
            "kinds": {},
            "groups": {sw.id: other.id},
        }
    )
    topo = build_topology(r, catalog, edits=ed)
    node = topo.node(sw.id)
    assert node is not None and node.label == "Склад-1" and node.manual and (node.rect.x, node.rect.y) == (900, 40)
    assert node.group == other.id and sw.id in next(g for g in topo.groups if g.id == other.id).nodes
    assert topo.link(stack.id) is None
    user = topo.link("u-1")
    assert user is not None and not user.auto and user.label == "резерв" and len(user.points) >= 2


def test_topology_is_shared_by_every_floor() -> None:
    from sitesizer.core.location import LOCATION_FIELDS

    assert "topology" in LOCATION_FIELDS


def test_drawio_file(tmp_path: Path, catalog: Catalog) -> None:
    r = size_site(make_site(**BIG), catalog)
    topo = build_topology(r, catalog)
    path = export_drawio(topo, tmp_path / "t.drawio")
    root = ET.parse(path).getroot()
    assert root.tag == "mxfile"
    cells = root.findall(".//mxCell")
    vertices = [c for c in cells if c.get("vertex") == "1"]
    edges = [c for c in cells if c.get("edge") == "1"]
    assert len(vertices) == len(topo.nodes) + len(topo.groups) + len(topo.cabinets)  # cabinet frames too
    assert len(edges) == len(topo.links)
    ids = {c.get("id") for c in cells}
    for e in edges:
        assert e.get("source") in ids and e.get("target") in ids
        assert e.find("mxGeometry") is not None
    # switches sit in their stack container (moving the stack in draw.io moves them)
    sw = next(n for n in topo.nodes if n.group)
    cell = next(c for c in vertices if c.get("id") == "c-" + "".join(ch if ch.isalnum() else "_" for ch in sw.id))
    assert cell.get("parent", "").startswith("c-g_")
    assert sw.label in topology_drawio(topo)


def test_painter_and_hit(catalog: Catalog) -> None:
    from PySide6.QtCore import QPointF

    from sitesizer.exporters.diagram import render_image

    r = size_site(make_site(**BIG), catalog)
    d = TopologyDiagram(r, catalog)
    img = render_image(d, scale=1.0)  # type: ignore[arg-type]
    assert img.width() > 400 and img.height() > 400
    n = next(n for n in d.topo.nodes if n.kind == "firewall")
    assert d.hit(QPointF(n.rect.cx, n.rect.cy) + d.offset) == ("node", n.id)
    lk = next(lk for lk in d.topo.links if lk.kind == "ha")
    x, y = lk.point_at(0.5)
    assert d.hit(QPointF(x, y) + d.offset) == ("link", lk.id)


def test_a_cabinet_is_one_stack_in_its_rack_order(catalog: Catalog) -> None:
    # one cabinet with Wi-Fi, video and access switches: one stack, in the order they stand in the cabinet
    r = size_site(make_site(sockets=96, cameras=20, aps=[("low_density", 10)], location_code="BO1"), catalog)
    assert len(r.rack.plans) == 1
    plan = r.rack.plans[0]
    rack_order = [it.label for it in sorted(plan.items, key=lambda i: -i.u) if it.group.endswith("_switch")]
    rack_order = [n for n in rack_order if "DSW" not in n]
    topo = build_topology(r, catalog)
    assert [g.title for g in topo.groups] == [f"Шафа {plan.tag}"] and not topo.cabinets
    labels = {n.id: n.label for n in topo.nodes}
    assert [labels[i] for i in topo.groups[0].nodes] == rack_order
    assert [c[-5:-2] for c in rack_order] == ["WSW", "VSW", "ASW", "ASW"]
    # more switches than a stack holds: several stacks in one cabinet frame
    small = build_topology(r, catalog, edits=TopologyEdits(group_size=2))
    assert len(small.groups) == 2 and len(small.cabinets) == 1
    assert small.cabinets[0].title == f"Шафа {plan.tag}"
    assert [g.title for g in small.groups] == ["Стек 1 · WSW · VSW", "Стек 2 · ASW"]
    frame = small.cabinets[0].rect
    for g in small.groups:
        assert frame.x < g.rect.x and g.rect.right < frame.right and frame.y < g.rect.y < frame.bottom
