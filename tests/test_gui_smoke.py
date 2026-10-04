"""End-to-end GUI smoke test (offscreen): drives the real main window like a user would."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt, QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app() -> QApplication:
    inst = QApplication.instance() or QApplication([])
    from sitesizer.gui.theme import load_fonts, set_font_family

    set_font_family(load_fonts())
    return inst  # type: ignore[return-value]


@pytest.fixture
def window(app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from sitesizer.gui import state as state_mod
    from sitesizer.gui.main_window import MainWindow
    from sitesizer.gui.theme import theme_manager

    monkeypatch.setattr(state_mod, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(state_mod, "user_catalog_path", lambda: tmp_path / "catalog.json")
    qs = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    st = state_mod.AppState(qs)
    st.settings.onboarding_done = True
    theme_manager.apply("light", 1.0)
    win = MainWindow(st)
    win._tour_shown = True
    win.resize(1400, 900)
    win.show()
    app.processEvents()
    yield win
    st._dirty_struct = False
    st.undo.setClean()
    win.close()
    win.deleteLater()
    app.processEvents()


def pump(app: QApplication, n: int = 5) -> None:
    for _ in range(n):
        app.processEvents()
    QTest.qWait(80)


def test_typing_updates_result_and_undo(app: QApplication, window) -> None:
    st = window.state
    loc = window.location
    loc.sockets.field.setFocus()
    loc.sockets.field.selectAll()
    QTest.keyClicks(loc.sockets.field, "100")
    pump(app)
    assert st.site.sockets == 100
    assert st.result is not None and st.result.categories["access_switch"].count == 3
    assert window.summary.sw.value.text() == str(st.result.total_switches)
    st.undo.undo()
    pump(app)
    assert st.site.sockets == 0
    assert loc.sockets.value() == 0
    st.undo.redo()
    pump(app)
    assert st.site.sockets == 100


def test_zones_presets_and_tier(app: QApplication, window) -> None:
    st, loc = window.state, window.location
    loc.add_zone()
    loc.add_zone()
    pump(app)
    assert len(st.site.ap_groups) == 2
    loc._zone_rows[0].qty.setValue(30)
    loc._zone_rows[0].qty.field.valueChanged.emit(30)
    pump(app)
    assert st.site.ap_groups[0].qty == 30
    loc._zone_rows[1].remove.click()
    pump(app)
    assert len(st.site.ap_groups) == 1
    loc.apply_preset("warehouse")
    pump(app)
    assert st.site.cameras == 40 and st.site.tier == 4
    QTest.keyClick(loc.tier, Qt.Key.Key_1)
    pump(app)
    assert st.site.tier == 1
    assert loc.psu_confirm.isVisible()
    yes = [b for b in loc.psu_confirm.findChildren(type(loc.add_zone.__self__.sockets.plus)) if b.text()]
    yes[0].click()
    pump(app)
    assert st.site.redundant_psu is True
    assert not loc.psu_confirm.isVisible()


def test_navigation_and_pages_render(app: QApplication, window) -> None:
    window.location.apply_preset("hq")
    pump(app)
    for key in ("bom", "topology", "ipplan", "power", "compare", "projects", "catalog", "help", "settings", "location"):
        window.navigate(key)
        pump(app, 3)
        assert window.stack.currentWidget() is window.pages[key]
    assert window.bom.proxy.rowCount() > 3
    assert window.ipplan.model.rowCount() >= 4
    assert window.compare.model.rowCount() > 0


def test_sites_save_load(app: QApplication, window, tmp_path: Path) -> None:
    st = window.state
    window.site_action("add", "")
    st.set_field("name", "Філія 2", merge=False)
    window.site_action("duplicate", st.current_id)
    pump(app)
    assert len(st.project.sites) == 3
    path = tmp_path / "p.sizing.json"
    saved = st.save_project(path)
    assert saved.exists() and not st.dirty
    st.new_project()
    assert len(st.project.sites) == 1
    window.open_path(str(path), ask=False)
    pump(app)
    assert len(st.project.sites) == 3
    assert window.sidebar.site_group.buttons().__len__() == 3


def test_theme_and_language_switch(app: QApplication, window) -> None:
    from sitesizer.gui.theme import tokens

    window.toggle_theme()
    pump(app)
    assert tokens().dark
    window.toggle_theme()
    pump(app)
    window.state.update_settings(language="en")
    pump(app)
    assert window.topbar.title.text() == "Location"
    assert window.state.result is not None
    window.state.set_field("sockets", 10, merge=False)
    pump(app)
    assert window.state.result.lines("access_switch")[0].category == "Access switches"
    window.state.update_settings(language="uk")
    pump(app)
    assert window.topbar.title.text() == "Локація"


def test_exports_in_background(app: QApplication, window, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window.location.apply_preset("rnd_office")
    pump(app)
    targets = iter([tmp_path / "a.xlsx", tmp_path / "a.pdf", tmp_path / "a.png", tmp_path / "a.svg"])
    monkeypatch.setattr(window, "_ask_path", lambda *_a, **_k: next(targets))
    window.export_xlsx()
    window.export_pdf()
    window.export_diagram("png")
    window.export_diagram("svg")
    QThreadPool.globalInstance().waitForDone(20_000)
    pump(app, 10)
    for name in ("a.xlsx", "a.pdf", "a.png", "a.svg"):
        assert (tmp_path / name).exists(), name


def test_palette_and_tour(app: QApplication, window) -> None:
    cmds = window.commands()
    assert any("PDF" in c.title for c in cmds)
    from sitesizer.gui.widgets.overlays import CommandPalette, TourOverlay, fuzzy_score

    assert fuzzy_score("pdf", "PDF-звіт") == 0
    assert fuzzy_score("ткм", "тема") is None
    assert fuzzy_score("тм", "тема") is not None
    pal = CommandPalette(window, cmds)
    pal.edit.setText("тема")
    assert pal.list.count() >= 1
    pal.deleteLater()
    window.start_tour()
    pump(app)
    overlays = window.centralWidget().findChildren(TourOverlay)
    assert overlays and overlays[0].isVisible()
    tour = overlays[0]
    assert len(tour.steps) == 5
    for _ in range(5):
        QTest.keyClick(tour, Qt.Key.Key_Right)
        pump(app, 2)
    assert window.state.settings.onboarding_done


def test_catalog_edit_applies(app: QApplication, window) -> None:
    cv = window.catalog
    keys = cv.models.keys
    row = keys.index("FS-148F")
    col = 3  # price
    idx = cv.models.index(row, col)
    assert cv.models.setData(idx, "12345")
    assert window.state.catalog.device("FS-148F").price == 12345.0
    window.state.set_field("sockets", 48, merge=False)
    pump(app)
    assert window.bom.tree.isColumnHidden(2) is False  # price columns appear
    bad = cv.models.setData(cv.models.index(keys.index("FS-148F-FPOE"), 10), "-5")
    assert not bad and not cv.error.isHidden()
    window.state.reset_catalog()
