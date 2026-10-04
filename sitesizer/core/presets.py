"""One-click location templates (``data/presets.json``)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .models import SiteInput

PRESETS_PATH = Path(__file__).resolve().parent.parent / "data" / "presets.json"


@dataclass(frozen=True)
class Preset:
    id: str
    label: dict[str, str]
    description: dict[str, str]
    icon: str
    input: dict[str, Any] = field(default_factory=dict)

    def build(self, name: str | None = None, keep: SiteInput | None = None) -> SiteInput:
        """New ``SiteInput`` from the preset; keeps the current name / IP base if given."""
        data = dict(self.input)
        if keep is not None:
            data.setdefault("name", keep.name)
        if name:
            data["name"] = name
        return SiteInput.model_validate(data)


@lru_cache(maxsize=1)
def load_presets(path: str | None = None) -> tuple[Preset, ...]:
    raw = json.loads(Path(path or PRESETS_PATH).read_text(encoding="utf-8"))
    out = []
    for item in raw.get("presets", []):
        SiteInput.model_validate(item.get("input", {}))  # fail early on bad data
        out.append(
            Preset(
                id=item["id"],
                label=item["label"],
                description=item.get("description", {}),
                icon=item.get("icon", ""),
                input=item.get("input", {}),
            )
        )
    return tuple(out)


def preset(preset_id: str) -> Preset | None:
    return next((p for p in load_presets() if p.id == preset_id), None)
