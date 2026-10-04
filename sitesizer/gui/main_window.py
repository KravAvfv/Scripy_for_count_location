"""Main window: sidebar + pages + summary bar, shortcuts, file handling and exports."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.presets import load_presets
from ..core.project import PROJECT_SUFFIX, ProjectError
from ..core.report import bom_csv, default_export_name, result_to_dict, safe_filename
from ..i18n import current, tr
from . import icons
from .state import AppState
from .theme import theme_manager, tokens
from .views.bom import BomView
from .views.catalog_view import CatalogView
from .views.compare import CompareView
from .views.help_view import HelpView
from .views.ipplan import IpPlanView
from .views.location import LocationView
from .views.power import PowerView
from .views.projects import ProjectsView
from .views.settings_view import SettingsView
from .views.topology import TopologyView
from .widgets.controls import button, icon_button, px, refresh_icons
from .widgets.feedback import ToastHost
from .widgets.overlays import Command, CommandPalette, TourOverlay, TourStep, ask_save, confirm, prompt_text
from .widgets.shell import NAV_BOTTOM, NAV_MAIN, Sidebar, SummaryBar, TopBar
from .workers import run_in_background

log = logging.getLogger(__name__)

PAGES = [k for k, _i, _t in NAV_MAIN + NAV_BOTTOM]


class MainWindow(QMainWindow):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.current_page = "location"
        self.setMinimumSize(QSize(px(1024), px(680)))
        self.setWindowIcon(app_icon())
        self._jobs: list[object] = []
        self._build()
        self._shortcuts()
        self._restore_geometry()
        state.message.connect(self._on_message)
        state.dirtyChanged.connect(self._on_dirty)
        state.projectChanged.connect(self._update_titles)
        state.siteChanged.connect(self._update_titles)
        state.undo.canUndoChanged.connect(self._on_can_undo)
        state.undo.canRedoChanged.connect(self._on_can_redo)
        theme_manager.changed.connect(self._on_theme)
        state.settingsChanged.connect(self._on_settings)
        self._lang = state.settings.language
        state.recompute()
        self.navigate(self.state.qsettings.value("window/page", "location", type=str) or "location")
        if state.catalog_error:
            QTimer.singleShot(400, lambda: self.toasts.show("error", tr("ui.catalog_fallback"), 8000))

    # ---- state hooks (bound methods: auto-disconnected when the window is deleted) --------
    def _on_message(self, severity: str, text: str) -> None:
        self.toasts.show(severity, text)

    def _on_dirty(self, _dirty: bool) -> None:
        self._update_titles()

    def _on_can_undo(self, value: bool) -> None:
        self.undo_btn.setEnabled(value)

    def _on_can_redo(self, value: bool) -> None:
        self.redo_btn.setEnabled(value)

    # ---- construction --------------------------------------------------------------------
    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.sidebar = Sidebar(self.state)
        self.sidebar.navigate.connect(self.navigate)
        self.sidebar.site_action.connect(self.site_action)
        lay.addWidget(self.sidebar)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.topbar = TopBar()
        self.undo_btn = icon_button("undo-2", tr("ui.undo") + "  (Ctrl+Z)")
        self.redo_btn = icon_button("redo-2", tr("ui.redo") + "  (Ctrl+Y)")
        self.undo_btn.clicked.connect(self.state.undo.undo)
        self.redo_btn.clicked.connect(self.state.undo.redo)
        self.undo_btn.setEnabled(False)
        self.redo_btn.setEnabled(False)
        self.palette_btn = button(tr("ui.palette_btn"), "ghost", "command", tr("ui.palette_tip"))
        self.palette_btn.clicked.connect(self.open_palette)
        self.theme_btn = icon_button("moon", tr("ui.toggle_theme"))
        self.theme_btn.clicked.connect(self.toggle_theme)
        self.save_btn = icon_button("save", tr("ui.save") + "  (Ctrl+S)")
        self.save_btn.clicked.connect(self.save)
        self.export_btn = button(tr("ui.export"), "primary", "download")
        self.export_btn.setMenu(self._export_menu())
        for w in (self.palette_btn, self.undo_btn, self.redo_btn, self.save_btn, self.theme_btn, self.export_btn):
            self.topbar.add(w)
        col.addWidget(self.topbar)

        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        self.location = LocationView(self.state)
        self.location.navigate.connect(self.navigate)
        self.bom = BomView(self.state)
        self.bom.open_catalog.connect(self.open_in_catalog)
        self.topology = TopologyView(self.state)
        self.topology.export_requested.connect(self.export_diagram)
        self.ipplan = IpPlanView(self.state)
        self.power = PowerView(self.state)
        self.compare = CompareView(self.state)
        self.projects = ProjectsView(self.state)
        self.projects.request.connect(self.project_request)
        self.catalog = CatalogView(self.state)
        self.help = HelpView(self.state)
        self.help.start_tour.connect(self.start_tour)
        self.settings_view = SettingsView(self.state)
        self.settings_view.start_tour.connect(self.start_tour)
        page_widgets: tuple[tuple[str, QWidget], ...] = (
            ("location", self.location),
            ("bom", self.bom),
            ("topology", self.topology),
            ("ipplan", self.ipplan),
            ("power", self.power),
            ("compare", self.compare),
            ("projects", self.projects),
            ("catalog", self.catalog),
            ("help", self.help),
            ("settings", self.settings_view),
        )
        for key, w in page_widgets:
            holder = QWidget()
            hl = QVBoxLayout(holder)
            hl.setContentsMargins(px(28), px(8), px(24), px(16))
            hl.addWidget(w)
            self.stack.addWidget(holder)
            self.pages[key] = holder
        col.addWidget(self.stack, 1)
        self.summary = SummaryBar(self.state)
        self.summary.open_checks.connect(self.show_checks)
        col.addWidget(self.summary)
        lay.addLayout(col, 1)
        self.setCentralWidget(root)
        self.toasts = ToastHost(root, bottom_offset=lambda: self.summary.height())
        self._update_titles()

    def _export_menu(self) -> QMenu:
        m = QMenu(self)
        items = [
            ("file-spreadsheet", tr("ui.export_xlsx"), "Ctrl+E", self.export_xlsx),
            ("file-text", tr("ui.export_pdf"), "Ctrl+P", self.export_pdf),
            (None, None, None, None),
            ("image", tr("ui.export_png"), "", lambda: self.export_diagram("png")),
            ("download", tr("ui.export_svg"), "", lambda: self.export_diagram("svg")),
            (None, None, None, None),
            ("file-json", tr("ui.export_json"), "", self.export_json),
            ("file-spreadsheet", tr("ui.export_csv"), "", self.export_csv),
            ("copy", tr("ui.copy_bom"), "Ctrl+Shift+C", lambda: self.bom.copy_to_clipboard()),
        ]
        for icon_name, text, sc, fn in items:
            if icon_name is None:
                m.addSeparator()
                continue
            act = QAction(icons.icon(icon_name, size=16), f"{text}\t{sc}" if sc else text, m)
            act.triggered.connect(fn)
            m.addAction(act)
        return m

    def _shortcuts(self) -> None:
        binds = [
            ("Ctrl+S", self.save),
            ("Ctrl+Shift+S", self.save_as),
            ("Ctrl+O", self.open_dialog),
            ("Ctrl+N", lambda: self.site_action("add", "")),
            ("Ctrl+Shift+N", self.new_project),
            ("Ctrl+D", lambda: self.site_action("duplicate", self.state.current_id)),
            ("Ctrl+E", self.export_xlsx),
            ("Ctrl+P", self.export_pdf),
            ("Ctrl+Z", self.state.undo.undo),
            ("Ctrl+Y", self.state.undo.redo),
            ("Ctrl+Shift+Z", self.state.undo.redo),
            ("Ctrl+K", self.open_palette),
            ("F1", lambda: self.navigate("help")),
            ("Ctrl+Shift+C", lambda: self.bom.copy_to_clipboard()),
            ("Ctrl+Shift+L", self.toggle_theme),
            ("Ctrl+L", lambda: self.location.focus_first()),
        ]
        for i, key in enumerate(PAGES[:9], start=1):
            binds.append((f"Ctrl+{i}", lambda k=key: self.navigate(k)))
        for seq, fn in binds:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(fn)

    # ---- navigation ----------------------------------------------------------------------
    def navigate(self, key: str) -> None:
        if key not in self.pages:
            key = "location"
        self.current_page = key
        self.stack.setCurrentWidget(self.pages[key])
        self.sidebar.set_current(key)
        self.state.qsettings.setValue("window/page", key)
        self._update_titles()
        if key == "location":
            QTimer.singleShot(0, self.location.focus_first)
        else:
            self.pages[key].setFocus()

    def show_checks(self) -> None:
        self.navigate("location")
        loc = self.location
        scroll = loc.preview_scroll if loc.preview_scroll.isVisible() else loc.form_scroll
        QTimer.singleShot(50, lambda: scroll.ensureWidgetVisible(loc.checks, 0, px(40)))

    def open_in_catalog(self, model: str) -> None:
        self.navigate("catalog")
        self.catalog.select_model(model)

    def _update_titles(self) -> None:
        key = self.current_page
        text_key = next((t for k, _i, t in NAV_MAIN + NAV_BOTTOM if k == key), "nav.location")
        self.topbar.title.setText(tr(text_key))
        project = self.state.project.name
        site = self.state.site.name
        dirty = "  ·  " + tr("ui.unsaved") if self.state.dirty else ""
        path = f"  ·  {self.state.project_path.name}" if self.state.project_path else ""
        if key in ("projects", "catalog", "help", "settings"):
            self.topbar.subtitle.setText(f"{project}{path}{dirty}")
        else:
            self.topbar.subtitle.setText(f"{project}  ›  {site}{dirty}")
        self.setWindowTitle(f"{'• ' if self.state.dirty else ''}{project} — SiteSizer")

    # ---- sites / projects ----------------------------------------------------------------
    def site_action(self, action: str, site_id: str) -> None:
        st = self.state
        if action == "select":
            st.select_site(site_id)
            if self.current_page in ("projects", "catalog", "help", "settings"):
                self.navigate("location")
        elif action == "add":
            st.add_site()
            self.navigate("location")
            QTimer.singleShot(0, lambda: (self.location.name_edit.setFocus(), self.location.name_edit.selectAll()))
            self.toasts.show("success", tr("ui.site_added"))
        elif action == "duplicate":
            st.duplicate_site(site_id)
            self.toasts.show("success", tr("ui.site_duplicated"))
        elif action == "hub":
            st.set_hub(site_id)
        elif action in ("up", "down"):
            st.move_site(site_id, -1 if action == "up" else 1)
        elif action == "rename":
            entry = st.project.site(site_id)
            if entry:
                name = prompt_text(self, tr("ui.rename"), tr("ui.rename_text"), entry.input.name)
                if name:
                    st.select_site(site_id)
                    st.set_field("name", name, tr("ui.site_name"), merge=False)
        elif action == "delete":
            entry = st.project.site(site_id)
            if entry and confirm(
                self,
                tr("ui.site_delete"),
                tr("ui.site_delete_text", name=entry.input.name),
                tr("ui.delete"),
                danger=True,
            ):
                st.remove_site(site_id)
                self.toasts.show("info", tr("ui.site_deleted", name=entry.input.name))

    def project_request(self, action: str, arg: str) -> None:
        if action == "new":
            self.new_project()
        elif action == "open":
            self.open_dialog()
        elif action == "open_path":
            self.open_path(arg)
        elif action == "save":
            self.save()
        elif action == "save_as":
            self.save_as()
        elif action in ("select", "duplicate", "hub", "delete", "rename", "add", "up", "down"):
            self.site_action(action, arg)
        elif action == "navigate":
            self.navigate(arg)

    def _maybe_save(self) -> bool:
        if not self.state.dirty:
            return True
        choice = ask_save(self)
        if choice == "save":
            return self.save()
        return choice == "discard"

    def new_project(self) -> None:
        if self._maybe_save():
            self.state.new_project()
            self.navigate("location")

    def open_dialog(self) -> None:
        if not self._maybe_save():
            return
        start = self.state.settings.export_dir or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, tr("ui.open_project"), start, f"{tr('ui.project_files')} (*{PROJECT_SUFFIX} *.json)"
        )
        if path:
            self.open_path(path, ask=False)

    def open_path(self, path: str, ask: bool = True) -> None:
        if ask and not self._maybe_save():
            return
        try:
            self.state.open_project(path)
        except ProjectError as err:
            self.toasts.show("error", str(err), 7000)
            return
        self.navigate("location")
        self.toasts.show("success", tr("ui.opened", name=Path(path).name))

    def save(self) -> bool:
        if self.state.project_path is None:
            return self.save_as()
        try:
            saved = self.state.save_project()
        except (OSError, ProjectError) as err:
            self.toasts.show("error", tr("ui.save_failed", err=err), 7000)
            return False
        self.toasts.show("success", tr("ui.saved", name=saved.name))
        return True

    def save_as(self) -> bool:
        start_dir = self.state.settings.export_dir or str(Path.home())
        name = safe_filename(self.state.project.name, "Project") + PROJECT_SUFFIX
        path, _ = QFileDialog.getSaveFileName(
            self, tr("ui.save_as"), str(Path(start_dir) / name), f"{tr('ui.project_files')} (*{PROJECT_SUFFIX})"
        )
        if not path:
            return False
        if not path.endswith(PROJECT_SUFFIX):
            path = path.removesuffix(".json") + PROJECT_SUFFIX
        try:
            saved = self.state.save_project(path)
        except (OSError, ProjectError) as err:
            self.toasts.show("error", tr("ui.save_failed", err=err), 7000)
            return False
        self.toasts.show("success", tr("ui.saved", name=saved.name))
        return True

    # ---- exports -------------------------------------------------------------------------
    def _ask_path(self, ext: str, filt: str, prefix: str = "BoM") -> Path | None:
        start = Path(self.state.settings.export_dir or Path.home())
        name = default_export_name(self.state.site.name, ext, prefix)
        path, _ = QFileDialog.getSaveFileName(self, tr("ui.export"), str(start / name), filt)
        if not path:
            return None
        p = Path(path)
        if p.suffix.lower() != f".{ext}":
            p = p.with_suffix(f".{ext}")
        self.state.update_settings(export_dir=str(p.parent))
        return p

    def _after_export(self, path: Path) -> None:
        self.toasts.show(
            "success",
            tr("ui.exported", name=path.name),
            7000,
            action=(tr("ui.open_file"), lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))),
        )

    def _run_export(self, fn, path: Path) -> None:
        toast = self.toasts.show("info", tr("ui.exporting", name=path.name), busy=True)

        def done(_out: object) -> None:
            toast.dismiss()
            self._after_export(path)

        def failed(err: str) -> None:
            toast.dismiss()
            self.toasts.show("error", tr("ui.export_failed", err=err), 9000)

        self._jobs.append(run_in_background(fn, done, failed))

    def export_xlsx(self) -> None:
        result = self.state.result
        if result is None or not result.has_equipment:
            self.toasts.show("warning", tr("ui.nothing_to_export"))
            return
        path = self._ask_path("xlsx", "Excel (*.xlsx)")
        if not path:
            return
        from ..exporters.xlsx import export_xlsx

        catalog, lang, opts = self.state.catalog, self.state.settings.language, self._report_options()
        self._run_export(lambda: export_xlsx(result, path, catalog=catalog, lang=lang, options=opts), path)

    def export_pdf(self) -> None:
        result = self.state.result
        if result is None or not result.has_equipment:
            self.toasts.show("warning", tr("ui.nothing_to_export"))
            return
        path = self._ask_path("pdf", "PDF (*.pdf)", "Report")
        if not path:
            return
        from ..exporters.pdf import export_pdf

        catalog, lang, opts = self.state.catalog, self.state.settings.language, self._report_options()
        self._run_export(lambda: export_pdf(result, path, catalog=catalog, lang=lang, options=opts), path)

    def _report_options(self) -> dict[str, object]:
        s = self.state.settings
        return {
            "author": s.author or self.state.project.author,
            "company": s.company,
            "logo": s.logo_path,
            "customer": self.state.project.customer,
            "project": self.state.project.name,
            "discount_pct": s.discount_pct,
            "vat_pct": s.vat_pct if s.show_vat else 0.0,
        }

    def export_diagram(self, fmt: str) -> None:
        diagram = self.topology.build_diagram(export=True)
        if diagram is None:
            self.toasts.show("warning", tr("ui.nothing_to_export"))
            return
        path = self._ask_path(fmt, f"{fmt.upper()} (*.{fmt})", "Topology")
        if not path:
            return
        from ..exporters.diagram import export_png, export_svg

        try:
            (export_png if fmt == "png" else export_svg)(diagram, path)
        except OSError as err:
            self.toasts.show("error", tr("ui.export_failed", err=err), 9000)
            return
        self._after_export(path)

    def export_json(self) -> None:
        if self.state.result is None:
            return
        path = self._ask_path("json", "JSON (*.json)")
        if path:
            path.write_text(
                json.dumps(result_to_dict(self.state.result), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._after_export(path)

    def export_csv(self) -> None:
        if self.state.result is None:
            return
        path = self._ask_path("csv", "CSV (*.csv)")
        if path:
            with_prices = any(line.unit_price is not None for line in self.state.result.bom)
            path.write_text(bom_csv(self.state.result, current(), with_prices), encoding="utf-8-sig")
            self._after_export(path)

    # ---- palette / tour / theme ----------------------------------------------------------
    def commands(self) -> list[Command]:
        t = current()
        cmds = [
            Command(
                tr(text_key),
                lambda k=key: self.navigate(k),
                icon_name,
                f"Ctrl+{i + 1}" if i < 9 else "",
                tr("ui.cmd_go"),
            )
            for i, (key, icon_name, text_key) in enumerate(NAV_MAIN + NAV_BOTTOM)
        ]
        cmds += [
            Command(tr("ui.export_xlsx"), self.export_xlsx, "file-spreadsheet", "Ctrl+E", tr("ui.export")),
            Command(tr("ui.export_pdf"), self.export_pdf, "file-text", "Ctrl+P", tr("ui.export")),
            Command(tr("ui.export_png"), lambda: self.export_diagram("png"), "image", "", tr("ui.export")),
            Command(tr("ui.export_svg"), lambda: self.export_diagram("svg"), "download", "", tr("ui.export")),
            Command(tr("ui.copy_bom"), self.bom.copy_to_clipboard, "copy", "Ctrl+Shift+C", tr("ui.export")),
            Command(tr("ui.site_add"), lambda: self.site_action("add", ""), "plus", "Ctrl+N", tr("ui.sites")),
            Command(
                tr("ui.site_duplicate"),
                lambda: self.site_action("duplicate", self.state.current_id),
                "copy",
                "Ctrl+D",
                tr("ui.sites"),
            ),
            Command(tr("ui.save"), self.save, "save", "Ctrl+S", tr("nav.projects")),
            Command(tr("ui.save_as"), self.save_as, "save", "Ctrl+Shift+S", tr("nav.projects")),
            Command(tr("ui.open_project"), self.open_dialog, "folder-open", "Ctrl+O", tr("nav.projects")),
            Command(tr("ui.new_project"), self.new_project, "folder-plus", "Ctrl+Shift+N", tr("nav.projects")),
            Command(tr("ui.toggle_theme"), self.toggle_theme, "moon", "Ctrl+Shift+L", tr("nav.settings")),
            Command(tr("ui.tour_start"), self.start_tour, "sparkles", "", tr("nav.help")),
            Command(
                tr("ui.mode_quick"),
                lambda: self.state.set_field("mode", "quick", tr("ui.mode"), False),
                "zap",
                "",
                tr("ui.mode"),
            ),
            Command(
                tr("ui.mode_extended"),
                lambda: self.state.set_field("mode", "extended", tr("ui.mode"), False),
                "sliders-horizontal",
                "",
                tr("ui.mode"),
            ),
        ]
        for k, tier in sorted(self.state.catalog.tiers.items()):
            cmds.append(
                Command(
                    tr("ui.cmd_tier", n=k, label=t.pick(tier.label)),
                    lambda n=int(k): self.state.set_field("tier", n, tr("ui.tier"), False),
                    "shield",
                    "",
                    tr("ui.tier"),
                )
            )
        for p in load_presets():
            cmds.append(
                Command(
                    tr("ui.cmd_preset", name=t.pick(p.label)),
                    lambda pid=p.id: self.location.apply_preset(pid),
                    "sparkles",
                    "",
                    tr("ui.presets"),
                    t.pick(p.description),
                )
            )
        for entry in self.state.project.sites:
            cmds.append(
                Command(
                    tr("ui.cmd_site", name=entry.input.name),
                    lambda sid=entry.id: self.site_action("select", sid),
                    "map-pin",
                    "",
                    tr("ui.sites"),
                )
            )
        return cmds

    def open_palette(self) -> None:
        CommandPalette(self, self.commands()).popup(self.centralWidget())

    def start_tour(self) -> None:
        self.navigate("location")
        loc = self.location

        def reveal(widget: QWidget) -> None:
            loc.form_scroll.ensureWidgetVisible(widget, 0, px(24))
            QApplication.processEvents()

        steps = [
            TourStep(lambda: loc.preset_box, tr("tour.1.title"), tr("tour.1.text"), lambda: reveal(loc.preset_box)),
            TourStep(lambda: loc.sockets_row, tr("tour.2.title"), tr("tour.2.text"), lambda: reveal(loc.sockets_row)),
            TourStep(
                lambda: loc.tier_card, tr("tour.3.title"), tr("tour.3.text"), lambda: reveal(loc.tier.parentWidget())
            ),
            TourStep(lambda: self.summary, tr("tour.4.title"), tr("tour.4.text")),
            TourStep(lambda: self.export_btn, tr("tour.5.title"), tr("tour.5.text")),
        ]
        overlay = TourOverlay(self.centralWidget(), steps)
        overlay.finished.connect(lambda: self.state.update_settings(onboarding_done=True))
        overlay.start()

    def toggle_theme(self) -> None:
        new = "light" if tokens().dark else "dark"
        self.state.update_settings(theme=new)

    def _on_settings(self) -> None:
        s = self.state.settings
        if s.theme != theme_manager.mode or abs(s.ui_scale - theme_manager.scale) > 1e-3:
            theme_manager.apply(s.theme, s.ui_scale)
        if s.language != self._lang:
            self._lang = s.language
            self.rebuild()
        self._update_titles()

    def _on_theme(self) -> None:
        icons.clear_cache()
        refresh_icons(self)
        self.theme_btn.setIcon(icons.icon("sun" if tokens().dark else "moon", size=16))
        self.export_btn.setIcon(icons.icon("download", "#FFFFFF", 16))
        apply_title_bar_theme(self)

    def rebuild(self) -> None:
        """Re-create all widgets (used after a language switch)."""
        page = self.current_page
        old = self.centralWidget()
        self._build()
        old.deleteLater()
        self.undo_btn.setEnabled(self.state.undo.canUndo())
        self.redo_btn.setEnabled(self.state.undo.canRedo())
        self.state.recompute()
        self.navigate(page)

    # ---- window state --------------------------------------------------------------------
    def _restore_geometry(self) -> None:
        geo = self.state.qsettings.value("window/geometry")
        if isinstance(geo, QByteArray) and not geo.isEmpty():
            self.restoreGeometry(geo)
        else:
            self.resize(px(1360), px(860))

    def closeEvent(self, e: QCloseEvent) -> None:
        if not self._maybe_save():
            e.ignore()
            return
        self.state.qsettings.setValue("window/geometry", self.saveGeometry())
        self.state.settings.save(self.state.qsettings)
        e.accept()

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        self.sidebar.set_compact(self.width() < px(1180))
        self.summary.cost.setVisible(self.summary.cost.isVisible() and self.width() > px(1280))

    def showEvent(self, e) -> None:
        super().showEvent(e)
        apply_title_bar_theme(self)
        if not self.state.settings.onboarding_done and not getattr(self, "_tour_shown", False):
            self._tour_shown = True
            QTimer.singleShot(600, self.start_tour)


def app_icon() -> QIcon:
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainter, QPixmap

    from ..exporters.diagram import paint_brand

    ic = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        paint_brand(p, QRectF(0, 0, size, size))
        p.end()
        ic.addPixmap(pm)
    return ic


def apply_title_bar_theme(win: QWidget) -> None:
    """Dark title bar on Windows 10/11 when the dark theme is active."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        value = ctypes.c_int(1 if tokens().dark else 0)
        hwnd = int(win.winId())
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except (AttributeError, OSError):
        log.debug("Could not set title bar theme", exc_info=True)
