"""Application state: project, current site, live result, catalog, settings and undo/redo.

The GUI never mutates a :class:`SiteInput` directly: views call :meth:`AppState.edit`, which
records an undoable command, then the result is recomputed (debounced) and broadcast through
:attr:`AppState.resultChanged`. Views are therefore simple "render the state" components.
"""

from __future__ import annotations

import copy
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QSettings, QStandardPaths, QTimer, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from ..core.catalog import (
    Catalog,
    CatalogError,
    load_catalog,
    load_default_catalog,
    save_catalog,
    with_missing_defaults,
)
from ..core.models import SiteInput, SiteResult
from ..core.project import Project, ProjectError, load_project, new_project, save_project
from ..core.sizing import size_site
from ..i18n import set_language

log = logging.getLogger(__name__)

RECOMPUTE_DELAY_MS = 60
MERGE_WINDOW_S = 1.5
MAX_RECENT = 8


@dataclass
class AppSettings:
    theme: str = "system"
    language: str = "uk"
    ui_scale: float = 1.0
    author: str = ""
    company: str = ""
    logo_path: str = ""
    currency: str = "UAH"
    vat_pct: float = 20.0
    show_vat: bool = True
    discount_pct: float = 0.0
    export_dir: str = ""
    fortios_default: str = "7.6.4"
    onboarding_done: bool = False
    recent_projects: list[str] = field(default_factory=list)
    last_project: str = ""
    sidebar_collapsed: bool = False
    export_choices: dict[str, Any] = field(default_factory=dict)
    """Last choices of the export dialog (sheets, PDF sections, pictures)."""

    @classmethod
    def load(cls, qs: QSettings) -> AppSettings:
        data = qs.value("app/settings_json", "", type=str)
        out = cls()
        if data:
            try:
                raw = json.loads(data)
                for f in fields(cls):
                    if f.name in raw:
                        setattr(out, f.name, raw[f.name])
            except (json.JSONDecodeError, TypeError):
                log.warning("Settings were corrupted; using defaults")
        return out

    def save(self, qs: QSettings) -> None:
        qs.setValue("app/settings_json", json.dumps(asdict(self), ensure_ascii=False))

    def push_recent(self, path: str) -> None:
        items = [p for p in self.recent_projects if p != path]
        items.insert(0, path)
        self.recent_projects = items[:MAX_RECENT]


def app_data_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    path = Path(base or Path.home() / ".sitesizer")
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_catalog_path() -> Path:
    return app_data_dir() / "catalog.json"


class SiteEditCommand(QUndoCommand):
    def __init__(
        self,
        state: AppState,
        site_id: str,
        before: dict[str, Any],
        after: dict[str, Any],
        text: str,
        merge_key: str | None,
    ) -> None:
        super().__init__(text)
        self.state = state
        self.site_id = site_id
        self.before = before
        self.after = after
        self.merge_key = merge_key
        self.stamp = time.monotonic()

    def id(self) -> int:
        return (hash(self.merge_key) & 0x7FFFFFFF) if self.merge_key else -1

    def mergeWith(self, other: QUndoCommand) -> bool:
        if not isinstance(other, SiteEditCommand):
            return False
        if other.merge_key != self.merge_key or other.site_id != self.site_id:
            return False
        if other.stamp - self.stamp > MERGE_WINDOW_S:
            return False
        self.after = other.after
        self.stamp = other.stamp
        return True

    def redo(self) -> None:
        self.state._apply_site_data(self.site_id, self.after)

    def undo(self) -> None:
        self.state._apply_site_data(self.site_id, self.before)


class AppState(QObject):
    siteChanged = Signal()
    resultChanged = Signal(object)
    projectChanged = Signal()
    catalogChanged = Signal()
    settingsChanged = Signal()
    dirtyChanged = Signal(bool)
    message = Signal(str, str)  # (severity, text) → toast

    def __init__(self, qsettings: QSettings | None = None) -> None:
        super().__init__()
        self.qsettings = qsettings or QSettings()
        self.settings = AppSettings.load(self.qsettings)
        set_language(self.settings.language)
        self._dirty_struct = False
        self.undo = QUndoStack(self)
        self.undo.setUndoLimit(200)
        self.undo.cleanChanged.connect(self._on_clean_changed)
        self.catalog_error = ""
        self.catalog = self._load_catalog()
        self.project: Project = new_project(author=self.settings.author)
        self._apply_new_site_defaults(self.project.sites[0].input)
        self.current_id = self.project.sites[0].id
        self.project_path: Path | None = None
        self._dirty_struct = False
        self.result: SiteResult | None = None
        self._result_cache: dict[str, tuple[str, SiteResult]] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RECOMPUTE_DELAY_MS)
        self._timer.timeout.connect(self.recompute)

    def _on_clean_changed(self, clean: bool) -> None:
        self.dirtyChanged.emit(not clean or self._dirty_struct)

    # ---- catalog --------------------------------------------------------------------------
    def _load_catalog(self) -> Catalog:
        path = user_catalog_path()
        if path.exists():
            try:
                return with_missing_defaults(load_catalog(path))
            except CatalogError as err:
                self.catalog_error = str(err)
                log.error("User catalog invalid, falling back to defaults: %s", err)
        return load_default_catalog()

    def set_catalog(self, catalog: Catalog, persist: bool = True) -> None:
        self.catalog = catalog
        self.catalog_error = ""
        if persist:
            try:
                save_catalog(catalog, user_catalog_path())
            except OSError as err:
                self.message.emit("error", str(err))
        self._result_cache.clear()
        self.catalogChanged.emit()
        self.recompute()

    def reset_catalog(self) -> None:
        path = user_catalog_path()
        if path.exists():
            path.unlink()
        self.set_catalog(load_default_catalog(), persist=False)

    # ---- settings -------------------------------------------------------------------------
    def update_settings(self, **changes: Any) -> None:
        for k, v in changes.items():
            setattr(self.settings, k, v)
        if "language" in changes:
            set_language(self.settings.language)
            self._result_cache.clear()
        self.settings.save(self.qsettings)
        self.settingsChanged.emit()
        if "language" in changes:
            self.recompute()

    # ---- current site ---------------------------------------------------------------------
    @property
    def site(self) -> SiteInput:
        entry = self.project.site(self.current_id)
        if entry is None:
            entry = self.project.sites[0]
            self.current_id = entry.id
        return entry.input

    def edit(self, text: str, mutate: Callable[[dict[str, Any]], None], merge_key: str | None = None) -> None:
        """Apply ``mutate`` to a dict copy of the current site and record it for undo."""
        before = self.site.model_dump(mode="json")
        data = copy.deepcopy(before)
        mutate(data)
        try:
            after = SiteInput.model_validate(data).model_dump(mode="json")
        except ValueError as err:
            log.info("Rejected edit '%s': %s", text, err)
            self.message.emit("error", str(err).splitlines()[0])
            return
        if after == before:
            return
        self.undo.push(SiteEditCommand(self, self.current_id, before, after, text, merge_key))

    def set_field(self, name: str, value: Any, text: str = "", merge: bool = True) -> None:
        def mutate(d: dict[str, Any]) -> None:
            d[name] = value

        self.edit(text or name, mutate, merge_key=name if merge else None)

    def replace_site(self, new_input: SiteInput, text: str) -> None:
        self.edit(text, lambda d: (d.clear(), d.update(new_input.model_dump(mode="json"))))

    def _apply_site_data(self, site_id: str, data: dict[str, Any]) -> None:
        entry = self.project.site(site_id)
        if entry is None:
            return
        entry.input = SiteInput.model_validate(data)
        if site_id != self.current_id:
            self.current_id = site_id
            self.projectChanged.emit()
        self.siteChanged.emit()
        self.schedule()

    def schedule(self) -> None:
        self._timer.start()

    def recompute(self) -> None:
        self._timer.stop()
        try:
            self.result = size_site(self.site, self.catalog, lang=self.settings.language)
        except Exception:  # never crash the UI on an engine bug
            log.exception("Sizing failed")
            self.message.emit("error", "Помилка розрахунку — деталі в журналі.")
            return
        self.resultChanged.emit(self.result)

    def result_for(self, site_id: str) -> SiteResult | None:
        entry = self.project.site(site_id)
        if entry is None:
            return None
        key = entry.input.model_dump_json()
        cached = self._result_cache.get(site_id)
        if cached and cached[0] == key:
            return cached[1]
        result = size_site(entry.input, self.catalog, lang=self.settings.language)
        self._result_cache[site_id] = (key, result)
        return result

    # ---- project structure ----------------------------------------------------------------
    def _apply_new_site_defaults(self, site: SiteInput) -> None:
        if not site.fortios_version:
            site.fortios_version = self.settings.fortios_default

    def _structure_changed(self, clear_undo: bool = False) -> None:
        self._dirty_struct = True
        if clear_undo:
            self.undo.clear()
        self.dirtyChanged.emit(True)
        self.projectChanged.emit()
        self.siteChanged.emit()
        self.recompute()

    def select_site(self, site_id: str) -> None:
        if site_id == self.current_id or self.project.site(site_id) is None:
            return
        self.current_id = site_id
        self.projectChanged.emit()
        self.siteChanged.emit()
        self.recompute()

    def add_site(self, site: SiteInput | None = None) -> str:
        entry = self.project.add_site(site)
        self._apply_new_site_defaults(entry.input)
        self.current_id = entry.id
        self._structure_changed()
        return entry.id

    def duplicate_site(self, site_id: str | None = None) -> None:
        from ..i18n import tr

        entry = self.project.duplicate_site(site_id or self.current_id, suffix=tr("ui.copy_suffix"))
        if entry:
            self.current_id = entry.id
            self._structure_changed()

    def remove_site(self, site_id: str) -> None:
        if len(self.project.sites) <= 1:
            return
        self.project.remove_site(site_id)
        self._result_cache.pop(site_id, None)
        if self.current_id == site_id:
            self.current_id = self.project.sites[0].id
        self._structure_changed(clear_undo=True)

    def set_hub(self, site_id: str) -> None:
        self.project.set_hub(site_id)
        self._structure_changed()

    def move_site(self, site_id: str, delta: int) -> None:
        entry = self.project.site(site_id)
        if entry is None:
            return
        i = self.project.sites.index(entry)
        j = max(0, min(len(self.project.sites) - 1, i + delta))
        if i != j:
            self.project.sites.insert(j, self.project.sites.pop(i))
            self._structure_changed()

    def update_project_meta(self, **changes: str) -> None:
        for k, v in changes.items():
            setattr(self.project, k, v)
        self._dirty_struct = True
        self.dirtyChanged.emit(True)
        self.projectChanged.emit()

    # ---- files ----------------------------------------------------------------------------
    @property
    def dirty(self) -> bool:
        return self._dirty_struct or not self.undo.isClean()

    def new_project(self) -> None:
        self.project = new_project(author=self.settings.author)
        self._apply_new_site_defaults(self.project.sites[0].input)
        self.current_id = self.project.sites[0].id
        self.project_path = None
        self._result_cache.clear()
        self._dirty_struct = False
        self.undo.clear()
        self.undo.setClean()
        self.dirtyChanged.emit(False)
        self.projectChanged.emit()
        self.siteChanged.emit()
        self.recompute()

    def open_project(self, path: str | Path) -> None:
        project = load_project(path)  # raises ProjectError
        if not project.sites:
            raise ProjectError("Проєкт не містить локацій.")
        self.project = project
        self.project_path = Path(path)
        self.current_id = (project.hub or project.sites[0]).id
        self._result_cache.clear()
        self._dirty_struct = False
        self.undo.clear()
        self.undo.setClean()
        self.settings.push_recent(str(self.project_path))
        self.settings.last_project = str(self.project_path)
        self.settings.save(self.qsettings)
        self.dirtyChanged.emit(False)
        self.projectChanged.emit()
        self.siteChanged.emit()
        self.settingsChanged.emit()
        self.recompute()

    def save_project(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else self.project_path
        if target is None:
            raise ProjectError("no path")
        if not self.project.author:
            self.project.author = self.settings.author
        saved = save_project(self.project, target)
        self.project_path = saved
        self._dirty_struct = False
        self.undo.setClean()
        self.settings.push_recent(str(saved))
        self.settings.last_project = str(saved)
        self.settings.save(self.qsettings)
        self.dirtyChanged.emit(False)
        self.settingsChanged.emit()
        return saved
