"""Which switch port every access point goes to — the wiring scheme for the installers.

Neighbouring access points must not hang on the same switch: when a switch fails, its APs go dark,
and the APs around them (on other switches) still cover the area. APs are numbered the way they
stand on the floor plan, row by row (``SiteInput.ap_columns`` per row; 0 = one line), and the
switches of the telecom room that serves them take turns::

    one line, 2 switches:   1 2 1 2 1 2 …
    rows of 4, 2 switches:  1 2 1 2        (a checkerboard: left / right / up / down neighbours
                            2 1 2 1         are always on another switch)

``switch = (column + row) mod n`` — neighbours in a row differ by one column, neighbours in a
column by one row, so they never share a switch when there are at least two. Ports are taken in
order on each switch; a full switch passes the AP on to the next one with a free port (reported).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import Catalog
from .models import ApGroup, SiteResult


@dataclass
class WiringSwitch:
    label: str
    model: str
    cabinet: str
    ports: int
    used: list[tuple[int, str]] = field(default_factory=list)
    """(port, AP name) in port order."""


@dataclass
class WiringAp:
    name: str
    zone: str
    row: int
    col: int
    switch: int = -1
    """Index into :attr:`WiringRoom.switches` (-1 = no free port anywhere)."""
    port: int = 0


@dataclass
class WiringRoom:
    title: str
    floor: int
    columns: int
    switches: list[WiringSwitch] = field(default_factory=list)
    aps: list[WiringAp] = field(default_factory=list)
    shared_pairs: int = 0
    """Neighbouring APs that ended up on the same switch."""


@dataclass
class ApWiring:
    rooms: list[WiringRoom] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any(r.aps for r in self.rooms)

    def rows(self) -> list[tuple[str, str, str, str, str, int]]:
        """(room, AP, zone, switch, cabinet, port) for tables."""
        out = []
        for room in self.rooms:
            for ap in room.aps:
                sw = room.switches[ap.switch] if ap.switch >= 0 else None
                out.append((room.title, ap.name, ap.zone, sw.label if sw else "—", sw.cabinet if sw else "", ap.port))
        return out


def switch_ports(model: str, catalog: Catalog) -> int:
    dev = catalog.models.get(model)
    if dev is None:
        return 24
    if dev.poe and dev.poe.ports_total:
        return dev.poe.ports_total
    return dev.ports.count if dev.ports else 24


def assign(count: int, switches: int, columns: int) -> list[tuple[int, int, int]]:
    """(row, column, preferred switch) for every AP — the alternating pattern of the module docstring."""
    out = []
    for i in range(count):
        row, col = divmod(i, columns) if columns > 0 else (0, i)
        out.append((row, col, (col + row) % switches if switches else -1))
    return out


def _zone_title(group: ApGroup, catalog: Catalog, lang: str) -> str:
    if group.name:
        return group.name
    zone = catalog.ap_zones.get(group.zone)
    if zone is None:
        return group.zone
    return zone.label.get(lang) or next(iter(zone.label.values()), group.zone)


def plan_ap_wiring(
    floors: list[tuple[str, SiteResult]], catalog: Catalog, lang: str = "uk", columns: int | None = None
) -> ApWiring:
    """The wiring of every floor and telecom room of a location (``floors`` = (name, result))."""
    from ..i18n import Translator

    t = Translator(lang)
    out = ApWiring()
    many_floors = len(floors) > 1
    for floor_name, r in floors:
        site = r.input
        cols = site.ap_columns if columns is None else columns
        code = site.location_code.strip()
        prefix = f"{code}-{site.floor}-" if code else (f"{site.floor}-" if many_floors else "")
        names = list(r.rack.rooms)
        # the APs actually installed (without the growth reserve), by the room that serves them
        n_rooms = max(1, len(names))
        room_groups: list[list[ApGroup]] = [[] for _ in range(n_rooms)]
        for g in site.ap_groups:
            if g.qty > 0:
                room_groups[min(g.room, n_rooms - 1) if site.per_room else 0].append(g)
        number = 0
        for i, groups in enumerate(room_groups):
            total = sum(g.qty for g in groups)
            if not total:
                continue
            room_name = names[i] if i < len(names) else str(i + 1)
            title = f"{floor_name} · {room_name}" if many_floors else room_name
            room = WiringRoom(title=title, floor=site.floor, columns=cols)
            plans = [p for p in r.rack.plans if p.room == i] or list(r.rack.plans)
            for p in plans:
                for it in sorted(p.items, key=lambda x: -x.u):
                    if it.group == "wifi_switch":
                        room.switches.append(
                            WiringSwitch(it.label or it.model, it.model, p.tag, switch_ports(it.model, catalog))
                        )
            for g in groups:
                zone = _zone_title(g, catalog, lang)
                for _ in range(g.qty):
                    number += 1
                    room.aps.append(WiringAp(f"{prefix}AP{number:02d}", zone, 0, 0))
            n = len(room.switches)
            for ap, (row, col, pref) in zip(room.aps, assign(len(room.aps), n, cols), strict=True):
                ap.row, ap.col = row, col
                for k in range(n):
                    sw = room.switches[(pref + k) % n]
                    if len(sw.used) < sw.ports:
                        ap.switch = (pref + k) % n
                        ap.port = len(sw.used) + 1
                        sw.used.append((ap.port, ap.name))
                        break
            by_pos = {(ap.row, ap.col): ap for ap in room.aps}
            for ap in room.aps:
                for nb in (by_pos.get((ap.row, ap.col + 1)), by_pos.get((ap.row + 1, ap.col))):
                    if nb is not None and ap.switch >= 0 and nb.switch == ap.switch:
                        room.shared_pairs += 1
            if n == 0:
                out.problems.append(t.t("apw.no_switch", room=title, n=len(room.aps)))
            elif n == 1 and len(room.aps) > 1:
                out.problems.append(t.t("apw.one_switch", room=title))
            elif room.shared_pairs:
                out.problems.append(t.t("apw.shared", room=title, n=room.shared_pairs))
            lost = sum(1 for ap in room.aps if ap.switch < 0)
            if lost and n:
                out.problems.append(t.t("apw.no_ports", room=title, n=lost))
            out.rooms.append(room)
    return out
