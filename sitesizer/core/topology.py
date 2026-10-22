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

Passive parts (on by default, ``TopologyEdits.show_odf`` / ``show_panels``):

* every optical patch panel (ODF) of the cabinets is a node with its duplex ports (``1…6`` for a
  12-fibre cable); a fibre link between cabinets runs through them — switch → ODF of its cabinet,
  port k → the ODF at the other end of the cable, port k → the core (several cabinets apart: through
  each pair of ODFs on the way, patched in the cabinets between). The ODFs of the head cabinet (the
  FortiGates / the core) stand in a row under the core, the others above the stack of their cabinet;
* copper patch panels stand left of their switch, with the switch ports they go to (``1–24 → 1–24``).

Which port of an ODF a link takes, its duplexes, notes on its ports and the switch ports of a copper
panel are the user's to change (``odf_map``, ``odf_ports``, ``port_notes``, ``panel_map``).

Nodes carry the same names as in the cabinets (``BO123-5A-ASW01``). The user's changes
(:class:`~sitesizer.core.models.TopologyEdits`: moved nodes, removed / added links, link types, labels,
switches moved to another column) are applied on top. The same :class:`Topology` — with its link
routes — is painted in the app, exported as PNG/SVG/PDF/Excel and written as a draw.io file.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .catalog import Catalog, FirewallSpec
from .models import RackItem, RackPlan, SiteResult, TopologyEdits

if TYPE_CHECKING:
    from ..i18n import Translator

SWITCH_KINDS = ("wifi_switch", "camera_switch", "access_switch")
POE_KINDS = ("wifi_switch", "camera_switch")
LINK_KINDS = ("fortilink", "stack", "uplink", "isl", "ha", "wan", "custom")
PASSIVE_KINDS = ("odf", "panel")
"""Optical (ODF) and copper patch panels: nodes the links run through, not devices."""

NODE_W = 232.0
NODE_H = 60.0
"""A switch: name, model and the line of what its ports serve."""
HEAD_W = 236.0
HEAD_H = 58.0
WAN_W = 220.0
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
TRUNK_STEP = 5.0
"""Between the level lines of the cables / cords through the ODFs."""
GROUP_PAD = 12.0
GROUP_TITLE_H = 22.0
CAB_PAD = 14.0
CAB_TITLE_H = 24.0
CAB_GAP = 40.0
"""Extra space between the stacks of different cabinets."""
ODF_CAP_W = 78.0
"""Caption of an ODF (its name, where its cable goes, the cable) left of its ports."""
CELL_W = 16.0
"""One duplex port of an ODF."""
ODF_H = 46.0
ODF_GAP = 64.0
"""Between an ODF and the first switch under it (the patch cords run sideways here)."""
ODF_VGAP = 14.0
ODF_MAX_PORTS = 48
PANEL_W = 128.0
PANEL_GAP = 46.0
"""Between a copper patch panel and its switch."""
MODULE_CHAR_W = 5.6
"""Width of one character of a module's name as the painters write it (9 px Inter)."""


def text_w(text: str) -> float:
    """About how wide a module's name is drawn (it runs along its line)."""
    return MODULE_CHAR_W * len(text) + 8 if text else 0.0


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
    room: int = 0
    """Telecom room of its cabinet."""
    ports: int = 0
    """ODF: its duplex ports; copper panel: its ports."""
    peer: str = ""
    """ODF: the ODF at the other end of its cable (node id), ``isp`` for the provider's one."""
    caption: str = ""
    """ODF: where its cable goes and the cable (``→ 1A0 · OM4 12 вол.``); panel: ``1–24 → 1–24``."""
    cells: dict[int, str] = field(default_factory=dict)
    """ODF: port -> the link that goes through it."""
    notes: dict[int, str] = field(default_factory=dict)
    """ODF / panel: port -> the user's note on what is plugged in there."""
    switch: str = ""
    """Copper panel: the switch its ports go to."""
    head: bool = False
    """ODF: stands in the cabinet of the FortiGates / the core (drawn in the row under the core)."""
    cable: str = ""
    """ODF: the cable at its back (``OM4 · 12 вол.``), as typed by the user or automatic."""
    auto_ports: int = 0
    """ODF: its duplex ports without the user's number (half the fibres of its cable)."""

    def cell_rect(self, port: int) -> Rect:
        """Box of an ODF's duplex port ``port`` (1-based)."""
        x = self.rect.x + ODF_CAP_W + (port - 1) * CELL_W
        return Rect(x, self.rect.y + 4, CELL_W, self.rect.h - 8)

    @property
    def shown_ports(self) -> int:
        """Ports drawn: all of them, more when links were given ports past the last one."""
        return max(self.ports, max(self.cells, default=0), max(self.notes, default=0))


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
    room: int = -1
    """Telecom room of its access switch (its colour); -1 for the links of the firewall and the core."""
    via: list[tuple[str, int]] = field(default_factory=list)
    """ODF ports the link runs through from ``a`` to ``b``: (ODF node id, port)."""
    typed_label: bool = False
    """The cable's label was typed by the user (drawn even when the link runs through ODFs)."""

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
    passive: list[str] = field(default_factory=list)
    """ODFs and copper patch panels drawn inside the stack's frame (not members of the stack)."""


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
    fiber: str = ""
    """The location's fibre (``OM4``, ``OS2``) for the cables' names."""
    lang: str = "uk"
    """Language of the drawing's texts (the legend of an export)."""
    room_names: dict[int, str] = field(default_factory=dict)
    """Telecom room number on the drawing (the colour of its links) -> its short name (``1A``)."""
    """Top-left corner of the drawing in node coordinates (negative when nodes were dragged up/left)."""

    def node(self, node_id: str) -> TopoNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def link(self, link_id: str) -> TopoLinkResult | None:
        return next((lk for lk in self.links if lk.id == link_id), None)

    def links_through(self, odf_id: str) -> list[TopoLinkResult]:
        """Links that go through an ODF or end on it."""
        return [lk for lk in self.links if odf_id in (lk.a, lk.b) or any(o == odf_id for o, _p in lk.via)]

    @property
    def empty(self) -> bool:
        return not any(n.kind != "wan" for n in self.nodes)


def link_id(kind: str, a: str, b: str) -> str:
    return f"{kind}:{a}|{b}"


def short_name(label: str) -> str:
    """``BO72-1B0-ASW01`` -> ``1B0-ASW01`` (the site code is the same on the whole drawing)."""
    parts = label.split("-")
    return "-".join(parts[1:]) if len(parts) >= 3 and parts[0].isalnum() else label


def link_title(topo: Topology, lk: TopoLinkResult) -> str:
    """``1B0-ASW01 :50 ⇄ 1A0-DSW01 :20`` — a link as the installers name it."""

    def end(nid: str, port: str) -> str:
        n = topo.node(nid)
        name = short_name(n.label) if n is not None else nid
        return f"{name} :{port}" if port else name

    return f"{end(lk.a, lk.port_a)} ⇄ {end(lk.b, lk.port_b)}"


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
        names = fw_ports(dev.firewall)
        if names:
            return [(n, "sfp" if n.startswith(("x", "sfp")) else "rj45") for n in names]
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


FW_1G_PORTS = ("a", "b", "internal1", "internal2", "internal3", "internal4", "internal5", "internal6")
"""A FortiGate without SFP+ (FG-80F): FortiLink ports a, b, then the internal ones (no port8, no x1)."""
FW_1G_HA = ("internal6", "internal5")


def fw_ports(fw: FirewallSpec) -> list[str]:
    """The FortiGate's data ports links take, in order (empty: ``x1…`` + ``port8…``)."""
    if fw.port_names:
        return list(fw.port_names)
    return list(FW_1G_PORTS) if fw.ports_10g == 0 else []


def ha_ports(fw: FirewallSpec | None) -> tuple[str, str]:
    """Heartbeat ports (ha1-link, ha2-link) of a FortiGate: dedicated ``ha1/ha2`` or two data ports."""
    if fw is not None and len(fw.ha_ports) >= 2:
        return fw.ha_ports[0], fw.ha_ports[1]
    if fw is not None and not fw.ha_ports and not fw.port_names and fw.ports_10g == 0:
        return FW_1G_HA
    return "ha1", "ha2"


MEDIA = ("dac", "fiber", "copper")
HA2 = "#2"
"""Suffix of the second HA link's id."""
FW_COPPER_PORTS = 8
"""Copper ports assumed on a FortiGate whose catalog entry does not list them."""


def auto_medium(lk: TopoLinkResult, a: TopoNode, b: TopoNode) -> str:
    """DAC in one cabinet, modules + fibre between cabinets and to the provider, copper for HA."""
    if lk.kind in ("ha", "patch") or "panel" in (a.kind, b.kind):
        return "copper"
    if "odf" in (a.kind, b.kind):
        return "fiber"
    if lk.kind == "isl":
        return "dac"
    if lk.kind == "wan" or (a.cabinet and b.cabinet and a.cabinet != b.cabinet):
        return "fiber"
    return "dac" if lk.speed >= 10 else "copper"  # a 1G link in one cabinet: a patch cord


def provider_module(catalog: Catalog, fw_model: str = "") -> str:
    """The module of a FortiGate's port towards the provider's ODF (single-mode when the catalog has it);
    a FortiGate without SFP+ (FG-80F) takes a 1G one in its wan/sfp shared port."""
    dev = catalog.models.get(fw_model) if fw_model else None
    if dev is not None and dev.firewall is not None and dev.firewall.ports_10g == 0:
        return catalog.rules.provider_module_1g
    spec = catalog.passive.fiber.get("os2") or next(iter(catalog.passive.fiber.values()), None)
    return spec.transceiver if spec else ""


def cable_label(lk: TopoLinkResult, a: TopoNode, b: TopoNode, fiber: str, catalog: Catalog, t: Translator) -> str:
    """What joins the two ends: ``10G DAC 1 м`` (by the units apart), ``10G OM4``, ``1G Cat6A``."""
    if lk.kind == "isl":
        return "ISL · MCLAG"
    if lk.kind == "ha":
        return "HA"
    if lk.kind == "patch":
        return ""
    sp = f"{lk.speed:g}G"
    if lk.medium == "copper":
        return f"{sp} Cat6A"
    if lk.medium == "fiber":
        return t.t("topo.cable_provider") if lk.kind == "wan" else f"{sp} {fiber}".strip()
    metres = 1 if dac_model(lk, a, b, catalog) == catalog.rules.dac_short_model else 3
    return f"{sp} DAC {t.t('topo.metres', n=metres)}"


def assign_ports(topo: Topology, catalog: Catalog, ed: TopologyEdits, t: Translator | None = None) -> None:
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
    ends = [
        (lk, nid)
        for lk in topo.links
        for nid in (lk.a, lk.b)
        if nid in nodes and lk.kind != "patch" and nodes[nid].kind not in PASSIVE_KINDS
    ]
    ends.sort(key=lambda e: _end_priority(e[0], e[1]))  # stable: link order within a priority
    fw_spec = {
        nid: dev.firewall
        for nid, n in nodes.items()
        if n.kind == "firewall" and (dev := catalog.models.get(n.model)) is not None and dev.firewall is not None
    }
    # heartbeat ports that are data ports (FG-80F) are not taken by the other links
    for lk in topo.links:
        if lk.kind == "ha":
            for nid in (lk.a, lk.b):
                if nid in fw_spec and nid in used:
                    used[nid].update(p for p in ha_ports(fw_spec[nid]) if p != "ha1" and p != "ha2")

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

    def to_copper_fw(lk: TopoLinkResult, nid: str) -> bool:
        other = nodes.get(lk.b if nid == lk.a else lk.a)
        return (
            lk.medium == "copper"
            and lk.kind in ("fortilink", "uplink")
            and nodes[nid].kind in (*SWITCH_KINDS, "core_switch")
            and other is not None
            and other.kind == "firewall"
        )

    no_sfp: dict[str, list[tuple[str, str]]] = {}
    """FortiGate -> (RJ45 port, device) of the fibre links that cannot go into it."""
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
            hb = ha_ports(fw_spec.get(nid))
            port = hb[1] if lk.id.endswith(HA2) else hb[0]
        elif lk.kind == "isl" and lk.medium != "copper":
            port = take(nid, "qsfp", 2)
        elif to_copper_fw(lk, nid):
            port = take(nid, "sfp", 1)  # a switch towards a FortiGate without SFP+: copper module in its uplink
        else:
            port = take(nid, "rj45" if lk.medium == "copper" else "sfp", 1)
        auto = ""
        if lk.medium == "fiber" and node.kind != "wan":
            auto = provider_module(catalog, node.model) if lk.kind == "wan" else topo.fiber_module
        elif to_copper_fw(lk, nid) and port and dict(choices[nid]).get(port) == "sfp":
            auto = catalog.rules.copper_module
        if (
            lk.medium == "fiber"
            and lk.kind != "wan"
            and node.kind == "firewall"
            and port
            and dict(choices[nid]).get(port) == "rj45"
        ):
            auto = ""  # an RJ45 port takes no module (FG-80F: its SFPs are the WAN ones)
            if lk.end(nid) not in ed.modules:
                other = nodes.get(lk.b if nid == lk.a else lk.a)
                no_sfp.setdefault(nid, []).append((port, other.label if other else "?"))
        module = ed.modules.get(lk.end(nid), auto)
        if nid == lk.a:
            lk.port_a, lk.module_a = port, module
        else:
            lk.port_b, lk.module_b = port, module
    if t is not None:
        for nid, found in no_sfp.items():
            ports = ", ".join(p for p, _o in found)
            others = ", ".join(dict.fromkeys(o for _p, o in found))
            topo.problems.append(t.t("topo.fw_no_sfp", name=nodes[nid].label, port=ports, other=others))


def dac_model(lk: TopoLinkResult, a: TopoNode, b: TopoNode, catalog: Catalog) -> str:
    """The DAC of a link: QSFP28 for the ISL, else 1 m or 3 m by the units the two devices stand apart."""
    rules = catalog.rules
    if lk.kind == "isl":
        return rules.dac_isl_model
    near = a.cabinet == b.cabinet and abs(a.u - b.u) <= rules.dac_short_max_u
    return rules.dac_short_model if near else rules.dac_long_model


def link_parts(topo: Topology, catalog: Catalog) -> dict[str, dict[str, tuple[int, str]]]:
    """Cabinet tag -> {model: (qty, ``dac`` / ``module``)}: the DACs and the modules (SFP / SFP+
    transceivers) on the links of the drawing, the user's media and modules included.

    A DAC belongs to the cabinet of its lower end; a module to the cabinet of the device it sits in.
    The ISL takes one DAC per port it uses (``25, 26``).
    """
    nodes = {n.id: n for n in topo.nodes}
    out: dict[str, dict[str, tuple[int, str]]] = {}

    def add(tag: str, model: str, kind: str, n: int = 1) -> None:
        if not model or n <= 0:
            return
        parts = out.setdefault(tag, {})
        have = parts.get(model, (0, kind))
        parts[model] = (have[0] + n, have[1])

    for lk in topo.links:
        a, b = nodes.get(lk.a), nodes.get(lk.b)
        if a is None or b is None:
            continue
        if lk.medium == "dac" and lk.kind != "patch":
            ports = len([p for p in lk.port_a.split(",") if p.strip()]) if lk.kind == "isl" else 1
            add(a.cabinet or b.cabinet, dac_model(lk, a, b, catalog), "dac", max(1, ports))
        for node, module in ((a, lk.module_a), (b, lk.module_b)):
            if node.kind not in ("wan", *PASSIVE_KINDS) and module.strip():
                add(node.cabinet, module.strip(), "module")
    return out


ROOM_KINDS = ("fortilink", "stack", "custom")
"""Link kinds coloured by the telecom room of their switch."""
LEGEND_ORDER = ("wan", "ha", "uplink", "isl", "room", "fortilink", "stack", "custom", "patch", "odf")


@dataclass
class LegendEntry:
    """One line of the legend: a colour (a link kind, or the links of a telecom room) and its cables."""

    kind: str
    room: int
    title: str
    cables: str
    count: int


def cable_kind(lk: TopoLinkResult, fiber: str, t: Translator) -> str:
    """The kind of cable of a link, without its length: ``10G DAC``, ``10G OM4``, ``1G Cat6A``."""
    sp = f"{lk.speed:g}G"
    if lk.kind == "isl" and lk.medium == "dac":
        return f"{sp} DAC QSFP28"
    if lk.medium == "copper":
        return "Cat6A" if lk.kind in ("ha", "patch") else f"{sp} Cat6A"
    if lk.medium == "fiber":
        return t.t("legend.provider") if lk.kind == "wan" else f"{sp} {fiber}".strip()
    return f"{sp} DAC"


def legend(topo: Topology, t: Translator) -> list[LegendEntry]:
    """What every colour of the drawing means and which cables its links are (one line per colour)."""
    found: dict[tuple[str, int], LegendEntry] = {}
    cables: dict[tuple[str, int], list[str]] = {}
    for lk in topo.links:
        room = lk.room if lk.kind in ROOM_KINDS and lk.room >= 0 else -1
        key = ("room" if room >= 0 else lk.kind, room)
        if key not in found:
            if room >= 0:
                title = t.t("legend.room", room=topo.room_names.get(room, str(room + 1)))
            else:
                title = t.t(f"legend.{lk.kind}")
            found[key] = LegendEntry(lk.kind, room, title, "", 0)
            cables[key] = []
        found[key].count += 1
        kind = cable_kind(lk, topo.fiber, t)
        if kind not in cables[key]:
            cables[key].append(kind)
    for key, entry in found.items():
        entry.cables = " · ".join(cables[key])
    odfs = sum(1 for n in topo.nodes if n.kind == "odf")
    if odfs:
        found[("odf", -1)] = LegendEntry("odf", -1, t.t("legend.odf"), "", odfs)
    order = {k: i for i, k in enumerate(LEGEND_ORDER)}
    return sorted(found.values(), key=lambda e: (order.get("room" if e.room >= 0 else e.kind, 99), e.room))


def odf_width(ports: int) -> float:
    return ODF_CAP_W + max(1, ports) * CELL_W + 6


def port_range(text: str) -> list[int]:
    """``1-24`` / ``1, 3, 5–21`` -> the port numbers (anything else is skipped)."""
    out: list[int] = []
    for part in text.replace("–", "-").replace(";", ",").split(","):
        bits = [b.strip() for b in part.split("-")]
        try:
            if len(bits) == 1 and bits[0]:
                out.append(int(bits[0]))
            elif len(bits) == 2:
                lo, hi = int(bits[0]), int(bits[1])
                out.extend(range(min(lo, hi), max(lo, hi) + 1))
        except ValueError:
            continue
    return out


def compact_range(ports: list[int]) -> str:
    """[1, 2, 3, 5] -> ``1–3, 5``."""
    out: list[str] = []
    run: list[int] = []
    for n in [*sorted(set(ports)), None]:
        if n is not None and run and n == run[-1] + 1:
            run.append(n)
            continue
        if run:
            out.append(str(run[0]) if len(run) == 1 else f"{run[0]}–{run[-1]}")
        run = [n] if n is not None else []
    return ", ".join(out)


def odf_fibres(model: str, catalog: Catalog, fspec: object = None) -> int:
    """Fibres of an optical patch panel model: the fibre type's housings, else from its catalog name
    (``24xLC Duplex`` / ``24 порти`` -> 48, ``12 волокон`` / ``12 fibres`` -> 12); 12 when unknown."""
    if fspec is not None and model and model == getattr(fspec, "housing_24", ""):
        return 24
    if fspec is not None and model and model == getattr(fspec, "housing_12", ""):
        return 12
    dev = catalog.models.get(model)
    if dev is not None:
        for text in (dev.spec_name, *dev.name.values()):
            m = re.search(r"(\d+)\s*(?:волок|fibre|fiber)", text, re.I)
            if m:
                return int(m.group(1))
            m = re.search(r"(\d+)\s*(?:[x×]\s*LC[\s-]*Dupl|порт|ports?\b)", text, re.I)
            if m:
                return 2 * int(m.group(1))
    return 12


def _odf_nodes(
    plans: list[RackPlan],
    fspec: object,
    fiber: str,
    ed: TopologyEdits,
    t: Translator,
    rooms: dict[tuple[int, int], int],
    catalog: Catalog,
    cables: dict[str, int],
    names: dict[str, str],
) -> list[TopoNode]:
    """An ODF node for every optical patch panel of the cabinets, joined to the one at the other end of its cable.

    ``odf:<cabinet>:<peer>`` stands in cabinet ``<cabinet>``; the peer is a cabinet of the same floor,
    ``fw`` (the firewall floor's ``floor<N>``), ``floor<N>`` or ``isp`` (the provider's fibre).
    """
    found: dict[tuple[int, str, str], TopoNode] = {}
    tags: dict[tuple[int, str], str] = {}
    out: list[TopoNode] = []
    for plan in plans:
        tags[(plan.floor, plan.key)] = plan.tag
        for it in sorted(plan.items, key=lambda i: -i.u):
            if it.group != "fiber":
                continue
            nid = f"{plan.tag}/{it.id}"
            # the model and the name in the cabinet are the user's (the racks page) unless the topology renamed it
            fibres = odf_fibres(it.model, catalog, fspec)
            ports = min(ODF_MAX_PORTS, max(1, ed.odf_ports.get(nid, fibres // 2)))
            node = TopoNode(
                nid,
                "odf",
                ed.labels.get(nid) or names.get(it.id) or f"ODF {plan.tag}",
                it.model,
                plan.tag,
                u=it.u,
                room=rooms.get((plan.floor, plan.room), 0),
                ports=ports,
                rect=Rect(0, 0, odf_width(ports), ODF_H),
                auto_ports=fibres // 2,
            )
            cable = cables.get(it.id, min(fibres, 12))
            node.cable = ed.labels.get(nid + "#cable", f"{fiber} · {t.t('topo.fibres', n=cable)}".strip(" ·"))
            node.caption = it.label  # hand-added: its own name
            if it.id.startswith("odf:"):
                _o, key, peer = it.id.split(":", 2)
                found[(plan.floor, key, peer)] = node
            out.append(node)
    for (floor, key, peer), node in found.items():
        target: TopoNode | None = None
        if peer == "isp":
            node.peer = "isp"
            node.caption = "→ " + t.t("topo.odf_isp")
            node.cable = ed.labels.get(node.id + "#cable", "")
            continue
        if (floor, peer) in tags:
            target = found.get((floor, peer, key))
        elif peer == "fw":
            target = next((n for (_f, _k, pp), n in found.items() if pp == f"floor{floor}"), None)
        elif peer.startswith("floor") and peer[5:].lstrip("-").isdigit():
            target = next((n for (f, _k, pp), n in found.items() if f == int(peer[5:]) and pp == "fw"), None)
        if target is not None:
            node.peer = target.id
            node.caption = "→ " + target.cabinet
        else:
            node.caption = "→ " + tags.get((floor, peer), peer)
    return out


def _panel_nodes(
    plans: list[RackPlan], switches: dict[str, TopoNode], catalog: Catalog, ed: TopologyEdits
) -> list[TopoNode]:
    """Copper patch panels of the switches (``panel:<switch>:<k>``), with the switch ports they go to."""
    per = catalog.passive.panel_ports
    out: list[TopoNode] = []
    for plan in plans:
        mine: dict[str, list[tuple[int, RackItem]]] = {}
        for it in plan.items:
            if it.group != "panel" or not it.id.startswith("panel:"):
                continue
            bits = it.id.split(":")
            if len(bits) < 3 or not bits[-1].isdigit():
                continue
            mine.setdefault(f"{plan.tag}/{':'.join(bits[1:-1])}", []).append((int(bits[-1]), it))
        for sw_id, items in mine.items():
            sw = switches.get(sw_id)
            if sw is None:
                continue
            dev = catalog.models.get(sw.model)
            copper = dev.ports.count if dev is not None and dev.ports is not None else 48
            for k, it in sorted(items, key=lambda ki: ki[0]):
                nid = f"{plan.tag}/{it.id}"
                lo, hi = (k - 1) * per + 1, min(k * per, copper)
                typed = ed.panel_map.get(nid, "").strip()
                to = port_range(typed) if typed else list(range(lo, hi + 1))
                node = TopoNode(
                    nid,
                    "panel",
                    ed.labels.get(nid, it.label),
                    it.model,
                    plan.tag,
                    u=it.u,
                    room=sw.room,
                    ports=per,
                    switch=sw.id,
                    group=sw.group,
                )
                node.caption = f"1–{min(per, len(to)) or per} → {compact_range(to) or '—'}"
                out.append(node)
    return out


def _odf_route(src: str, dst: str, adj: dict[str, list[tuple[TopoNode, TopoNode]]]) -> list[tuple[TopoNode, TopoNode]]:
    """ODF pairs (near end, far end) a cable path takes from cabinet ``src`` to ``dst`` (shortest)."""
    prev: dict[str, tuple[str, TopoNode, TopoNode]] = {}
    seen, queue = {src}, [src]
    while queue:
        cab = queue.pop(0)
        if cab == dst:
            break
        for near, far in adj.get(cab, []):
            if far.cabinet not in seen:
                seen.add(far.cabinet)
                prev[far.cabinet] = (cab, near, far)
                queue.append(far.cabinet)
    if dst not in prev:
        return []
    path: list[tuple[TopoNode, TopoNode]] = []
    cab = dst
    while cab != src:
        back, near, far = prev[cab]
        path.append((near, far))
        cab = back
    return path[::-1]


def through_odfs(topo: Topology, ed: TopologyEdits, t: Translator) -> None:
    """Which ODF ports every fibre link goes through (``via``) and the ODFs' ``cells``.

    The user's ports (``odf_map``) are taken first; a cable's two ends get the same port number unless
    the user said otherwise; the rest take the first port free at both ends. A link drawn to an ODF ends
    on one of its ports (its ``port_a`` / ``port_b``).
    """
    nodes = {n.id: n for n in topo.nodes}
    odfs = [n for n in topo.nodes if n.kind == "odf"]
    if not odfs:
        return
    adj: dict[str, list[tuple[TopoNode, TopoNode]]] = {}
    isp: dict[str, TopoNode] = {}
    for o in odfs:
        o.cells = {}
        if o.peer == "isp":
            isp.setdefault(o.cabinet, o)
        elif o.peer in nodes:
            adj.setdefault(o.cabinet, []).append((o, nodes[o.peer]))
    routes: dict[tuple[str, str], list[tuple[TopoNode, TopoNode]]] = {}
    plan: list[tuple[TopoLinkResult, list[list[TopoNode]]]] = []
    """Per link: its hops — a cable (two ODFs) or the provider's ODF / an ODF the link ends on (one)."""
    for lk in topo.links:
        a, b = nodes.get(lk.a), nodes.get(lk.b)
        lk.via = []
        if a is None or b is None or lk.kind == "patch":
            continue
        hops: list[list[TopoNode]] = []
        if a.kind == "odf" or b.kind == "odf":
            hops = [[n] for n in (a, b) if n.kind == "odf"]
        elif lk.kind == "wan":
            fw = a if a.kind == "firewall" else b
            if fw.cabinet in isp:
                hops = [[isp[fw.cabinet]]]
        elif lk.medium == "fiber" and a.cabinet and b.cabinet and a.cabinet != b.cabinet:
            key = (a.cabinet, b.cabinet)
            if key not in routes:
                routes[key] = _odf_route(a.cabinet, b.cabinet, adj)
            hops = [[near, far] for near, far in routes[key]]
        if hops:
            plan.append((lk, hops))
    taken: dict[str, dict[int, str]] = {o.id: {} for o in odfs}

    def typed(lk: TopoLinkResult, o: TopoNode) -> int | None:
        if o.id in (lk.a, lk.b):  # the link ends on this ODF: its port field
            text = ed.ports.get(lk.end(o.id), "").strip()
            return int(text) if text.isdigit() and int(text) > 0 else None
        n = ed.odf_map.get(f"{lk.id}@{o.id}")
        return n if n is not None and n > 0 else None

    clash: set[str] = set()
    for lk, hops in plan:  # the user's ports first
        for hop in hops:
            for o in hop:
                n = typed(lk, o)
                if n is None:
                    continue
                if n in taken[o.id]:
                    clash.add(o.id)
                else:
                    taken[o.id][n] = lk.id
    for lk, hops in plan:
        via: list[tuple[str, int]] = []
        for hop in hops:
            mine = {o.id: n for o in hop if (n := typed(lk, o)) is not None and taken[o.id].get(n) == lk.id}
            free = [o for o in hop if o.id not in mine]
            if free:
                want = next(iter(mine.values()), None)
                if want is not None and all(want not in taken[o.id] for o in free):
                    n = want
                else:
                    n = 1
                    while any(n in taken[o.id] for o in free):
                        n += 1
                for o in free:
                    taken[o.id][n] = lk.id
                    mine[o.id] = n
            via += [(o.id, mine[o.id]) for o in hop]
        ends = {oid for oid, _n in via if oid in (lk.a, lk.b)}
        for oid, n in via:
            if oid == lk.a:
                lk.port_a, lk.module_a = str(n), ""
            elif oid == lk.b:
                lk.port_b, lk.module_b = str(n), ""
        lk.via = [(oid, n) for oid, n in via if oid not in ends]
    for o in odfs:
        o.cells = dict(sorted(taken[o.id].items()))
        o.notes = {
            int(k.rsplit("#", 1)[1]): v
            for k, v in ed.port_notes.items()
            if k.rsplit("#", 1)[0] == o.id and k.rsplit("#", 1)[1].isdigit() and v
        }
        o.rect.w = odf_width(o.shown_ports)
        if len(o.cells) > o.ports or max(o.cells, default=0) > o.ports:
            topo.problems.append(t.t("topo.odf_short", name=f"{o.label} {o.caption}", used=len(o.cells), cap=o.ports))
        if o.id in clash:
            topo.problems.append(t.t("topo.odf_clash", name=f"{o.label} {o.caption}"))


def build_topology(
    result: SiteResult, catalog: Catalog, lang: str = "uk", edits: TopologyEdits | None = None
) -> Topology:
    """Nodes, links, columns and routes of the location's network (see the module docstring)."""
    from ..i18n import Translator

    t = Translator(lang)
    ed = edits if edits is not None else result.input.topology
    size = ed.group_size or catalog.rules.topology_group_size
    topo = Topology(access=ed.access, group_size=size, title=result.input.name, lang=lang)
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
    rooms: dict[tuple[int, int], int] = {}
    """(floor, telecom room) -> its number on the drawing (the colour of its links)."""
    for plan in result.rack.plans:
        # the switches of a cabinet form its stack(s) in the order they stand in it, top-down
        room = rooms.setdefault((plan.floor, plan.room), len(rooms))
        topo.room_names.setdefault(room, re.sub(r"\d+$", "", plan.tag) or plan.tag)
        members: list[TopoNode] = []
        for it in _plan_switches(plan):
            node = TopoNode(
                f"{plan.tag}/{it.id}", it.group, it.label or it.model, it.model, plan.tag, u=it.u, room=room
            )
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

    # ---- passive parts: ODFs, copper patch panels -------------------------------------------
    fspec = catalog.passive.fiber.get(result.rack.passive.fiber_type)
    fiber = result.rack.passive.fiber_type.upper() if result.rack.passive.fiber_type else ""
    odfs = (
        _odf_nodes(
            result.rack.plans, fspec, fiber, ed, t, rooms, catalog, result.rack.odf_fibers, result.input.layout.labels
        )
        if ed.show_odf
        else []
    )
    panels = _panel_nodes(result.rack.plans, switches, catalog, ed) if ed.show_panels else []
    for n in panels:
        n.group = switches[n.switch].group
    head_cabs = {n.cabinet for n in (*fws, *cores)}
    first_col: dict[str, str] = {}
    """Cabinet -> its first stack (its ODFs stand above it)."""
    for gid, col in columns.items():
        first_col.setdefault(col_cab.get(gid) or col[0].cabinet, gid)
    isp_odfs = [o for o in odfs if o.peer == "isp"]
    head_odfs = [o for o in odfs if o.peer != "isp" and (o.cabinet in head_cabs or o.cabinet not in first_col)]
    col_odfs: dict[str, list[TopoNode]] = {}
    for o in odfs:
        o.head = o in isp_odfs or o in head_odfs
        if not o.head:
            col_odfs.setdefault(first_col[o.cabinet], []).append(o)
    panels_of: dict[str, list[TopoNode]] = {}
    for n in panels:
        panels_of.setdefault(n.switch, []).append(n)

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
    # a stack is wider with patch panels left of its switches, or with a long row of ODFs above it
    shift = {gid: PANEL_W + PANEL_GAP if any(n.id in panels_of for n in col) else 0.0 for gid, col in col_list}
    col_w = {
        gid: max(
            shift[gid] + NODE_W,
            shift[gid] + sum(o.rect.w for o in col_odfs.get(gid, [])) + ODF_VGAP * (len(col_odfs.get(gid, [])) - 1),
        )
        for gid, _col in col_list
    }
    cols_w = sum(col_w.values()) + sum(gaps)
    pair_w = 2 * head_w + PAIR_GAP
    width = max(cols_w, pair_w, 520.0) + 2 * PAD
    cx = width / 2
    col_x: dict[str, float] = {}
    x = cx - cols_w / 2
    for i, (gid, _col) in enumerate(col_list):
        col_x[gid] = x
        x += col_w[gid] + (gaps[i] if i < len(gaps) else 0)

    def place_row(row: list[TopoNode], y: float, x0: float | None = None) -> None:
        total = sum(n.rect.w for n in row) + (len(row) - 1) * 24
        x = cx - total / 2 if x0 is None else x0
        for n in row:
            n.rect.x, n.rect.y = x, y
            x += n.rect.w + 24

    y = PAD
    if wans:
        total = len(wans) * WAN_W + (len(wans) - 1) * 60
        x = cx - total / 2
        for w in wans:
            w.rect.x, w.rect.y = x, y
            x += WAN_W + 60
        y += WAN_H + (70 if isp_odfs else 150)  # room for the port, the module and the cable towards the provider
    if isp_odfs and fws:
        place_row(isp_odfs, y)
        y += ODF_H + 96

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
    # the band where up-links run sideways towards the cores; the modules written down from the
    # cores' ports stay above its lines
    lanes = sum(1 for _g, col in col_list for _ in range(2 if len(col) > 1 or ed.access == "dual" else 1))
    ups = sum(2 * len(col) if ed.access == "dual" else (2 if len(col) > 1 else 1) for _g, col in col_list)
    """Up-links of the stacks (each its own level in the bands)."""
    module_len = text_w(fspec.transceiver if fspec else "")
    head_row = head_odfs + (isp_odfs if not fws else [])
    if head_row and (fws or cores):
        # the ODFs of the head cabinet in a row under the core, right of the head cabinet's own stacks; the
        # patch cords from the core's ports to them run sideways above the row
        y += max(64.0, 44 + module_len) + TRUNK_STEP * min(ups, 80)
        odf_by_id = {o.id: o for o in odfs}

        def peer_x(o: TopoNode) -> float:
            q = odf_by_id.get(o.peer)  # under the stack its cable goes to: the cables do not cross
            return col_x.get(first_col.get(q.cabinet, ""), 1e9) if q is not None else 1e9

        head_row.sort(key=peer_x)
        right = max((col_x[g] + col_w[g] for g, col in col_list if col and col[0].cabinet in head_cabs), default=None)
        total = sum(o.rect.w for o in head_row) + (len(head_row) - 1) * 24
        x0 = cx - total / 2
        if right is not None and x0 < right + 60:
            x0 = right + 60
        place_row(head_row, y, x0)
        y += ODF_H
        band = 46.0 + TRUNK_STEP * min(ups, 80)
    else:
        band = max(62.0, 54 + module_len) + LANE_STEP * min(lanes, 24)
    top = y + band + GROUP_TITLE_H + GROUP_PAD if (fws or cores) else y + GROUP_TITLE_H
    if framed:
        top += CAB_TITLE_H + CAB_PAD
    odf_block = ODF_H + ODF_GAP if col_odfs else 0.0  # every stack starts at the same height
    for gid, col in col_list:
        x = col_x[gid] + shift[gid]
        ox = x
        for o in col_odfs.get(gid, []):
            o.rect.x, o.rect.y = ox, top
            ox += o.rect.w + ODF_VGAP
        yy = top + odf_block
        for n in col:
            n.rect = Rect(x, yy, NODE_W, NODE_H)
            mine = panels_of.get(n.id, [])
            ph = (NODE_H - 4 * (len(mine) - 1)) / len(mine) if mine else 0.0
            for k, pn in enumerate(mine):
                pn.rect = Rect(x - PANEL_GAP - PANEL_W, yy + k * (ph + 4), PANEL_W, ph)
            yy += NODE_H + STACK_GAP

    # ---- the user's positions --------------------------------------------------------------
    col_passive = {
        gid: [*(o.id for o in col_odfs.get(gid, [])), *(pn.id for n in col for pn in panels_of.get(n.id, []))]
        for gid, col in col_list
    }
    all_nodes = [
        *wans,
        *(isp_odfs if fws else []),
        *fws,
        *cores,
        *head_row,
        *(
            n
            for gid, col in col_list
            for n in (*col_odfs.get(gid, []), *(pn for sw in col for pn in panels_of.get(sw.id, [])), *col)
        ),
    ]
    for n in all_nodes:
        pos = ed.positions.get(n.id)
        if pos is not None:
            n.rect.x, n.rect.y = pos.x, pos.y
            n.manual = True
    topo.nodes = all_nodes
    for gid, col in col_list:
        tag = col_cab.get(gid, "")
        title = col_kind.get(gid, gid) if tag in framed else col_titles.get(gid, gid)
        topo.groups.append(TopoGroup(gid, ed.labels.get(gid, title), [n.id for n in col], passive=col_passive[gid]))
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
            # the HA pair is joined by two links (heartbeat ports ha1 and ha2)
            add("ha", fws[i], fws[i + 1], "HA", speed=1.0)
            a, b = fws[i], fws[i + 1]
            links.append(TopoLinkResult(link_id("ha", a.id, b.id) + HA2, a.id, b.id, "ha", "HA", speed=1.0))
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
    for pn in panels:  # a copper patch panel to its switch (0.25 m cords)
        links.append(TopoLinkResult(f"patch:{pn.id}", pn.id, pn.switch, "patch", speed=1.0))
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
    topo.fiber_module = fspec.transceiver if fspec else ""
    fiber = result.rack.passive.fiber_type.upper()
    topo.fiber = fiber
    for lk in out:
        a, b = nodes_by_id[lk.a], nodes_by_id[lk.b]
        sw = next((n for n in (a, b) if n.kind in SWITCH_KINDS), None)
        lk.room = sw.room if sw is not None else -1
        lk.medium = ed.media.get(lk.id) or auto_medium(lk, a, b)
        typed = ed.labels.get(lk.id) if lk.auto else (lk.label or None)
        lk.typed_label = bool(typed)
        lk.label = typed if typed is not None else cable_label(lk, a, b, fiber, catalog, t)
    topo.links = out
    assign_ports(topo, catalog, ed, t)
    through_odfs(topo, ed, t)

    # ---- ports ---------------------------------------------------------------------------------
    for n in (*cores, *fws):
        dev = catalog.models.get(n.model)
        if dev is None:
            continue
        cap = 0
        skip = {"wan"}
        if dev.firewall is not None:
            cap = len(fw_ports(dev.firewall)) or dev.firewall.ports_10g
            if ha_ports(dev.firewall) == ("ha1", "ha2"):
                skip.add("ha")  # dedicated heartbeat ports
        elif dev.ports is not None:
            cap = dev.ports.count + (dev.uplinks.count if dev.uplinks else 0)
        used = sum((2 if lk.kind == "isl" else 1) for lk in out if n.id in (lk.a, lk.b) and lk.kind not in skip)
        if cap and used > cap:
            topo.problems.append(t.t("topo.ports_short", name=n.label, used=used, cap=cap))

    route_links(topo)
    return topo


def _fit_groups(topo: Topology) -> None:
    by_id = {n.id: n for n in topo.nodes}
    for g in topo.groups:
        rects = [by_id[i].rect for i in g.nodes if i in by_id]
        rects += [by_id[i].rect for i in g.passive if i in by_id and not by_id[i].manual]
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
        # up a lane beside the column, above the module written up the lane; the two lanes of a gap
        # get their labels at different heights
        module = text_w(lk.module_a)
        up = module + 14 + (0.0 if x2 > x1 else 28.0) if module else (18.0 if x2 > x1 else 46.0)
        return (lens[0] + min(up, lens[1] - 10)) / total
    return 0.5


def route_links(topo: Topology) -> None:
    """Orthogonal routes for every link; also sets the drawing's size.

    Up-links of a column leave its top switch from the top; the others leave from the side facing
    their core and run up a lane beside the column, then sideways in a band above the columns.
    Ports along a side are spread so that lines do not sit on top of each other. A link through ODFs
    runs into the port (cell) of every ODF on its way and out of it on the other side; a copper patch
    panel is joined to its switch by a short level line.
    """
    nodes = {n.id: n for n in topo.nodes}
    groups = {g.id: g for g in topo.groups}
    _fit_groups(topo)
    columns_top = min((g.rect.y for g in (*topo.groups, *topo.cabinets)), default=0.0)
    heads = [n for n in topo.nodes if n.kind in ("firewall", "core_switch")]
    heads_bottom = max((n.rect.bottom for n in heads), default=0.0)
    head_row_top = min(
        (n.rect.y for n in topo.nodes if n.kind == "odf" and n.head and n.rect.y > heads_bottom), default=None
    )

    def above(n: TopoNode) -> bool:
        g = groups.get(n.group)
        if g is None:
            return False
        return any(o != n.id and nodes[o].rect.y < n.rect.y for o in g.nodes if o in nodes)

    def cell(o: str, port: int) -> Rect | None:
        n = nodes.get(o)
        return n.cell_rect(port) if n is not None and n.kind == "odf" and port > 0 else None

    def end_cell(lk: TopoLinkResult, nid: str) -> Rect | None:
        """The ODF port a link ends on (a link drawn to an ODF)."""
        port = lk.port_a if nid == lk.a else lk.port_b
        return cell(nid, int(port)) if port.isdigit() else None

    def stops(lk: TopoLinkResult) -> list[Rect]:
        """The ODF ports on the way from ``a`` to ``b`` (an ODF end included)."""
        out = [c for o, port in lk.via if (c := cell(o, port)) is not None]
        a, b = nodes[lk.a], nodes[lk.b]
        if a.kind == "odf" and (c := end_cell(lk, a.id)) is not None:
            out.insert(0, c)
        if b.kind == "odf" and (c := end_cell(lk, b.id)) is not None:
            out.append(c)
        return out

    # which side of which node each link uses
    ends: dict[str, dict[str, list[tuple[float, str]]]] = {}

    def claim(node: TopoNode, side: str, other_x: float, lid: str) -> None:
        ends.setdefault(node.id, {}).setdefault(side, []).append((other_x, lid))

    def dev_side(dev: TopoNode, tx: float, ty: float) -> str:
        """The side a device's line leaves through towards the point (tx, ty)."""
        if dev.kind in SWITCH_KINDS and dev.group in groups and above(dev) and ty < dev.rect.y:
            return "left" if tx < groups[dev.group].rect.cx else "right"
        return "top" if ty < dev.rect.cy else "bottom"

    plan: dict[str, tuple[str, str]] = {}
    via_stops: dict[str, list[Rect]] = {}
    for lk in topo.links:
        a, b = nodes.get(lk.a), nodes.get(lk.b)
        if a is None or b is None:
            continue
        if lk.kind == "patch":
            plan[lk.id] = ("patch", "patch")
            continue
        cells = stops(lk) if (lk.via or "odf" in (a.kind, b.kind)) else []
        if cells:
            via_stops[lk.id] = cells
            sa = sb = "cell"
            if a.kind != "odf":
                sa = dev_side(a, cells[0].cx, cells[0].cy)
                claim(a, sa, cells[0].cx, lk.id)
            if b.kind != "odf":
                sb = dev_side(b, cells[-1].cx, cells[-1].cy)
                claim(b, sb, cells[-1].cx, lk.id)
            plan[lk.id] = (sa, sb)
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
    band_y: dict[str, float] = {}
    k = 0
    low_band = columns_top - 30
    # with a row of ODFs under the core, the links that do not go through them run sideways above it
    high_band = head_row_top - 12 if head_row_top is not None else None
    for lk in topo.links:
        a, b = nodes.get(lk.a), nodes.get(lk.b)
        if a is None or b is None or lk.id not in plan or lk.id in via_stops:
            continue
        if plan[lk.id][0] == "stack" or lk.kind not in ("fortilink", "custom", "stack"):
            continue
        band_y[lk.id] = (high_band if high_band is not None else low_band) - LANE_STEP * k
        k += 1
    floor_y = heads_bottom + 18
    # ODF routes: the cables between cabinets in the band, the cords to the core above the ODF row
    module_room = max((text_w(m) for lk in topo.links for m in (lk.module_a, lk.module_b)), default=0.0)
    trunk_y: dict[str, float] = {}
    head_y: dict[str, float] = {}
    kt = kh = 0
    for lk in topo.links:
        if lk.id not in via_stops:
            continue
        trunk_y[lk.id] = low_band - TRUNK_STEP * kt
        kt += 1
        head_y[lk.id] = heads_bottom + max(40.0, module_room + 30) + TRUNK_STEP * kh
        kh += 1
    cell_rank: dict[tuple[float, float], int] = {}
    cell_cab: dict[tuple[float, float], str] = {}
    for o in topo.nodes:
        if o.kind == "odf":
            for i, n in enumerate(sorted(o.cells)):
                c = o.cell_rect(n)
                cell_rank[(round(c.x, 1), round(c.y, 1))] = i
            for n in range(1, o.shown_ports + 1):
                c = o.cell_rect(n)
                cell_cab[(round(c.x, 1), round(c.y, 1))] = o.cabinet

    def cab_of(c: Rect) -> str:
        return cell_cab.get((round(c.x, 1), round(c.y, 1)), "")

    def leg(
        p: tuple[float, float], up_p: bool, q: tuple[float, float], up_q: bool, hint: float | None
    ) -> list[tuple[float, float]]:
        """Corners from ``p`` (leaving upwards / downwards) to ``q`` (reached moving up / down), and ``q``."""
        (px, py), (qx, qy) = p, q
        if abs(px - qx) < 0.5 and up_p == up_q and ((qy < py) if up_p else (qy > py)):
            return [q]
        if up_p and up_q:
            lo, hi = qy + 6, py - 6
            y = min(max(hint, lo), hi) if hint is not None and lo <= hi else (py + qy) / 2
        elif not up_p and not up_q:
            lo, hi = py + 6, qy - 6
            y = min(max(hint, lo), hi) if hint is not None and lo <= hi else (py + qy) / 2
        elif up_p:  # over the top: e.g. from one ODF to another standing as high
            y = min(py, qy) - 12 if hint is None else min(hint, min(py, qy) - 12)
        else:  # under the bottom: a patch cord between two ODFs of one cabinet
            y = max(py, qy) + 12 if hint is None else max(hint, max(py, qy) + 12)
        return [(px, y), (qx, y), q]

    def cord_y(lk: TopoLinkResult, dev: TopoNode, c: Rect) -> float:
        """Where the cord between a device and an ODF port runs sideways: above the ODF row for the core /
        FortiGate, else just past the ODF (each port of it at its own height)."""
        rank = cell_rank.get((round(c.x, 1), round(c.y, 1)), 0)
        if dev.kind in ("firewall", "core_switch") and c.cy > dev.rect.cy and lk.id in head_y:
            return head_y[lk.id]
        return c.bottom + 8 + 6.0 * rank if c.cy < dev.rect.cy else c.y - 8 - 6.0 * rank

    def route_via(lk: TopoLinkResult, a: TopoNode, b: TopoNode, cells: list[Rect]) -> list[tuple[float, float]]:
        sa, sb = plan[lk.id]
        pts: list[tuple[float, float]] = []
        first = cells[0]
        if sa == "cell":
            up = (len(cells) > 1 and cells[1].cy < first.y + 1) or (len(cells) == 1 and b.rect.cy < first.y)
            cur = (first.cx, first.y if up else first.bottom)
            pts.append((first.cx, first.bottom if up else first.y))
            pts.append(cur)
            todo = cells[1:]
        else:
            cur = port(a, sa, lk.id)
            pts.append(cur)
            up = sa == "top" or (sa in ("left", "right") and first.cy < a.rect.y)
            if sa in ("left", "right"):
                lx = lane_x.get(lk.id, cur[0])
                cur = (lx, cur[1])
                pts.append(cur)
            todo = cells
        prev: Rect | None = first if sa == "cell" else None
        for i, c in enumerate(todo):
            enter_low = cur[1] >= c.bottom - 1
            entry = (c.cx, c.bottom if enter_low else c.y)
            hint: float | None
            if prev is None:
                hint = cord_y(lk, a, c)
            elif cab_of(prev) == cab_of(c):  # patched between two ODFs of one cabinet: under them
                hint = max(prev.bottom, c.bottom) + 8 + 5.0 * cell_rank.get((round(c.x, 1), round(c.y, 1)), 0)
            else:
                hint = trunk_y.get(lk.id)
            pts += leg(cur, up, entry, enter_low, hint)
            nxt = todo[i + 1] if i + 1 < len(todo) else None
            ny = nxt.cy if nxt is not None else (b.rect.cy if sb != "cell" else c.cy)
            # to another cabinet's ODF standing as high: over the top, through the band above the stacks
            up = ny < c.y - 1 or (nxt is not None and cab_of(nxt) != cab_of(c) and ny <= c.bottom + 1)
            cur = (c.cx, c.y if up else c.bottom)
            pts.append(cur)
            prev = c
        if sb == "cell":
            return pts
        q = port(b, sb, lk.id)
        hint = cord_y(lk, b, cells[-1])
        if sb in ("left", "right"):
            lx = lane_x.get(lk.id, q[0])
            pts += leg(cur, up, (lx, q[1]), not up, hint)[:-1]
            pts += [(lx, q[1]), q]
        else:
            pts += leg(cur, up, q, sb == "bottom", hint)
        return pts

    for lk in topo.links:
        a, b = nodes.get(lk.a), nodes.get(lk.b)
        if a is None or b is None or lk.id not in plan:
            lk.points = []
            continue
        sa, sb = plan[lk.id]
        if sa == "patch":
            y = a.rect.cy
            lk.points = (
                [(a.rect.right, y), (b.rect.x, y)] if a.rect.right <= b.rect.x else [(a.rect.x, y), (b.rect.right, y)]
            )
            continue
        if lk.id in via_stops:
            lk.points = _dedupe(route_via(lk, a, b, via_stops[lk.id]))
            continue
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
        lk.label_t = _via_label_t(lk) if lk.id in via_stops else _label_t(lk)

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


def _dedupe(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop repeated points and the middle of three on one straight line."""
    out: list[tuple[float, float]] = []
    for pt in points:
        if out and abs(out[-1][0] - pt[0]) < 0.01 and abs(out[-1][1] - pt[1]) < 0.01:
            continue
        if len(out) >= 2:
            (x0, y0), (x1, y1) = out[-2], out[-1]
            if (abs(x0 - x1) < 0.01 and abs(x1 - pt[0]) < 0.01 and (y1 - y0) * (pt[1] - y1) > 0) or (
                abs(y0 - y1) < 0.01 and abs(y1 - pt[1]) < 0.01 and (x1 - x0) * (pt[0] - x1) > 0
            ):
                out[-1] = pt
                continue
        out.append(pt)
    return out


def _via_label_t(lk: TopoLinkResult) -> float:
    """A typed cable label of a link through ODFs: on its first cord (from ``a`` to the first ODF)."""
    if len(lk.points) < 2:
        return 0.5
    lens = [math.hypot(x2 - x1, y2 - y1) for (x1, y1), (x2, y2) in itertools.pairwise(lk.points)]
    total = sum(lens) or 1.0
    return min(0.5, (lens[0] + (lens[1] / 2 if len(lens) > 1 else 0)) / total)
