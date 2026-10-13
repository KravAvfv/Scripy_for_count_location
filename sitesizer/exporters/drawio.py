"""draw.io (diagrams.net) export of the network topology.

Writes an uncompressed ``.drawio`` file: every device is a shape, every stack (column) a container
holding its switches (move the container — the switches follow), every link an edge glued to its two
devices, with the same corners as in the app. Colours, names, models and link labels match the
in-app picture, so the file opens looking the same and stays fully editable.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from ..core.topology import Rect, TopoLinkResult, Topology, TopoNode
from .device_icons import ICON_H, ICON_W, drawio_image
from .topology import LIGHT, TopoColors

FONT = "Inter,Segoe UI,Helvetica,Arial,sans-serif"


def _sub(el: ET.Element, tag: str, **attrs: object) -> ET.Element:
    """``ET.SubElement`` with attributes given as keywords (``parent=`` is an attribute here)."""
    return ET.SubElement(el, tag, {k: str(v) for k, v in attrs.items()})


def _style(**kw: object) -> str:
    return "".join(f"{k}={v};" for k, v in kw.items())


def _cell_id(raw: str) -> str:
    return "c-" + "".join(ch if ch.isalnum() else "_" for ch in raw)


def _rel(r: Rect, x: float, y: float) -> tuple[float, float]:
    """Point on a shape's border as draw.io connection constraints (0..1)."""
    fx = (x - r.x) / r.w if r.w else 0.5
    fy = (y - r.y) / r.h if r.h else 0.5
    return round(min(1.0, max(0.0, fx)), 4), round(min(1.0, max(0.0, fy)), 4)


def topology_drawio(topo: Topology, colors: TopoColors = LIGHT, name: str = "Topology") -> str:
    """The ``.drawio`` XML text."""
    ox, oy = topo.origin
    sx, sy = -ox, -oy  # shift so that nothing has negative coordinates

    mxfile = ET.Element(
        "mxfile",
        host="SiteSizer",
        modified=datetime.now().isoformat(timespec="seconds"),
        agent="SiteSizer",
        version="24.0.0",
        type="device",
    )
    diagram = _sub(mxfile, "diagram", id="topology", name=name or "Topology")
    model = _sub(
        diagram,
        "mxGraphModel",
        dx="1200",
        dy="800",
        grid="1",
        gridSize="10",
        guides="1",
        tooltips="1",
        connect="1",
        arrows="0",
        fold="1",
        page="1",
        pageScale="1",
        pageWidth=str(int(topo.width + 40)),
        pageHeight=str(int(topo.height + 40)),
        math="0",
        shadow="0",
    )
    root = _sub(model, "root")
    _sub(root, "mxCell", id="0")
    _sub(root, "mxCell", id="1", parent="0")

    def geometry(cell: ET.Element, r: Rect, parent: Rect | None = None) -> None:
        px, py = (parent.x, parent.y) if parent else (-sx, -sy)
        _sub(
            cell,
            "mxGeometry",
            x=f"{r.x - px:.1f}",
            y=f"{r.y - py:.1f}",
            width=f"{r.w:.1f}",
            height=f"{r.h:.1f}",
            **{"as": "geometry"},
        )

    # cabinet frames under everything, then the stacks (containers), so that they lie under the links
    for cab in topo.cabinets:
        cell = _sub(
            root,
            "mxCell",
            id=_cell_id("c-" + cab.id),
            value=cab.title,
            style=_style(
                rounded=1,
                arcSize=4,
                whiteSpace="wrap",
                html=1,
                fillColor="#FAFBFC",
                strokeColor="#9AA7B4",
                fontColor="#1F2D3A",
                fontFamily=FONT,
                fontSize=12,
                fontStyle=1,
                align="left",
                verticalAlign="top",
                spacingLeft=10,
                spacingTop=3,
            ),
            vertex="1",
            parent="1",
        )
        geometry(cell, cab.rect)
    parent_of: dict[str, tuple[str, Rect]] = {}
    for g in topo.groups:
        gid = _cell_id("g-" + g.id)
        cell = _sub(
            root,
            "mxCell",
            id=gid,
            value=g.title,
            style=_style(
                rounded=1,
                arcSize=6,
                whiteSpace="wrap",
                html=1,
                container=1,
                collapsible=0,
                dashed=1,
                fillColor="#F4F7FA",
                strokeColor="#B7C4D1",
                fontColor="#5E6B77",
                fontFamily=FONT,
                fontSize=11,
                fontStyle=1,
                align="left",
                verticalAlign="top",
                spacingLeft=10,
                spacingTop=2,
            ),
            vertex="1",
            parent="1",
        )
        geometry(cell, g.rect)
        for nid in g.nodes:
            parent_of[nid] = (gid, g.rect)

    def node_cell(n: TopoNode) -> None:
        fill, edge, text = colors.node(n.kind)
        value = (
            n.label if not n.model else f'<b>{_esc(n.label)}</b><br><font style="font-size:10px">{_esc(n.model)}</font>'
        )
        if n.endpoints:
            value += f'<br><font style="font-size:10px" color="{colors.link("fortilink")}">{_esc(n.endpoints)}</font>'
        style = _style(
            shape="label",  # a box with the device's figure on the left (switch, FortiGate, cloud)
            image=drawio_image(n.kind),
            imageWidth=round(ICON_W),
            imageHeight=round(ICON_H),
            imageAlign="left",
            imageVerticalAlign="middle",
            spacingLeft=round(ICON_W) + 8,
            rounded=1,
            arcSize=50 if n.kind == "wan" else 16,
            whiteSpace="wrap",
            html=1,
            fillColor=fill,
            strokeColor=edge,
            strokeWidth=2 if n.kind in ("firewall", "core_switch") else 1,
            fontColor=text,
            fontFamily=FONT,
            fontSize=12,
        )
        parent, prect = parent_of.get(n.id, ("1", None))
        cell = _sub(root, "mxCell", id=_cell_id(n.id), value=value, style=style, vertex="1", parent=parent)
        geometry(cell, n.rect, prect)

    for n in topo.nodes:
        node_cell(n)

    nodes = {n.id: n for n in topo.nodes}
    for lk in topo.links:
        a, b = nodes.get(lk.a), nodes.get(lk.b)
        if a is None or b is None:
            continue
        _edge(root, lk, a, b, colors, sx, sy)

    ET.indent(mxfile)
    return ET.tostring(mxfile, encoding="unicode", xml_declaration=False)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _edge(
    root: ET.Element, lk: TopoLinkResult, a: TopoNode, b: TopoNode, colors: TopoColors, sx: float, sy: float
) -> None:
    color = colors.link(lk.kind)
    width = {"isl": 3, "ha": 2, "uplink": 2, "wan": 1}.get(lk.kind, 1.5)
    style: dict[str, object] = {
        "endArrow": "none",
        "startArrow": "none",
        "html": 1,
        "rounded": 1,
        "strokeColor": color,
        "strokeWidth": width,
        "fontColor": color,
        "fontFamily": FONT,
        "fontSize": 10,
        "labelBackgroundColor": "#FFFFFF",
    }
    if lk.kind == "wan":
        style["dashed"] = 1
    if lk.kind == "isl":
        style["shape"] = "link"  # a double line: the ICL is two links
        style["width"] = 4
    if len(lk.points) >= 2:
        ex, ey = _rel(a.rect, *lk.points[0])
        nx, ny = _rel(b.rect, *lk.points[-1])
        style.update(exitX=ex, exitY=ey, exitPerimeter=0, entryX=nx, entryY=ny, entryPerimeter=0)
    cell = _sub(
        root,
        "mxCell",
        id=_cell_id("l-" + lk.id),
        value=lk.label,
        style=_style(**style),
        edge="1",
        parent="1",
        source=_cell_id(a.id),
        target=_cell_id(b.id),
    )
    # label position along the edge: -1 = source end, 1 = target end
    geo = _sub(cell, "mxGeometry", relative="1", x=f"{lk.label_t * 2 - 1:.3f}", **{"as": "geometry"})
    corners = lk.points[1:-1]
    if corners:
        arr = _sub(geo, "Array", **{"as": "points"})
        for x, y in corners:
            _sub(arr, "mxPoint", x=f"{x + sx:.1f}", y=f"{y + sy:.1f}")
    # port (and module) at each end: labels of the edge, so they move with it
    for end, port, module in ((-0.9, lk.port_a, lk.module_a), (0.9, lk.port_b, lk.module_b)):
        if not (port or module):
            continue
        value = "<br>".join(x for x in (f"<b>{_esc(port)}</b>" if port else "", _esc(module)) if x)
        label = _sub(
            root,
            "mxCell",
            id=_cell_id(f"p-{lk.id}{end}"),
            value=value,
            style=_style(
                edgeLabel=1,
                html=1,
                align="left",
                verticalAlign="middle",
                resizable=0,
                points="[]",
                fontColor=color,
                fontFamily=FONT,
                fontSize=9,
                labelBackgroundColor="#FFFFFF",
            ),
            vertex="1",
            connectable="0",
            parent=cell.get("id", ""),
        )
        lg = _sub(label, "mxGeometry", x=f"{end:.2f}", relative="1", **{"as": "geometry"})
        _sub(lg, "mxPoint", x="6", y="0", **{"as": "offset"})


def export_drawio(topo: Topology, path: str | Path, name: str = "") -> Path:
    p = Path(path)
    p.write_text(topology_drawio(topo, name=name or topo.title or "Topology"), encoding="utf-8")
    return p
