"""Upgrade projects saved by older versions.

Version 1 → 2: switches are numbered per telecom room (``access_switch:r2.3`` instead of one number
running over all rooms) and cabinets are tagged by room (``7A0``, ``7A1``, ``7B0`` instead of
``7A``, ``7B``, ``7C``). The manual layout (positions, deleted items, names, models) and the topology
edits (positions, links, labels, media, ports…) refer to those ids, so they are rewritten — what the
user placed, named or drew stays where it was.
"""

from __future__ import annotations

import re
from typing import Any

from .catalog import Catalog
from .models import SiteInput
from .passive import EDGE_KEYS, RACK_ORDER, _letter, switch_id
from .project import Project

PROJECT_V2 = 2

_ITEM_RE = re.compile(rf"(?P<key>{'|'.join(EDGE_KEYS)}):(?P<n>\d+)(?=$|[:|@/])")


def _switch_map(site: SiteInput, catalog: Catalog) -> dict[str, str]:
    """Old switch id (``access_switch:5``) -> new one (``access_switch:r1.2``)."""
    from .sizing import size_site

    bare = site.model_copy(update={"layout": type(site.layout)(), "topology": type(site.topology)()})
    r = size_site(bare, catalog)
    out: dict[str, str] = {}
    n_closets = max(1, r.rack.idf_count)
    for key in RACK_ORDER:
        res = r.categories.get(key)
        if res is None or not res.count:
            continue
        if res.per_room is not None:
            old = 0
            for room, count in enumerate(res.per_room[:n_closets]):
                for n in range(1, count + 1):
                    old += 1
                    out[f"{key}:{old}"] = switch_id(key, room, n)
            continue
        seen = [0] * n_closets
        for i in range(res.count):
            room = i % n_closets  # the old code gave each switch to the least loaded closet in turn
            seen[room] += 1
            out[f"{key}:{i + 1}"] = switch_id(key, room, seen[room])
    return {k: v for k, v in out.items() if k != v}


def _tag_map(site: SiteInput, catalog: Catalog) -> dict[str, str]:
    """Old cabinet tag (``7C``) -> new one (``7B0``), for every cabinet."""
    from .sizing import size_site

    auto = size_site(site.model_copy(update={"layout": type(site.layout)()}), catalog)
    keys = [p.key for p in auto.rack.plans]  # creation order: the old letters A, B, C…
    old_letter = {key: _letter(i) for i, key in enumerate(keys)}
    used = set(old_letter.values())
    n = 0
    for key in site.layout.added:
        while _letter(n) in used:
            n += 1
        old_letter[key] = _letter(n)
        used.add(old_letter[key])
    now = size_site(site, catalog)
    out: dict[str, str] = {}
    for plan in now.rack.plans:
        props = site.layout.props.get(plan.key)
        letter = (props.letter.strip()[:3] if props and props.letter else "") or old_letter.get(plan.key, "")
        if letter:
            out[f"{plan.floor}{letter}"] = plan.tag  # unchanged ones too: their switch ids may change
    return out


def _item(text: str, switches: dict[str, str]) -> str:
    return _ITEM_RE.sub(lambda m: switches.get(m.group(0), m.group(0)), text)


_NODE_ID_RE = re.compile(r"(?P<tag>-?\d+[A-Z]{1,3}\d{0,2})/(?P<item>[^|@]+)")
_GROUP_RE = re.compile(r"(cab:)?(-?\d+[A-Z]{1,3}\d{0,2})(:sw(?::\d+)?)?")


def _rewrite(value: Any, cabinets: dict[str, tuple[str, dict[str, str]]]) -> Any:
    """Every topology id inside ``value`` (dict keys too): ``cabinets`` maps an old cabinet tag to its
    new tag and the switch ids of its floor."""
    if isinstance(value, dict):
        return {_rewrite(k, cabinets): _rewrite(v, cabinets) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite(v, cabinets) for v in value]
    if not isinstance(value, str):
        return value

    def node(m: re.Match[str]) -> str:
        tag, item = m.group("tag"), m.group("item")
        if tag not in cabinets:
            return m.group(0)
        new_tag, switches = cabinets[tag]
        return f"{new_tag}/{switches.get(item, _item(item, switches))}"

    text = _NODE_ID_RE.sub(node, value)
    g = _GROUP_RE.fullmatch(text)
    if g and (g.group(1) or g.group(3)) and g.group(2) in cabinets:
        text = f"{g.group(1) or ''}{cabinets[g.group(2)][0]}{g.group(3) or ''}"
    return text


def upgrade_project(project: Project, catalog: Catalog) -> bool:
    """Bring a project of an older version up to date (in place); True when anything changed."""
    if project.version >= PROJECT_V2:
        return False
    cabinets: dict[str, tuple[str, dict[str, str]]] = {}
    per_site: list[dict[str, str]] = []
    for entry in project.sites:
        switches = _switch_map(entry.input, catalog)
        per_site.append(switches)
        for old, new in _tag_map(entry.input, catalog).items():
            cabinets[old] = (new, switches)
    changed = False
    topology: Any = None
    for entry, switches in zip(project.sites, per_site, strict=True):
        data = entry.input.model_dump(mode="json")
        layout = data.get("layout") or {}
        for field in ("positions", "labels", "models"):
            layout[field] = {_item(k, switches): v for k, v in (layout.get(field) or {}).items()}
        layout["hidden"] = [_item(i, switches) for i in layout.get("hidden") or []]
        if topology is None:  # shared by every floor: rewritten once, with each cabinet's own floor
            topology = _rewrite(data.get("topology") or {}, cabinets)
        data["topology"] = topology
        upgraded = SiteInput.model_validate(data)
        if upgraded != entry.input:
            entry.input = upgraded
            changed = True
    project.version = PROJECT_V2
    return changed
