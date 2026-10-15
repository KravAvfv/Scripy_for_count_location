"""Telecom rooms: cabinet numbering (A0, A1, B0…), cabinets per room, ids that never jump between rooms."""

from __future__ import annotations

from pathlib import Path

from sitesizer.core.catalog import Catalog
from sitesizer.core.passive import drop_room
from sitesizer.core.sizing import size_site
from sitesizer.core.topology import build_topology
from tests.conftest import make_site

ROOT = Path(__file__).resolve().parent.parent


def tags(r) -> list[tuple[int, str]]:
    return [(p.room, p.tag) for p in r.rack.plans]


def test_cabinets_numbered_per_room(catalog: Catalog) -> None:
    # room A: cabinets A0, A1…; room B: B0, B1…
    r = size_site(
        make_site(rooms=[{"sockets": 48 * 8}, {"sockets": 96}], rack_size_u=24, location_code="BO1", floor=3),
        catalog,
    )
    by_room: dict[int, list[str]] = {}
    for room, tag in tags(r):
        by_room.setdefault(room, []).append(tag)
    assert by_room[0][:2] == ["3A0", "3A1"] and by_room[1][0] == "3B0"
    names = [it.label for p in r.rack.plans if p.room == 1 for it in p.items if it.group == "access_switch"]
    assert names and all(n.startswith("BO1-3B0-ASW") for n in names)


def test_cabinets_typed_per_room(catalog: Catalog) -> None:
    site = {"rooms": [{"sockets": 96, "cabinets": 3}, {"sockets": 48}], "location_code": "BO1"}
    r = size_site(make_site(**site), catalog)
    assert [t for room, t in tags(r) if room == 0] == ["1A0", "1A1", "1A2"]
    assert [t for room, t in tags(r) if room == 1] == ["1B0"]
    # every switch is in some cabinet of its room, none lost
    assert sum(1 for p in r.rack.plans if p.room == 0 for it in p.items if it.group == "access_switch") == 2
    # fewer cabinets than the equipment needs: as many as needed, with a finding
    big = size_site(make_site(rooms=[{"sockets": 48 * 12, "cabinets": 1}], rack_size_u=24), catalog)
    assert len(big.rack.plans) > 1 and any("1" in p for p in big.rack.layout_problems)


def test_switch_ids_do_not_jump_between_rooms(catalog: Catalog) -> None:
    """A switch of room B dragged to a unit keeps its place when room A gets more switches."""
    base = make_site(rooms=[{"sockets": 48}, {"sockets": 96}])
    r = size_site(base, catalog)
    b_sw = [it for p in r.rack.plans if p.room == 1 for it in p.items if it.group == "access_switch"]
    assert [it.id for it in b_sw] == ["access_switch:r1.1", "access_switch:r1.2"]
    key = next(p.key for p in r.rack.plans if p.room == 1)
    layout = {"positions": {"access_switch:r1.2": {"rack": key, "u": 5}}}
    more = size_site(make_site(rooms=[{"sockets": 48 * 3}, {"sockets": 96}], layout=layout), catalog)
    moved = next(it for p in more.rack.plans for it in p.items if it.id == "access_switch:r1.2")
    plan = next(p for p in more.rack.plans if any(it is moved for it in p.items))
    assert plan.room == 1 and moved.u == 5
    # nothing of room A ended up in room B
    assert all(":r1." in it.id for p in more.rack.plans if p.room == 1 for it in p.items if it.group.endswith("switch"))


def test_device_of_a_vanished_cabinet_stays_in_its_room(catalog: Catalog) -> None:
    site = make_site(rooms=[{"sockets": 96}, {"sockets": 48 * 6}], rack_size_u=24)
    r = size_site(site, catalog)
    second = [p for p in r.rack.plans if p.room == 1][-1]
    assert second.key.startswith("idf1-") and second.key != "idf1-1"
    extras = [{"id": "nvr", "kind": "device", "model": "FS-148F", "rack": second.key, "u": 3}]
    # room B shrinks to one cabinet: the device goes to B0, not to the main room
    fewer = size_site(make_site(rooms=[{"sockets": 96}, {"sockets": 48}], layout={"extras": extras}), catalog)
    home = next(p for p in fewer.rack.plans for it in p.items if it.id == "nvr")
    assert home.room == 1


def test_drop_room_renumbers_switches_and_devices() -> None:
    layout = {
        "positions": {
            "access_switch:r2.1": {"rack": "idf2-1", "u": 4},
            "access_switch:r1.1": {"rack": "idf1-1", "u": 4},
        },
        "labels": {"dev:2:abc:1": "NVR", "panel:access_switch:r2.1:1": "PP", "dev:1:x:1": "gone"},
        "models": {"access_switch:r2.1": "FS-348G"},
    }
    drop_room(layout, 1, {"idf1-1"})
    assert layout["positions"] == {"access_switch:r1.1": {"rack": "idf1-1", "u": 4}}
    assert layout["labels"] == {"dev:1:abc:1": "NVR", "panel:access_switch:r1.1:1": "PP"}
    assert layout["models"] == {"access_switch:r1.1": "FS-348G"}


def test_hand_added_devices_named_after_their_cabinet(catalog: Catalog) -> None:
    """Racks page and location page: a switch / FortiGate put in by hand gets the cabinet's floor and letter."""
    rooms = [{"sockets": 48, "devices": [{"model": "FS-148F", "qty": 1, "label": "мій", "id": "d1"}]}]
    layout = {
        "props": {"mdf-1": {"floor": 7, "letter": "C2"}},
        "extras": [
            {"id": "x1", "kind": "device", "model": "FS-148F-FPOE", "rack": "mdf-1", "u": 2, "role": "camera_switch"}
        ],
    }
    r = size_site(make_site(rooms=rooms, location_code="BO9", layout=layout), catalog)
    items = {it.id: it for p in r.rack.plans for it in p.items}
    assert items["x1"].label == "BO9-7C2-VSW01"
    assert items["dev:0:d1:1"].label.startswith("BO9-7C2-ASW")
    assert r.rack.plans[0].tag == "7C2"


def test_links_coloured_per_room(catalog: Catalog) -> None:
    from sitesizer.exporters.topology import LIGHT

    r = size_site(make_site(rooms=[{"sockets": 96}, {"sockets": 96}, {"sockets": 48}], core_count=2), catalog)
    topo = build_topology(r, catalog)
    rooms = {lk.room for lk in topo.links if lk.kind in ("fortilink", "stack")}
    assert rooms == {0, 1, 2}
    colours = {LIGHT.link("fortilink", room) for room in rooms}
    assert len(colours) == 3
    assert all(lk.room == -1 for lk in topo.links if lk.kind in ("isl", "uplink", "ha"))


def test_build_scripts_are_ascii() -> None:
    # Windows PowerShell 5.1 reads a UTF-8 file without BOM as ANSI: an em dash breaks the script
    for name in ("build.ps1", "build.sh"):
        text = (ROOT / name).read_bytes()
        assert all(b < 128 for b in text), name


def test_old_projects_are_upgraded(catalog: Catalog) -> None:
    """A version-1 file: the switch of room B was ``access_switch:3`` in cabinet ``1B`` (now r1.1 in 1B0)."""
    from sitesizer.core.migrate import upgrade_project
    from sitesizer.core.project import Project

    site = make_site(
        name="1",
        rooms=[{"sockets": 96}, {"sockets": 48}],
        layout={
            "positions": {"access_switch:3": {"rack": "idf1-1", "u": 7}},
            "labels": {"access_switch:3": "Хол", "panel:access_switch:3:1": "ПП хол"},
            "hidden": ["access_switch:2"],
        },
        topology={
            "positions": {"1B/access_switch:3": {"x": 10, "y": 20}, "1A/firewall:1": {"x": 1, "y": 2}},
            "labels": {"1B:sw": "Стек холу", "fortilink:1B/access_switch:3|1A/firewall:1": "мідь"},
            "media": {"fortilink:1B/access_switch:3|1A/firewall:1": "copper"},
        },
    )
    project = Project.model_validate({"version": 1, "sites": [{"input": site.model_dump(mode="json"), "is_hub": True}]})
    assert upgrade_project(project, catalog)
    assert project.version == 2
    new = project.sites[0].input
    assert new.layout.positions["access_switch:r1.1"].u == 7
    assert new.layout.labels == {"access_switch:r1.1": "Хол", "panel:access_switch:r1.1:1": "ПП хол"}
    assert new.layout.hidden == ["access_switch:2"]  # room A keeps its numbers
    topo = new.topology
    assert set(topo.positions) == {"1B0/access_switch:r1.1", "1A0/firewall:1"}
    assert topo.labels["1B0:sw"] == "Стек холу"
    assert topo.media == {"fortilink:1B0/access_switch:r1.1|1A0/firewall:1": "copper"}
    # the upgraded project works: the switch stands where it was put and keeps its name
    r = size_site(new, catalog)
    sw = next(it for p in r.rack.plans if p.room == 1 for it in p.items if it.id == "access_switch:r1.1")
    assert sw.label == "Хол" and sw.u == 7
    # a second upgrade does nothing
    assert not upgrade_project(project, catalog)


def test_old_cabinet_tags_map_to_rooms(catalog: Catalog) -> None:
    from sitesizer.core.migrate import _tag_map

    site = make_site(rooms=[{"sockets": 48 * 8}, {"sockets": 48}], rack_size_u=24)
    r = size_site(site, catalog)
    n0 = sum(1 for p in r.rack.plans if p.room == 0)
    old = "ABCDEFG"
    want = {f"1{old[i]}": f"1A{i}" for i in range(n0)} | {f"1{old[n0]}": "1B0"}
    assert _tag_map(site, catalog) == want
