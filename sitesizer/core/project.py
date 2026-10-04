"""Projects: one or several locations (e.g. HQ + remote sites) saved as a single JSON file."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import SiteInput

PROJECT_FORMAT = "sitesizer-project"
PROJECT_VERSION = 1
PROJECT_SUFFIX = ".sizing.json"


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class SiteEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=_new_id)
    input: SiteInput = Field(default_factory=SiteInput)
    is_hub: bool = False
    """Hub (HQ) of the hub-and-spoke WAN diagram."""


class Project(BaseModel):
    model_config = ConfigDict(extra="ignore")

    format: str = PROJECT_FORMAT
    version: int = PROJECT_VERSION
    name: str = "Новий проєкт"
    customer: str = ""
    author: str = ""
    created: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    modified: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    sites: list[SiteEntry] = Field(default_factory=list)
    notes: str = ""

    # ---- operations ---------------------------------------------------------------------
    def site(self, site_id: str) -> SiteEntry | None:
        return next((s for s in self.sites if s.id == site_id), None)

    def add_site(self, site: SiteInput | None = None, is_hub: bool = False) -> SiteEntry:
        entry = SiteEntry(input=site or SiteInput(name=f"Локація {len(self.sites) + 1}"), is_hub=is_hub)
        if not self.sites:
            entry.is_hub = True
        elif is_hub:
            self.set_hub("")
        self.sites.append(entry)
        return entry

    def duplicate_site(self, site_id: str, suffix: str = " (копія)") -> SiteEntry | None:
        src = self.site(site_id)
        if src is None:
            return None
        copy_input = SiteInput.model_validate(src.input.model_dump())
        copy_input.name = f"{src.input.name}{suffix}"
        entry = SiteEntry(input=copy_input, is_hub=False)
        idx = self.sites.index(src)
        self.sites.insert(idx + 1, entry)
        return entry

    def remove_site(self, site_id: str) -> bool:
        src = self.site(site_id)
        if src is None:
            return False
        self.sites.remove(src)
        if src.is_hub and self.sites:
            self.sites[0].is_hub = True
        return True

    def set_hub(self, site_id: str) -> None:
        for s in self.sites:
            s.is_hub = s.id == site_id

    @property
    def hub(self) -> SiteEntry | None:
        return next((s for s in self.sites if s.is_hub), self.sites[0] if self.sites else None)


class ProjectError(Exception):
    pass


def new_project(name: str = "Новий проєкт", author: str = "") -> Project:
    project = Project(name=name, author=author)
    project.add_site(SiteInput(name="Локація 1"), is_hub=True)
    return project


def save_project(project: Project, path: str | Path) -> Path:
    p = Path(path)
    project.modified = datetime.now().isoformat(timespec="seconds")
    data = project.model_dump(mode="json")
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)  # atomic on the same volume
    return p


def load_project(path: str | Path) -> Project:
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise ProjectError(f"Файл не знайдено: {p}") from err
    except json.JSONDecodeError as err:
        raise ProjectError(f"Файл пошкоджено (JSON, рядок {err.lineno}): {p.name}") from err
    if isinstance(raw, dict) and "sites" not in raw and ("sockets" in raw or "ap_groups" in raw):
        # A bare SiteInput JSON (e.g. CLI --json input) — wrap it.
        raw = {"name": raw.get("name", p.stem), "sites": [{"input": raw, "is_hub": True}]}
    if not isinstance(raw, dict) or raw.get("format", PROJECT_FORMAT) != PROJECT_FORMAT:
        raise ProjectError(f"Це не файл проєкту: {p.name}")
    if int(raw.get("version", 1)) > PROJECT_VERSION:
        raise ProjectError("Файл створено новішою версією програми.")
    try:
        return Project.model_validate(raw)
    except ValidationError as err:
        first = err.errors()[0]
        loc = ".".join(str(x) for x in first.get("loc", ()))
        raise ProjectError(f"Некоректні дані у проєкті ({loc}): {first.get('msg')}") from err
