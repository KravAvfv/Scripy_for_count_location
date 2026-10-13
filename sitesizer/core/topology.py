"""Device-level network topology (FortiLink), built from the cabinets of the whole location.

The picture follows the Fortinet "HA-mode FortiGates + MCLAG core" design:

* the FortiGate pair on top, joined by the HA link;
* the core pair (FS-1024E, MCLAG peers) under it, joined by the ISL (MCLAG ICL, at least two links);
  every FortiGate is linked to every core switch (FortiLink, full mesh);
* access switches stand in columns — one stack per cabinet, in the order they stand in it (WSW, VSW,
  ASW top-down), at most ``rules.topology_group_size`` switches each (several stacks of a cabinet get a
  common cabinet frame). ``ring``: switches of a column are daisy-chained, the
  top one goes to the first core and the bottom one to the second (both ends dual-homed, the stack
  is a ring); ``dual``: every switch has a link to each core.

Without a core the FortiGates take the role of the cores (FortiLink straight from the firewall).

Nodes carry the same names as in the cabinets (``BO123-5A-ASW01``). The user's changes
(:class:`~sitesizer.core.models.TopologyEdits`: moved nodes, removed / added links, link types, labels,
switches moved to another column) are applied on top. The same :class:`Topology` — with its link
routes — is painted in the app, exported as PNG/SVG/PDF/Excel and written as a draw.io file.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

from .catalog import Catalog
from .models import RackPlan, SiteResult, TopologyEdits

SWITCH_KINDS = ("wifi_switch", "camera_switch", "access_switch")
POE_KINDS = ("wifi_switch", "camera_switch")
LINK_KINDS = ("fortilink", "stack", "uplink", "isl", "ha", "wan", "custom")

NODE_W = 172.0
NODE_H = 48.0
HEAD_W = 196.0
HEAD_H = 58.0
WAN_W = 112.0
WAN_H = 30.0
PAD = 40.0
COL_GAP = 84.0
STACK_GAP = 22.0
PAIR_GAP = 220.0
LANE_STEP = 7.0
GROUP_PAD = 12.0
GROUP_TITLE_H = 22.0
CAB_PAD = 14.0
CAB_TITLE_H = 24.0
CAB_GAP = 40.0
"""Extra space between the stacks of different cabinets."""


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x <= self.right and self.y <= y <= self.bottom


@dataclass
class TopoNode:
    id: str
    kind: str
    """``wan``, ``firewall``, ``core_switch``, ``wifi_switch``, ``camera_switch``, ``access_switch``."""
    label: str
    model: str = ""
    cabinet: str = ""
    """Cabinet tag (``5A``) the device stands in."""
    group: str = ""
    """Column (stack) of an access switch."""
    rect: Rect = field(default_factory=lambda: Rect(0, 0, NODE_W, NODE_H))
    manual: bool = False


@dataclass
class TopoLinkResult:
    id: str
    a: str
    """Lower end (the switch) for up-links; for HA / ISL the left one."""
    b: str
    kind: str
    label: str = ""
    auto: bool = True
    points: list[tuple[float, float]] = field(default_factory=list)
    """Route: start on ``a``, end on ``b``, corners in between."""
    label_t: float = 0.5
    """Where the label sits along the route (0 = at ``a``, 1 = at ``b``)."""

    def point_at(self, t: float) -> tuple[float, float]:
        """Point at fraction ``t`` of the route's length."""
        segs = list(zip(self.points, self.points[1:], strict=False))
        total = sum(math.hypot(x2 - x1, y2 - y1) for (x1, y1), (x2, y2) in segs)
        left = total * t
        for (x1, y1), (x2, y2) in segs:
            d = math.hypot(x2 - x1, y2 - y1)
            if left <= d and d > 0:
                return x1 + (x2 - x1) * left / d, y1 + (y2 - y1) * left / d
            left -= d
        return self.points[-1] if self.points else (0.0, 0.0)


@dataclass
class TopoGroup:
    id: str
    title: str
    nodes: list[str] = field(default_factory=list)
    rect: Rect = field(default_factory=lambda: Rect(0, 0, 0, 0))


@dataclass
class Topology:
    nodes: list[TopoNode] = field(default_factory=list)
    links: list[TopoLinkResult] = field(default_factory=list)
    groups: list[TopoGroup] = field(default_factory=list)
    cabinets: list[TopoGroup] = field(default_factory=list)
    """A frame around the stacks of one cabinet (when it has several: PoE and ASW)."""
    width: float = 0.0
    height: float = 0.0
    access: str = "ring"
    group_size: int = 5
    problems: list[str] = field(default_factory=list)
    """Ports a device runs out of, and similar findings."""
    title: str = ""
    origin: tuple[float, float] = (0.0, 0.0)
    """Top-left corner of the drawing in node coordinates (negative when nodes were dragged up/left)."""

    def node(self, node_id: str) -> TopoNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def link(self, link_id: str) -> TopoLinkResult | None:
        return next((lk for lk in self.links if lk.id == link_id), None)

    @property
    def empty(self) -> bool:
        return not any(n.kind != "wan" for n in self.nodes)


def link_id(kind: str, a: str, b: str) -> str:
    return f"{kind}:{a}|{b}"


def _plan_switches(plan: RackPlan) -> list[tuple[str, str, str, str]]:
    """(item id, group, label, model) of the active devices of a cabinet, top-down."""
    out = []
    for it in sorted(plan.items, key=lambda i: -i.u):
        if it.group in (*SWITCH_KINDS, "firewall", "core_switch"):
            out.append((it.id, it.group, it.label or it.model, it.model))
    return out


def build_topology(
    result: SiteResult, catalog: Catalog, lang: str = "uk", edits: TopologyEdits | None = None
) -> Topology:
    """Nodes, links, columns and routes of the location's network (see the module docstring)."""
    from ..i18n import Translator

    t = Translator(lang)
    ed = edits if edits is not None else result.input.topology
    size = ed.group_size or catalog.rules.topology_group_size
    topo = Topology(access=ed.access, group_size=size, title=result.input.name)
    tier = catalog.tier(result.tier_id)

    # ---- nodes from the cabinets ---------------------------------------------------------
    fws: list[TopoNode] = []
    cores: list[TopoNode] = []
    columns: dict[str, list[TopoNode]] = {}
    col_titles: dict[str, str] = {}
    col_kind: dict[str, str] = {}
    """Stack id -> its title without the cabinet (used inside a cabinet frame)."""
    col_cab: dict[str, str] = {}
    cab_titles: dict[str, str] = {}
    codes = {"wifi_switch": "WSW", "camera_switch": "VSW", "access_switch": "ASW"}
    for plan in result.rack.plans:
        # the switches of a cabinet form its stack(s) in the order they stand in it, top-down
        members: list[TopoNode] = []
        for item_id, group, label, model in _plan_switches(plan):
            node = TopoNode(f"{plan.tag}/{item_id}", group, label, model, plan.tag)
            if group == "firewall":
                fws.append(node)
            elif group == "core_switch":
                cores.append(node)
            else:
                members.append(node)
        chunks = [members[i : i + size] for i in range(0, len(members), size)]
        cab_titles[plan.tag] = t.t("topo.cabinet", tag=plan.tag)
        for k, chunk in enumerate(chunks, 1):
            gid = f"{plan.tag}:sw" + (f":{k}" if len(chunks) > 1 else "")
            roles = " · ".join(dict.fromkeys(codes.get(n.kind, "") for n in chunk))
            col_titles[gid] = cab_titles[plan.tag]
            col_kind[gid] = t.t("topo.stack", n=k, roles=roles)
            col_cab[gid] = plan.tag
            for n in chunk:
                n.group = gid
            columns[gid] = chunk
    # the user's moves between columns
    switches = {n.id: n for col in columns.values() for n in col}
    for node_id, gid in ed.groups.items():
        moved = switches.get(node_id)
        if moved is None or moved.group == gid:
            continue
        columns[moved.group].remove(moved)
        columns.setdefault(gid, []).append(moved)
        moved.group = gid
        col_titles.setdefault(gid, t.t("topo.group_user"))
    for gid in [g for g, col in columns.items() if not col]:
        del columns[gid]
        col_titles.pop(gid, None)
    for node in (*fws, *cores, *switches.values()):
        node.label = ed.labels.get(node.id, node.label)
    wans: list[TopoNode] = []
    if fws and ed.show_wan:
        names = ["WAN 1", "WAN 2"] if tier.dual_wan else ["WAN"]
        wans = [
            TopoNode(f"wan{i + 1}", "wan", ed.labels.get(f"wan{i + 1}", name), rect=Rect(0, 0, WAN_W, WAN_H))
            for i, name in enumerate(names)
        ]

    # ---- automatic layout ------------------------------------------------------------------
    for n in (*fws, *cores):
        n.rect = Rect(0, 0, HEAD_W, HEAD_H)
    col_list = list(columns.items())
    # the stacks of one cabinet side by side, inside the cabinet's frame
    order = {tag: i for i, tag in enumerate(dict.fromkeys(col_cab.get(g, "") for g, _c in col_list))}
    col_list.sort(key=lambda gc: order[col_cab.get(gc[0], "")])
    per_cab: dict[str, int] = {}
    for gid, _col in col_list:
        if gid in col_cab:
            per_cab[col_cab[gid]] = per_cab.get(col_cab[gid], 0) + 1
    framed = {tag for tag, n in per_cab.items() if n > 1}  # a cabinet with several stacks: a frame around them
    gaps = [
        COL_GAP
        + (
            CAB_GAP
            if col_cab.get(a) != col_cab.get(b) and (col_cab.get(a) in framed or col_cab.get(b) in framed)
            else 0
        )
        for (a, _ca), (b, _cb) in itertools.pairwise(col_list)
    ]
    cols_w = len(col_list) * NODE_W + sum(gaps)
    pair_w = 2 * HEAD_W + PAIR_GAP
    width = max(cols_w, pair_w, 520.0) + 2 * PAD
    cx = width / 2
    y = PAD
    if wans:
        total = len(wans) * WAN_W + (len(wans) - 1) * 60
        x = cx - total / 2
        for w in wans:
            w.rect.x, w.rect.y = x, y
            x += WAN_W + 60
        y += WAN_H + 54

    def place_pair(nodes: list[TopoNode], y: float) -> None:
        if len(nodes) == 1:
            nodes[0].rect.x, nodes[0].rect.y = cx - HEAD_W / 2, y
            return
        total = len(nodes) * HEAD_W + (len(nodes) - 1) * PAIR_GAP
        x = cx - total / 2
        for n in nodes:
            n.rect.x, n.rect.y = x, y
            x += HEAD_W + PAIR_GAP

    if fws:
        place_pair(fws, y)
        y += HEAD_H + 96
    if cores:
        place_pair(cores, y)
        y += HEAD_H
    # the band where up-links run sideways towards the cores
    lanes = sum(1 for _g, col in col_list for _ in range(2 if len(col) > 1 or ed.access == "dual" else 1))
    band = 62 + LANE_STEP * min(lanes, 24)
    top = y + band + GROUP_TITLE_H + GROUP_PAD if (fws or cores) else y + GROUP_TITLE_H
    if framed:
        top += CAB_TITLE_H + CAB_PAD
    x = cx - cols_w / 2
    for i, (_gid, col) in enumerate(col_list):
        yy = top
        for n in col:
            n.rect = Rect(x, yy, NODE_W, NODE_H)
            yy += NODE_H + STACK_GAP
        x += NODE_W + (gaps[i] if i < len(gaps) else 0)

    # ---- the user's positions --------------------------------------------------------------
    all_nodes = [*wans, *fws, *cores, *(n for _g, col in col_list for n in col)]
    for n in all_nodes:
        pos = ed.positions.get(n.id)
        if pos is not None:
            n.rect.x, n.rect.y = pos.x, pos.y
            n.manual = True
    topo.nodes = all_nodes
    for gid, col in col_list:
        tag = col_cab.get(gid, "")
        title = col_kind.get(gid, gid) if tag in framed else col_titles.get(gid, gid)
        topo.groups.append(TopoGroup(gid, ed.labels.get(gid, title), [n.id for n in col]))
    for tag in framed:
        nodes = [n.id for gid, col in col_list if col_cab.get(gid) == tag for n in col]
        cid = f"cab:{tag}"
        topo.cabinets.append(TopoGroup(cid, ed.labels.get(cid, cab_titles[tag]), nodes))
    _fit_groups(topo)

    # ---- links -------------------------------------------------------------------------------
    links: list[TopoLinkResult] = []
    fiber = result.rack.passive.fiber_type.upper() if result.rack.passive.fiber_type else ""

    def medium(a: TopoNode, b: TopoNode, speed: float) -> str:
        sp = f"{speed:g}G"
        if a.cabinet and a.cabinet == b.cabinet:
            return f"{sp} DAC"
        return f"{sp} {fiber}".strip() if fiber else sp

    def speed_of(n: TopoNode, parent: TopoNode) -> float:
        dev = catalog.models.get(n.model)
        up = dev.uplinks.speed_gbps if dev and dev.uplinks else 1.0
        pdev = catalog.models.get(parent.model)
        if pdev and pdev.firewall is not None:
            return min(up, 10.0) if pdev.firewall.ports_10g else 1.0
        if parent.kind in SWITCH_KINDS:  # stacked: uplink port to uplink port
            port = pdev.uplinks.speed_gbps if pdev and pdev.uplinks else up
        else:
            port = pdev.ports.speed_gbps if pdev and pdev.ports else up
        return min(up, port, 10.0)

    def add(kind: str, a: TopoNode, b: TopoNode, label: str = "") -> None:
        links.append(TopoLinkResult(link_id(kind, a.id, b.id), a.id, b.id, kind, label))

    for w in wans:
        for f in fws:
            add("wan", f, w)
    if len(fws) > 1:
        for i in range(len(fws) - 1):
            add("ha", fws[i], fws[i + 1], "HA")
    if cores:
        if len(cores) > 1:
            for i in range(len(cores) - 1):
                add("isl", cores[i], cores[i + 1], "ISL · MCLAG")
        for c in cores:
            for f in fws:
                add("uplink", c, f, medium(c, f, speed_of(c, f) if c.model else 10.0))
    parents = cores or fws
    if parents:
        first, last = parents[0], parents[-1]
        for _gid, col in col_list:
            if not col:
                continue
            if ed.access == "dual":
                for n in col:
                    for p in (first, last) if last is not first else (first,):
                        add("fortilink", n, p, medium(n, p, speed_of(n, p)))
                continue
            top_sw, bottom_sw = col[0], col[-1]
            add("fortilink", top_sw, first, medium(top_sw, first, speed_of(top_sw, first)))
            for a, b in itertools.pairwise(col):
                add("stack", b, a, medium(a, b, speed_of(b, a)))
            if len(col) > 1 or last is not first:
                add("fortilink", bottom_sw, last, medium(bottom_sw, last, speed_of(bottom_sw, last)))
    hidden = set(ed.hidden)
    nodes_by_id = {n.id: n for n in all_nodes}
    out = []
    for lk in links:
        if lk.id in hidden:
            continue
        lk.kind = ed.kinds.get(lk.id, lk.kind)
        lk.label = ed.labels.get(lk.id, lk.label)
        out.append(lk)
    for ul in ed.links:
        if ul.a in nodes_by_id and ul.b in nodes_by_id and ul.a != ul.b:
            out.append(TopoLinkResult(ul.id, ul.a, ul.b, ul.kind, ul.label, auto=False))
    topo.links = out

    # ---- ports ---------------------------------------------------------------------------------
    for n in (*cores, *fws):
        dev = catalog.models.get(n.model)
        if dev is None:
            continue
        cap = 0
        if dev.firewall is not None:
            cap = dev.firewall.ports_10g
        elif dev.ports is not None:
            cap = dev.ports.count + (dev.uplinks.count if dev.uplinks else 0)
        used = sum((2 if lk.kind == "isl" else 1) for lk in out if n.id in (lk.a, lk.b) and lk.kind != "wan")
        if cap and used > cap:
            topo.problems.append(t.t("topo.ports_short", name=n.label, used=used, cap=cap))

    route_links(topo)
    return topo


def _fit_groups(topo: Topology) -> None:
    by_id = {n.id: n for n in topo.nodes}
    for g in topo.groups:
        rects = [by_id[i].rect for i in g.nodes if i in by_id]
        if not rects:
            continue
        x0 = min(r.x for r in rects) - GROUP_PAD
        y0 = min(r.y for r in rects) - GROUP_PAD - GROUP_TITLE_H
        x1 = max(r.right for r in rects) + GROUP_PAD
        y1 = max(r.bottom for r in rects) + GROUP_PAD
        g.rect = Rect(x0, y0, x1 - x0, y1 - y0)
    stacks = {nid: g for g in topo.groups for nid in g.nodes}
    for cab in topo.cabinets:
        rects = [stacks[i].rect for i in cab.nodes if i in stacks] or [by_id[i].rect for i in cab.nodes if i in by_id]
        if not rects:
            continue
        x0 = min(r.x for r in rects) - CAB_PAD
        y0 = min(r.y for r in rects) - CAB_PAD - CAB_TITLE_H
        x1 = max(r.right for r in rects) + CAB_PAD
        y1 = max(r.bottom for r in rects) + CAB_PAD
        cab.rect = Rect(x0, y0, x1 - x0, y1 - y0)


def _spread(count: int, k: int, lo: float, hi: float) -> float:
    return lo + (hi - lo) * (k + 1) / (count + 1)


def _label_t(lk: TopoLinkResult) -> float:
    """Labels near the switch end, so that crossing lines do not stack their labels on one spot."""
    if len(lk.points) < 2 or lk.kind in ("ha", "isl", "stack"):
        return 0.5
    lens = [math.hypot(x2 - x1, y2 - y1) for (x1, y1), (x2, y2) in zip(lk.points, lk.points[1:], strict=False)]
    total = sum(lens) or 1.0
    if lk.kind in ("uplink", "wan"):
        return 0.3
    if lk.kind == "fortilink" and len(lens) > 2:
        (x1, _y1), (x2, _y2) = lk.points[0], lk.points[1]
        if abs(x1 - x2) < 0.5:  # leaves the switch upwards: just above its column
            return max(0.0, lens[0] - 14) / total
        # up a lane beside the column; the two lanes of a gap get their labels at different heights
        return (lens[0] + min(18.0 if x2 > x1 else 46.0, lens[1] / 2)) / total
    return 0.5


def route_links(topo: Topology) -> None:
    """Orthogonal routes for every link; also sets the drawing's size.

    Up-links of a column leave its top switch from the top; the others leave from the side facing
    their core and run up a lane beside the column, then sideways in a band above the columns.
    Ports along a side are spread so that lines do not sit on top of each other.
    """
    nodes = {n.id: n for n in topo.nodes}
    groups = {g.id: g for g in topo.groups}
    _fit_groups(topo)
    columns_top = min((g.rect.y for g in (*topo.groups, *topo.cabinets)), default=0.0)
    heads = [n for n in topo.nodes if n.kind in ("firewall", "core_switch")]
    heads_bottom = max((n.rect.bottom for n in heads), default=0.0)

    def above(n: TopoNode) -> bool:
        g = groups.get(n.group)
        if g is None:
            return False
        return any(o != n.id and nodes[o].rect.y < n.rect.y for o in g.nodes if o in nodes)

    # which side of which node each link uses
    ends: dict[str, dict[str, list[tuple[float, str]]]] = {}

    def claim(node: TopoNode, side: str, other_x: float, lid: str) -> None:
        ends.setdefault(node.id, {}).setdefault(side, []).append((other_x, lid))

    plan: dict[str, tuple[str, str]] = {}
    for lk in topo.links:
        a, b = nodes.get(lk.a), nodes.get(lk.b)
        if a is None or b is None:
            continue
        if lk.kind in ("ha", "isl") and abs(a.rect.cy - b.rect.cy) < 1:
            left = a if a.rect.cx <= b.rect.cx else b
            sa, sb = ("right", "left") if left is a else ("left", "right")
            plan[lk.id] = (sa, sb)
            claim(a, sa, b.rect.cx, lk.id)
            claim(b, sb, a.rect.cx, lk.id)
            continue
        if lk.kind == "stack" and a.group and a.group == b.group and not (a.manual or b.manual):
            plan[lk.id] = ("stack", "stack")
            continue
        lower, upper = (a, b) if a.rect.cy >= b.rect.cy else (b, a)
        side_low = "top"
        if lk.kind == "fortilink" and lower.group and above(lower) and upper.rect.bottom <= columns_top:
            g = groups[lower.group]
            side_low = "left" if upper.rect.cx < g.rect.cx else "right"
        sa, sb = (side_low, "bottom") if lower is a else ("bottom", side_low)
        plan[lk.id] = (sa, sb)
        claim(lower, side_low, upper.rect.cx, lk.id)
        claim(upper, "bottom", lower.rect.cx, lk.id)

    def port(node: TopoNode, side: str, lid: str) -> tuple[float, float]:
        items = sorted(ends.get(node.id, {}).get(side, []))
        k = next((i for i, (_x, i_lid) in enumerate(items) if i_lid == lid), 0)
        r = node.rect
        if side in ("top", "bottom"):
            x = _spread(len(items), k, r.x + 10, r.right - 10)
            return x, (r.y if side == "top" else r.bottom)
        y = _spread(len(items), k, r.y + 6, r.bottom - 6)
        return (r.x if side == "left" else r.right), y

    # lanes: per column side, per link (inner lanes for higher switches)
    lane_x: dict[str, float] = {}
    for g in topo.groups:
        for side in ("left", "right"):
            members = [
                (nodes[o].rect.y, lid) for o in g.nodes if o in nodes for _x, lid in ends.get(o, {}).get(side, [])
            ]
            members.sort()
            for k, (_y, lid) in enumerate(members):
                base = g.rect.x if side == "left" else g.rect.right
                lane_x[lid] = base - 8 - LANE_STEP * k if side == "left" else base + 8 + LANE_STEP * k
    band_links = [lk for lk in topo.links if lk.id in plan and plan[lk.id][0] not in ("stack",)]
    band_y: dict[str, float] = {}
    k = 0
    low_band = columns_top - 30
    for lk in band_links:
        a, b = nodes.get(lk.a), nodes.get(lk.b)
        if a is None or b is None or lk.kind not in ("fortilink", "custom", "stack"):
            continue
        band_y[lk.id] = low_band - LANE_STEP * k
        k += 1
    floor_y = heads_bottom + 18

    for lk in topo.links:
        a, b = nodes.get(lk.a), nodes.get(lk.b)
        if a is None or b is None or lk.id not in plan:
            lk.points = []
            continue
        sa, sb = plan[lk.id]
        if sa == "stack":
            x = a.rect.x + 18
            lo, hi = (a, b) if a.rect.y > b.rect.y else (b, a)
            pts = [(x, lo.rect.y), (x, hi.rect.bottom)]
            lk.points = pts if lo is a else pts[::-1]
            continue
        pa = port(a, sa, lk.id)
        pb = port(b, sb, lk.id)
        if sa in ("left", "right") and sb in ("left", "right"):
            lk.points = [pa, pb]
            continue
        lower_first = sa != "bottom"
        lo_pt, hi_pt = (pa, pb) if lower_first else (pb, pa)
        lo_side = sa if lower_first else sb
        if lo_side in ("left", "right") and lk.id in lane_x:
            lx = lane_x[lk.id]
            by = max(min(band_y.get(lk.id, low_band), lo_pt[1] - 10), floor_y)
            pts = [lo_pt, (lx, lo_pt[1]), (lx, by), (hi_pt[0], by), hi_pt]
        elif abs(lo_pt[0] - hi_pt[0]) < 0.5:
            pts = [lo_pt, hi_pt]
        elif lk.kind in ("uplink", "wan"):
            pts = [lo_pt, hi_pt]  # straight, crossing like the whiteboard sketch
        else:
            band_at = band_y.get(lk.id)
            by = band_at if band_at is not None and hi_pt[1] < band_at < lo_pt[1] else (lo_pt[1] + hi_pt[1]) / 2
            by = max(by, hi_pt[1] + 10)
            pts = [lo_pt, (lo_pt[0], by), (hi_pt[0], by), hi_pt]
        lk.points = pts if lower_first else pts[::-1]

    for lk in topo.links:
        lk.label_t = _label_t(lk)

    rects = [n.rect for n in topo.nodes] + [g.rect for g in (*topo.groups, *topo.cabinets)]
    pts = [p for lk in topo.links for p in lk.points]
    x0 = min([r.x for r in rects] + [p[0] for p in pts], default=PAD)
    y0 = min([r.y for r in rects] + [p[1] for p in pts], default=PAD)
    x1 = max([r.right for r in rects] + [p[0] for p in pts], default=420.0)
    y1 = max([r.bottom for r in rects] + [p[1] for p in pts], default=120.0)
    # nodes dragged past the top / left edge: the drawing grows that way (painters shift by ``origin``)
    topo.origin = (min(0.0, x0 - PAD), min(0.0, y0 - PAD))
    topo.width = x1 + PAD - topo.origin[0]
    topo.height = y1 + PAD + 28 - topo.origin[1]  # footer line
