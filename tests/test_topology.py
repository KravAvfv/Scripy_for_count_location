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
    assert kinds(topo, "ha") == [(fws[0].id, fws[1].id)] * 2  # the HA pair: two links (ha1, ha2)
    assert sorted(lk.port_a for lk in topo.links if lk.kind == "ha") == ["ha1", "ha2"]
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
    edge_ids = {c.get("id") for c in cells if c.get("edge") == "1"}
    port_labels = [c for c in cells if c.get("vertex") == "1" and c.get("parent") in edge_ids]
    vertices = [c for c in cells if c.get("vertex") == "1" and c not in port_labels]
    edges = [c for c in cells if c.get("edge") == "1"]
    assert len(vertices) == len(topo.nodes) + len(topo.groups) + len(topo.cabinets)  # cabinet frames too
    # every port (and module) is a label of its edge
    ends = sum(bool(p or m) for lk in topo.links for p, m in ((lk.port_a, lk.module_a), (lk.port_b, lk.module_b)))
    assert len(port_labels) == ends > 0
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
    assert [c[-5:-2] for c in rack_order] == ["WSW", "ASW", "ASW", "VSW"]
    # more switches than a stack holds: several stacks in one cabinet frame
    small = build_topology(r, catalog, edits=TopologyEdits(group_size=2))
    assert len(small.groups) == 2 and len(small.cabinets) == 1
    assert small.cabinets[0].title == f"Шафа {plan.tag}"
    assert [g.title for g in small.groups] == ["Стек 1 · WSW · ASW", "Стек 2 · ASW · VSW"]
    frame = small.cabinets[0].rect
    for g in small.groups:
        assert frame.x < g.rect.x and g.rect.right < frame.right and frame.y < g.rect.y < frame.bottom


def test_ports_modules_and_cables(catalog: Catalog) -> None:
    from sitesizer.core.topology import endpoints_text, port_choices
    from sitesizer.i18n import Translator

    # the last ports first, alternating uplink / copper: 49, 48, 50, 47…; FortiGate: x1…
    assert [p for p, _c in port_choices("FS-148F", catalog)[:6]] == ["49", "48", "50", "47", "51", "46"]
    assert [p for p, c in port_choices("FS-148F", catalog) if c == "sfp"] == ["49", "50", "51", "52"]
    assert [p for p, c in port_choices("FG-120G", catalog) if c == "sfp"] == ["x1", "x2", "x3", "x4"]
    assert [p for p, c in port_choices("FG-120G", catalog) if c == "rj45"][:2] == ["port8", "port7"]
    t = Translator("uk")
    text = endpoints_text([("cameras", 38), ("other", 0), ("ajax", 4), ("skud", 1)], 48, t)
    assert text == "1–38 Камери · 44–47 Ajax · 48 СКУД"

    site = make_site(
        rooms=[{"sockets": 150, "cameras": 60, "ajax": 4, "skud": 1}, {"sockets": 48}],
        aps=[("low_density", 30)],
        tier=2,
        redundant_psu=False,
        core_count=2,
        location_code="BO1",
    )
    r = size_site(site, catalog)
    topo = build_topology(r, catalog)
    names = {n.id: n.label for n in topo.nodes}
    by = {(names[lk.a], names[lk.b], lk.kind): lk for lk in topo.links}
    isl = by[("BO1-1A-DSW01", "BO1-1A-DSW02", "isl")]
    assert isl.port_a == isl.port_b == "25, 26"  # the 100G QSFP28 ports
    # a stack: the link down from a switch takes 49, the one up 50; DAC in one cabinet, no modules
    down = by[("BO1-1B-ASW02", "BO1-1B-ASW01", "stack")]
    assert (down.port_a, down.port_b) == ("50", "49") and not down.module_a and "DAC" in down.label
    # between cabinets: fibre, the transceiver at both ends
    om4 = catalog.passive.fiber["om4"].transceiver
    fl = by[("BO1-1C-ASW01", "BO1-1A-DSW01", "fortilink")]
    assert fl.module_a == fl.module_b == om4 and fl.label == "10G OM4"
    # no port is used twice on a device
    seen: dict[str, list[str]] = {}
    for lk in topo.links:
        for node, port in ((lk.a, lk.port_a), (lk.b, lk.port_b)):
            for p in filter(None, (x.strip() for x in port.split(","))):
                seen.setdefault(node, []).append(p)
    assert all(len(v) == len(set(v)) for v in seen.values())
    # what the switch ports serve
    nodes = {n.label: n for n in topo.nodes}
    assert nodes["BO1-1A-VSW02"].endpoints == "1–12 Камери · 44–47 Ajax · 48 СКУД"
    assert nodes["BO1-1A-VSW01"].endpoints == "1–48 Камери"
    # the user's ports, modules, cable and endpoints win; an auto port is not given out twice
    key_a, key_b = down.end(down.a), down.end(down.b)
    edits = TopologyEdits(
        ports={key_a: "sfp+3", fl.end(fl.a): "49"},
        modules={key_b: "SFP Foxgate 1330nm"},
        labels={down.id: "Cat6A 0.5 м"},
        endpoints={nodes["BO1-1A-VSW01"].id: "1–40 Камери"},
    )
    topo2 = build_topology(r, catalog, edits=edits)
    lk2 = topo2.link(down.id)
    assert lk2 is not None and lk2.port_a == "sfp+3" and lk2.module_b == "SFP Foxgate 1330nm"
    assert lk2.label == "Cat6A 0.5 м"
    assert next(n for n in topo2.nodes if n.label == "BO1-1A-VSW01").endpoints == "1–40 Камери"
    asw01 = [lk for lk in topo2.links if fl.a in (lk.a, lk.b)]
    ports = [lk.port_a if lk.a == fl.a else lk.port_b for lk in asw01]
    assert ports.count("49") == 1  # typed on one link, so the other gets another port


def test_link_medium_decides_ports_modules_and_cable(catalog: Catalog) -> None:
    r = size_site(make_site(sockets=200, tier=2, redundant_psu=False, location_code="BO1"), catalog)
    topo = build_topology(r, catalog)
    wan = next(lk for lk in topo.links if lk.kind == "wan")
    # towards the provider's ODF: a single-mode module in the FortiGate's WAN port
    assert wan.medium == "fiber" and wan.port_a == "wan1" and wan.module_a == catalog.passive.fiber["os2"].transceiver
    assert not wan.port_b and not wan.module_b
    stack = next(lk for lk in topo.links if lk.kind == "stack")
    assert stack.medium == "dac" and "DAC" in stack.label and not (stack.module_a or stack.module_b)
    # copper: RJ45 ports from the end (48, 47…), a patch cord
    t2 = build_topology(r, catalog, edits=TopologyEdits(media={stack.id: "copper"}))
    lk = t2.link(stack.id)
    assert lk is not None and lk.medium == "copper" and lk.label.endswith("Cat6A")
    assert lk.port_a == lk.port_b == "48" and not lk.module_a
    # modules + fibre: the transceiver at both ends, SFP ports
    t3 = build_topology(r, catalog, edits=TopologyEdits(media={stack.id: "fiber"}))
    lk = t3.link(stack.id)
    om4 = catalog.passive.fiber[r.rack.passive.fiber_type].transceiver
    assert lk is not None and lk.module_a == lk.module_b == om4 and lk.port_a in ("49", "50")
    # the user's cable text still wins over the medium's
    t4 = build_topology(r, catalog, edits=TopologyEdits(media={stack.id: "fiber"}, labels={stack.id: "LC-LC 2 м"}))
    assert t4.link(stack.id).label == "LC-LC 2 м"


def test_device_figures_in_drawio(tmp_path: Path, catalog: Catalog) -> None:
    import base64

    from sitesizer.exporters.device_icons import icon_svg

    r = size_site(make_site(**BIG), catalog)
    topo = build_topology(r, catalog)
    xml = topology_drawio(topo)
    root = ET.fromstring(xml)
    fw = next(n for n in topo.nodes if n.kind == "firewall")
    cell = next(c for c in root.iter("mxCell") if c.get("value", "").startswith(f"<b>{fw.label}"))
    style = dict(kv.split("=", 1) for kv in cell.get("style", "").split(";") if "=" in kv)
    assert style["shape"] == "label" and style["imageAlign"] == "left"
    data = style["image"].removeprefix("data:image/svg+xml,")
    assert base64.b64decode(data).decode() == icon_svg("firewall")


def test_a_cabinet_is_one_stack_however_many_switches(catalog: Catalog) -> None:
    # six access switches in one cabinet (the second cabinet removed): one stack, not 5 + 1
    r = size_site(
        make_site(sockets=48 * 6, rack_size_u=42, location_code="BO107", floor=4, layout={"removed": ["mdf-2"]}),
        catalog,
    )
    assert [len([it for it in p.items if it.group == "access_switch"]) for p in r.rack.plans] == [6]
    topo = build_topology(r, catalog)
    assert [len(g.nodes) for g in topo.groups] == [6] and not topo.cabinets
    assert len([lk for lk in topo.links if lk.kind == "stack"]) == 5


def test_crossing_lines_hop(catalog: Catalog) -> None:
    from sitesizer.exporters.topology import _crossings

    # an upright line over a level one hops at the crossing; touching ends do not
    assert _crossings(((50, 0), (50, 100)), [((0, 40), (100, 40))]) == [(50, 40)]
    assert _crossings(((50, 0), (50, 100)), [((50, 40), (100, 40))]) == []
    assert _crossings(((0, 10), (100, 10)), [((30, 0), (30, 50)), ((70, 0), (70, 50))]) == [(30, 10), (70, 10)]
    # draw.io: crossing edges get line jumps (arcs)
    topo = build_topology(size_site(make_site(**BIG), catalog), catalog)
    xml = topology_drawio(topo)
    assert "jumpStyle=arc;" in xml


def test_modules_clear_the_band(catalog: Catalog) -> None:
    # the band under the cores leaves room for the modules written down from their ports
    from sitesizer.core.topology import text_w

    r = size_site(make_site(**BIG), catalog)
    topo = build_topology(r, catalog)
    cores = [n for n in topo.nodes if n.kind == "core_switch"]
    module = text_w(topo.fiber_module)
    band_top = min(
        y for lk in topo.links if lk.kind == "fortilink" for (_x, y) in lk.points[1:-1]
    )  # the highest level run towards the cores
    assert band_top >= max(c.rect.bottom for c in cores) + 16 + module
