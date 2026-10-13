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
from typing import TYPE_CHECKING

from .catalog import Catalog
from .models import RackItem, RackPlan, SiteResult, TopologyEdits

if TYPE_CHECKING:
    from ..i18n import Translator

SWITCH_KINDS = ("wifi_switch", "camera_switch", "access_switch")
POE_KINDS = ("wifi_switch", "camera_switch")
LINK_KINDS = ("fortilink", "stack", "uplink", "isl", "ha", "wan", "custom")

NODE_W = 232.0
NODE_H = 60.0
"""A switch: name, model and the line of what its ports serve."""
HEAD_W = 236.0
HEAD_H = 58.0
WAN_W = 140.0
WAN_H = 40.0
PAD = 40.0
COL_GAP = 84.0
STACK_GAP = 46.0
"""Room for the port numbers at both ends of a stack link and the cable between them."""
STACK_X = 30.0
"""Where a stack link runs, from the left edge of its switches (port labels go left of it)."""
PAIR_GAP = 220.0
PORT_STEP = 30.0
"""Room per link along the bottom of a FortiGate / core (its port number sits there)."""
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
    u: int = 0
    """Unit of the device in its cabinet (decides the DAC length)."""
    endpoints: str = ""
    """What its copper ports serve (``1–38 Камери · 44–47 Ajax · 48 СКУД``)."""


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
    speed: float = 10.0
    """Gbit/s of the link (decides the cable's label)."""
    medium: str = "dac"
    """``dac`` (a DAC cable, no modules), ``fiber`` (a module at each end + a fibre cord) or ``copper``."""
    port_a: str = ""
    """Port of ``a`` the cable goes into (``49``, ``x1``)."""
    port_b: str = ""
    module_a: str = ""
    """Module (transceiver) in ``a``'s port; empty for a DAC or copper."""
    module_b: str = ""

    def end(self, node_id: str) -> str:
        """``<link id>@<node id>`` — the key of one end in :class:`TopologyEdits` ports / modules."""
        return f"{self.id}@{node_id}"

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
    fiber_module: str = ""
    """The location's fibre transceiver (put at both ends of a fibre link)."""
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


def _plan_switches(plan: RackPlan) -> list[RackItem]:
    """The active devices of a cabinet, top-down."""
    return [
        it for it in sorted(plan.items, key=lambda i: -i.u) if it.group in (*SWITCH_KINDS, "firewall", "core_switch")
    ]


ENDPOINT_ORDER = ("sockets", "aps", "cameras", "other", "ajax", "skud")
TAIL_KINDS = ("other", "ajax", "skud")
"""Taken from the last copper port down (``48`` СКУД, ``44–47`` Ajax)."""


def endpoints_text(endpoints: list[tuple[str, int]], ports: int, t: Translator) -> str:
    """``1–38 Камери · 44–47 Ajax · 48 СКУД``: the main devices from port 1, the rest from the last copper port."""

    def rng(a: int, b: int) -> str:
        return str(a) if a == b else f"{a}–{b}"

    parts: list[tuple[int, str]] = []
    start = 1
    for kind, n in endpoints:
        if kind not in TAIL_KINDS and n > 0:
            parts.append((start, f"{rng(start, start + n - 1)} {t.t(f'topo.ep.{kind}')}"))
            start += n
    end = ports
    for kind in reversed(TAIL_KINDS):
        n = sum(k for e, k in endpoints if e == kind)
        if n > 0:
            parts.append((end - n + 1, f"{rng(end - n + 1, end)} {t.t(f'topo.ep.{kind}')}"))
            end -= n
    return " · ".join(text for _s, text in sorted(parts))


def _port_class(kind: str) -> str:
    k = kind.upper()
    return "qsfp" if "QSFP" in k else "sfp" if "SFP" in k else "rj45"


def port_choices(model: str, catalog: Catalog) -> list[tuple[str, str]]:
    """(port, class) of a device in the order links take them: the last ports first, alternating
    between the first uplink ports and the last copper ones — ``49, 48, 50, 47…`` on a 48+4 switch
    (a DAC / fibre link only takes the SFP ones: 49, 50, 51, 52). FortiGate: ``x1…`` (10G SFP+)."""
    dev = catalog.models.get(model)
    if dev is None:
        return []
    if dev.firewall is not None:
        copper = dev.ports.count if dev.ports is not None else FW_COPPER_PORTS
        return [(f"x{i}", "sfp") for i in range(1, dev.firewall.ports_10g + 1)] + [
            (f"port{i}", "rj45") for i in range(copper, 0, -1)
        ]
    if dev.ports is None:
        return []
    n_ports, n_up = dev.ports.count, dev.uplinks.count if dev.uplinks else 0
    pc, uc = _port_class(dev.ports.type), _port_class(dev.uplinks.type) if dev.uplinks else "rj45"
    out: list[tuple[str, str]] = []
    for k in range(max(n_ports, n_up)):
        if k < n_up:
            out.append((str(n_ports + 1 + k), uc))
        if k < n_ports:
            out.append((str(n_ports - k), pc))
    return out


def _end_priority(lk: TopoLinkResult, node_id: str) -> int:
    """Which links of a device take its ports first: ISL, the stack link down, the one up, the core…"""
    if lk.kind == "isl":
        return 0
    if lk.kind == "stack":
        return 1 if lk.b == node_id else 2
    return {"uplink": 3, "fortilink": 4}.get(lk.kind, 5)


MEDIA = ("dac", "fiber", "copper")
FW_COPPER_PORTS = 8
"""Copper ports assumed on a FortiGate whose catalog entry does not list them."""


def auto_medium(lk: TopoLinkResult, a: TopoNode, b: TopoNode) -> str:
    """DAC in one cabinet, modules + fibre between cabinets and to the provider, copper for HA."""
    if lk.kind == "ha":
        return "copper"
    if lk.kind == "isl":
        return "dac"
    if lk.kind == "wan" or (a.cabinet and b.cabinet and a.cabinet != b.cabinet):
        return "fiber"
    return "dac" if lk.speed >= 10 else "copper"  # a 1G link in one cabinet: a patch cord


def provider_module(catalog: Catalog) -> str:
    """The module of a FortiGate's port towards the provider's ODF (single-mode when the catalog has it)."""
    spec = catalog.passive.fiber.get("os2") or next(iter(catalog.passive.fiber.values()), None)
    return spec.transceiver if spec else ""


def cable_label(lk: TopoLinkResult, a: TopoNode, b: TopoNode, fiber: str, catalog: Catalog, t: Translator) -> str:
    """What joins the two ends: ``10G DAC 1 м`` (by the units apart), ``10G OM4``, ``1G Cat6A``."""
    if lk.kind == "isl":
        return "ISL · MCLAG"
    if lk.kind == "ha":
        return "HA"
    sp = f"{lk.speed:g}G"
    if lk.medium == "copper":
        return f"{sp} Cat6A"
    if lk.medium == "fiber":
        return t.t("topo.cable_provider") if lk.kind == "wan" else f"{sp} {fiber}".strip()
    metres = 1 if abs(a.u - b.u) <= catalog.rules.dac_short_max_u else 3
    return f"{sp} DAC {t.t('topo.metres', n=metres)}"


def assign_ports(topo: Topology, catalog: Catalog, ed: TopologyEdits) -> None:
    """Port and module at both ends of every link; the user's ports / modules win (and are kept free).

    DAC and fibre take SFP ports (the ISL the QSFP ones), copper the RJ45 ones — from the end, as
    :func:`port_choices` lists them. Fibre gets a module at each end (towards the provider: on the
    FortiGate's side only), DAC and copper none.
    """
    nodes = {n.id: n for n in topo.nodes}
    used: dict[str, set[str]] = {nid: set() for nid in nodes}
    for lk in topo.links:
        for nid in (lk.a, lk.b):
            typed = ed.ports.get(lk.end(nid))
            if typed and nid in used:
                used[nid].update(p.strip() for p in typed.split(","))
    choices = {nid: port_choices(n.model, catalog) for nid, n in nodes.items()}
    wan_no = {n.id: i for i, n in enumerate((n for n in topo.nodes if n.kind == "wan"), 1)}
    ends = [(lk, nid) for lk in topo.links for nid in (lk.a, lk.b) if nid in nodes]
    ends.sort(key=lambda e: _end_priority(e[0], e[1]))  # stable: link order within a priority
    provider = provider_module(catalog)

    def take(nid: str, want: str, count: int) -> str:
        picked: list[str] = []
        tiers = {"qsfp": ["qsfp", "sfp", "rj45"], "sfp": ["sfp", "qsfp", "rj45"], "rj45": ["rj45", "sfp", "qsfp"]}[want]
        for tier in tiers:
            for name, cls in choices[nid]:
                if len(picked) == count:
                    break
                if cls == tier and name not in used[nid] and name not in picked:
                    picked.append(name)
        used[nid].update(picked)
        return ", ".join(sorted(picked, key=lambda p: (len(p), p)))

    for lk, nid in ends:
        node = nodes[nid]
        typed = ed.ports.get(lk.end(nid))
        if typed is not None:
            port = typed
        elif node.kind == "wan":
            port = ""
        elif lk.kind == "wan":
            port = f"wan{wan_no.get(lk.b if nid == lk.a else lk.a, 1)}"
        elif lk.kind == "ha":
            port = "ha"
        elif lk.kind == "isl" and lk.medium != "copper":
            port = take(nid, "qsfp", 2)
        else:
            port = take(nid, "rj45" if lk.medium == "copper" else "sfp", 1)
        auto = ""
        if lk.medium == "fiber" and node.kind != "wan":
            auto = provider if lk.kind == "wan" else topo.fiber_module
        module = ed.modules.get(lk.end(nid), auto)
        if nid == lk.a:
            lk.port_a, lk.module_a = port, module
        else:
            lk.port_b, lk.module_b = port, module


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
        for it in _plan_switches(plan):
            node = TopoNode(f"{plan.tag}/{it.id}", it.group, it.label or it.model, it.model, plan.tag, u=it.u)
            if it.group in SWITCH_KINDS:
                dev = catalog.models.get(it.model)
                ports = dev.ports.count if dev is not None and dev.ports is not None else 48
                node.endpoints = ed.endpoints.get(node.id, endpoints_text(it.endpoints, ports, t))
            if it.group == "firewall":
                fws.append(node)
            elif it.group == "core_switch":
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
    col_list = list(columns.items())
    # a head with many links below it gets wider, so that their port numbers do not overlap
    parents_n = len(cores or fws) or 1
    below = sum(len(col) if ed.access == "dual" else (2 if len(col) > 1 else 1) for _g, col in col_list)
    head_w = max(HEAD_W, PORT_STEP * math.ceil(below / parents_n) + 24)
    for n in fws:
        n.rect = Rect(0, 0, HEAD_W if cores else head_w, HEAD_H)
    for n in cores:
        n.rect = Rect(0, 0, head_w, HEAD_H)
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
    pair_w = 2 * head_w + PAIR_GAP
    width = max(cols_w, pair_w, 520.0) + 2 * PAD
    cx = width / 2
    y = PAD
    if wans:
        total = len(wans) * WAN_W + (len(wans) - 1) * 60
        x = cx - total / 2
        for w in wans:
            w.rect.x, w.rect.y = x, y
            x += WAN_W + 60
        y += WAN_H + 150  # room for the port, the module and the cable towards the provider

    def place_pair(nodes: list[TopoNode], y: float) -> None:
        total = sum(n.rect.w for n in nodes) + (len(nodes) - 1) * PAIR_GAP
        x = cx - total / 2
        for n in nodes:
            n.rect.x, n.rect.y = x, y
            x += n.rect.w + PAIR_GAP

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

    def add(kind: str, a: TopoNode, b: TopoNode, label: str = "", speed: float = 10.0) -> None:
        links.append(TopoLinkResult(link_id(kind, a.id, b.id), a.id, b.id, kind, label, speed=speed))

    for w in wans:
        for f in fws:
            add("wan", f, w, speed=1.0)
    if len(fws) > 1:
        for i in range(len(fws) - 1):
            add("ha", fws[i], fws[i + 1], "HA", speed=1.0)
    if cores:
        if len(cores) > 1:
            for i in range(len(cores) - 1):
                add("isl", cores[i], cores[i + 1], "ISL · MCLAG", speed=100.0)
        for c in cores:
            for f in fws:
                add("uplink", c, f, speed=speed_of(c, f) if c.model else 10.0)
    parents = cores or fws
    if parents:
        first, last = parents[0], parents[-1]
        for _gid, col in col_list:
            if not col:
                continue
            if ed.access == "dual":
                for n in col:
                    for p in (first, last) if last is not first else (first,):
                        add("fortilink", n, p, speed=speed_of(n, p))
                continue
            top_sw, bottom_sw = col[0], col[-1]
            add("fortilink", top_sw, first, speed=speed_of(top_sw, first))
            for a, b in itertools.pairwise(col):
                add("stack", b, a, speed=speed_of(b, a))
            if len(col) > 1 or last is not first:
                add("fortilink", bottom_sw, last, speed=speed_of(bottom_sw, last))
    hidden = set(ed.hidden)
    nodes_by_id = {n.id: n for n in all_nodes}
    out = []
    for lk in links:
        if lk.id in hidden:
            continue
        lk.kind = ed.kinds.get(lk.id, lk.kind)
        out.append(lk)
    for ul in ed.links:
        if ul.a in nodes_by_id and ul.b in nodes_by_id and ul.a != ul.b:
            out.append(TopoLinkResult(ul.id, ul.a, ul.b, ul.kind, ul.label, auto=False))
    # the medium (DAC / modules + fibre / copper) decides the ports, modules and the cable's label
    fspec = catalog.passive.fiber.get(result.rack.passive.fiber_type)
    topo.fiber_module = fspec.transceiver if fspec else ""
    fiber = result.rack.passive.fiber_type.upper()
    for lk in out:
        a, b = nodes_by_id[lk.a], nodes_by_id[lk.b]
        lk.medium = ed.media.get(lk.id) or auto_medium(lk, a, b)
        typed = ed.labels.get(lk.id) if lk.auto else (lk.label or None)
        lk.label = typed if typed is not None else cable_label(lk, a, b, fiber, catalog, t)
    topo.links = out
    assign_ports(topo, catalog, ed)

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
    if lk.kind == "wan":
        return 0.9  # the FortiGate's end carries its port and the module along the line
    if lk.kind == "uplink":
        return 0.5 if lk.medium == "fiber" else 0.3  # modules run along both ends of a fibre
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
            x = a.rect.x + STACK_X
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
