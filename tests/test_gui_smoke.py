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
    window.state.update_settings(ui_scale=1.25)
    pump(app)
    assert window.sidebar.width() in (round(236 * 1.25), round(64 * 1.25))
    window.state.update_settings(ui_scale=1.0)
    pump(app)
    assert window.state.result is not None


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
    from sitesizer.gui.views.bom import COL_TOTAL, LINE_ROLE
    from sitesizer.gui.views.catalog_view import MODEL_COLUMNS

    def column(key: str) -> int:
        return next(i for i, c in enumerate(MODEL_COLUMNS) if c[0] == key)

    cv = window.catalog
    keys = cv.models.keys
    row = keys.index("FS-148F")
    assert cv.models.setData(cv.models.index(row, column("cat.col.price")), "12345")
    assert cv.models.setData(cv.models.index(row, column("cat.col.code")), "000084545")
    assert window.state.catalog.device("FS-148F").price == 12345.0
    window.state.set_field("sockets", 48, merge=False)
    pump(app)
    line = window.state.result.lines("access_switch")[0]
    assert line.unit_price == 12345.0 and line.code == "000084545"
    group = window.bom.proxy.index(0, 0)
    assert window.bom.proxy.index(0, COL_TOTAL, group).data(LINE_ROLE) is not None
    bad = cv.models.setData(cv.models.index(keys.index("FS-148F-FPOE"), column("cat.col.ports")), "-5")
    assert not bad and not cv.error.isHidden()
    window.state.reset_catalog()


def test_catalog_add_model(app: QApplication, window) -> None:
    from sitesizer.gui.views.catalog_view import MODEL_COLUMNS, AddModelDialog, new_model

    cv = window.catalog
    dlg = AddModelDialog(set(cv.data["models"]), "", cv)
    dlg.sku.setText("FS-148F")
    dlg._accept()
    assert dlg.result() != dlg.DialogCode.Accepted and not dlg.problem.isHidden()  # duplicate SKU
    dlg.sku.setText("NVR-16")
    dlg.name.setText("NVR 16 ch")
    dlg.power.setText("45,5")
    dlg.price.setText("12000")
    dlg._accept()
    key, model = dlg.values()
    assert key == "NVR-16" and model["power_base_w"] == 45.5 and model["price"] == 12000.0
    assert cv.insert_model(key, model)
    dev = window.state.catalog.device("NVR-16")
    assert dev.kind == "accessory" and dev.rack_units == 1 and dev.name["uk"] == "NVR 16 ch"
    # every kind gets the sections it needs, so a new switch / FortiGate / AP is valid right away
    for kind in ("switch", "firewall", "ap", "license", "work"):
        assert cv.insert_model(f"NEW-{kind}", new_model(kind))
    # changing the type in the table fills the missing sections instead of failing
    col = next(i for i, c in enumerate(MODEL_COLUMNS) if c[0] == "cat.col.kind")
    assert cv.models.setData(cv.models.index(cv.models.keys.index("NVR-16"), col), "switch")
    assert window.state.catalog.device("NVR-16").ports is not None
    # a model from the catalog can be put into a cabinet and is counted everywhere
    assert cv.insert_model("NVR-32", new_model("accessory", rack_units=2, power_w=60))
    st = window.state
    st.set_field("sockets", 48, merge=False)
    pump(app)
    window.racks.add_extra("device", 30, 2, "NVR-32")
    pump(app)
    assert any(line.model == "NVR-32" for line in st.result.lines("rack_device"))
    assert ("NVR-32", 1, 60) in st.result.power.breakdown
    st.undo.undo()
    pump(app)
    window.state.reset_catalog()


def _mouse(widget, kind, pos) -> None:
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    types = {
        "press": QEvent.Type.MouseButtonPress,
        "move": QEvent.Type.MouseMove,
        "release": QEvent.Type.MouseButtonRelease,
    }
    buttons = Qt.MouseButton.NoButton if kind == "release" else Qt.MouseButton.LeftButton
    p = QPointF(pos)
    ev = QMouseEvent(
        types[kind], p, widget.mapToGlobal(p), Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier
    )
    QApplication.sendEvent(widget, ev)


def test_rack_editor_drag_and_drop(app: QApplication, window) -> None:
    st = window.state
    st.edit("t", lambda d: d.update(sockets=96, aggregation="yes", mode="extended"))
    st.recompute()
    window.navigate("racks")
    pump(app)
    editor = window.racks.editor
    plan = st.result.rack.plans[0]
    rect = editor.item_rect_in_widget("access_switch:2")
    target = editor.unit_point(plan.key, 3)
    assert rect is not None and target is not None
    _mouse(editor, "press", rect.center())
    _mouse(editor, "move", rect.center() + (target - rect.center()) / 2)
    _mouse(editor, "move", target)
    _mouse(editor, "release", target)
    pump(app)
    pos = st.site.layout.positions["access_switch:2"]
    assert (pos.rack, pos.u) == (plan.key, 3)
    # keyboard nudge, extra organizer, new cabinet
    editor.setFocus()
    QTest.keyClick(editor, Qt.Key.Key_Up)
    pump(app)
    assert st.site.layout.positions["access_switch:2"].u == 4
    window.racks.add_extra("manager")
    dev_id = window.racks.add_extra("device", 30, 1, "FS-148F")
    pump(app)
    assert dev_id and any(line.group == "rack_device" for line in st.result.bom)
    st.undo.undo()
    pump(app)
    window.racks.add_rack(24)
    pump(app)
    assert len(st.site.layout.extras) == 1 and st.site.layout.added == ["user-1"]
    assert [p.size_u for p in st.result.rack.plans][-1] == 24
    st.undo.undo()
    st.undo.undo()
    pump(app)
    assert st.site.layout.added == [] and not st.site.layout.extras


def test_bom_inline_edit_and_custom_line(app: QApplication, window) -> None:
    from PySide6.QtWidgets import QSpinBox

    from sitesizer.gui.views.bom import COL_QTY, LINE_ROLE

    st = window.state
    st.edit("t", lambda d: d.update(sockets=96))
    st.recompute()
    window.navigate("bom")
    pump(app)
    bom = window.bom
    idx = None
    for r in range(bom.proxy.rowCount()):
        g = bom.proxy.index(r, 0)
        for c in range(bom.proxy.rowCount(g)):
            child = bom.proxy.index(c, 0, g)
            if child.data(LINE_ROLE).group == "access_switch":
                idx = child.siblingAtColumn(COL_QTY)
    assert idx is not None
    bom.tree.setCurrentIndex(idx)
    bom.tree.edit(idx)
    pump(app)
    editor = bom.tree.findChild(QSpinBox)
    assert editor is not None
    editor.setValue(4)
    QTest.keyClick(editor, Qt.Key.Key_Return)
    pump(app)
    assert st.site.bom.overrides["access_switch:FS-148F"].qty == 4
    assert st.result.categories["access_switch"].count == 4
    line = st.result.lines("access_switch")[0]
    assert line.manual and line.calc_qty == 2
    bom.commit(line, COL_QTY, 2)  # back to the calculated value → override dropped
    pump(app)
    assert "access_switch:FS-148F" not in st.site.bom.overrides
    st.edit(
        "t",
        lambda d: d["bom"]["custom"].append({"id": "w1", "name": "Монтаж", "qty": 3, "price": 100, "section": "works"}),
    )
    st.recompute()
    pump(app)
    assert st.result.lines("custom")[0].total_price == 300


def test_export_dialog_choices(app: QApplication, window, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openpyxl import load_workbook

    from sitesizer.gui.widgets import export_dialog

    st = window.state
    st.edit("t", lambda d: d.update(sockets=60, location_code="BO7", location_id=7))
    st.recompute()

    def fake_exec(self) -> int:
        self.sheets["prices"].setChecked(False)
        self.sheets["inputs"].setChecked(False)
        self.only_used.setChecked(True)
        self.pdf.setChecked(True)
        self.folder.setText(str(tmp_path))
        self.name.setText("out")
        return 1

    monkeypatch.setattr(export_dialog.ExportDialog, "exec", fake_exec)
    window.export_all()
    QThreadPool.globalInstance().waitForDone(20_000)
    pump(app, 10)
    # each table in its own file by default
    assert sorted(p.name for p in tmp_path.glob("*.xlsx")) == [
        "out — IP-таблиця.xlsx",
        "out — специфікація.xlsx",
        "out — схема шаф.xlsx",
    ]
    assert load_workbook(tmp_path / "out — схема шаф.xlsx").sheetnames == ["BO7 - Шафи"]
    assert (tmp_path / "out.pdf").read_bytes().startswith(b"%PDF")
    assert st.settings.export_choices["only_used"] is True

    def one_file(self) -> int:
        fake_exec(self)
        self.split.setChecked(False)
        return 1

    monkeypatch.setattr(export_dialog.ExportDialog, "exec", one_file)
    window.export_all()
    QThreadPool.globalInstance().waitForDone(20_000)
    pump(app, 10)
    wb = load_workbook(tmp_path / "out.xlsx")
    assert wb.sheetnames == ["Слаботрумка", "BO7 - Схема+шафи", "BO7 - IP"]


def test_settings_and_catalog_move_over_from_the_old_name(tmp_path: Path) -> None:
    from sitesizer.gui.app import migrate_legacy_data

    old_dir = tmp_path / "SiteSizer" / "SiteSizer"
    old_dir.mkdir(parents=True)
    (old_dir / "catalog.json").write_text('{"x": 1}', encoding="utf-8")
    new_dir = tmp_path / "LocalCount" / "LocalCount"
    new_dir.mkdir(parents=True)
    legacy = QSettings(str(tmp_path / "old.ini"), QSettings.Format.IniFormat)
    legacy.setValue("app/settings_json", '{"theme": "dark"}')
    settings = QSettings(str(tmp_path / "new.ini"), QSettings.Format.IniFormat)
    assert migrate_legacy_data(settings, legacy, new_dir)
    assert settings.value("app/settings_json") == '{"theme": "dark"}'
    assert (new_dir / "catalog.json").read_text(encoding="utf-8") == '{"x": 1}'
    # the second start leaves the new data alone
    (new_dir / "catalog.json").write_text('{"x": 2}', encoding="utf-8")
    settings.setValue("app/settings_json", '{"theme": "light"}')
    assert not migrate_legacy_data(settings, legacy, new_dir)
    assert (new_dir / "catalog.json").read_text(encoding="utf-8") == '{"x": 2}'
    assert settings.value("app/settings_json") == '{"theme": "light"}'


def test_racks_rooms_rename_and_delete(app: QApplication, window, monkeypatch: pytest.MonkeyPatch) -> None:
    from sitesizer.gui.views import racks as racks_mod

    monkeypatch.setattr(racks_mod, "confirm", lambda *a, **k: True)
    st = window.state
    racks = window.racks

    def settle() -> None:
        pump(app)
        st.recompute()
        pump(app)

    def chip_texts() -> list[str]:
        return [racks.chips.itemAt(i).widget().text() for i in range(racks.chips.count())]

    st.edit("t", lambda d: d.update(sockets=96, mode="extended"))
    st.recompute()
    window.navigate("racks")
    pump(app)
    # one room: "all cabinets", the room and "+ room"
    assert chip_texts() == ["Усі шафи (1)", "Комутаційна 1 (MDF) · 1 шафа", " Комутаційна"]
    # add a room: the switches are spread over it and the view jumps to it
    racks.add_room_btn.click()
    settle()
    assert st.site.closets == 2 and st.result.rack.rooms[1] == "Комутаційна 2 (IDF-1)"
    assert racks.view_room == 1
    assert [p.room for p in racks.editor.diagram.plans] == [1]
    assert window.location.closets.value() == 2
    # add two cabinets to that room, then look at it and at everything
    racks.add_rack(42)
    settle()
    racks.add_rack(24)
    settle()
    assert [p.room for p in st.result.rack.plans] == [0, 1, 1, 1]
    assert len(racks.editor.diagram.plans) == 3 and racks.view_room == 1
    assert "3 шафи" in chip_texts()[2]
    racks.chips.itemAt(0).widget().click()  # "all cabinets"
    pump(app)
    assert racks.view_room is None and len(racks.editor.diagram.plans) == 4
    racks.chips.itemAt(1).widget().click()  # the main room
    pump(app)
    assert [p.room for p in racks.editor.diagram.plans] == [0] and racks.chips.itemAt(1).widget().isChecked()
    assert not racks.del_room.isEnabled()  # the main room stays
    # delete a cabinet of room 2
    racks.show_room(1)
    pump(app)
    racks.editor.select("user-2")
    racks.delete_rack()
    settle()
    assert [p.key for p in st.result.rack.plans if p.room == 1] == ["idf1-1", "user-1"]
    # rename the room; empty name = automatic again
    racks.room_name.setText("Серверна 2 поверх")
    racks.room_name.editingFinished.emit()
    settle()
    assert st.result.rack.rooms[1] == "Серверна 2 поверх" and "Серверна 2 поверх · 2 шафи" in chip_texts()
    # a third room, then remove the second: the third one takes its place
    racks.add_room()
    settle()
    assert len(st.result.rack.rooms) == 3
    racks.delete_room(1)
    settle()
    assert st.site.closets == 2 and st.result.rack.rooms == ["Комутаційна 1 (MDF)", "Комутаційна 2 (IDF-1)"]
    assert not st.site.layout.added and not st.site.layout.room_names
    st.undo.undo()
    settle()
    assert st.result.rack.rooms[1] == "Серверна 2 поверх" and st.site.layout.added == ["user-1"]
    # a room without cabinets shows a hint instead of an empty canvas
    racks.show_room(2)
    for p in [p for p in st.result.rack.plans if p.room == 2]:
        racks.editor.sel_rack = p.key
        racks.delete_rack()
        settle()
    assert racks.view_room == 2 and racks.empty.isVisible() and not racks.scroll.isVisible()
    racks.add_rack(24)
    settle()
    assert [p.key for p in st.result.rack.plans if p.room == 2] and racks.scroll.isVisible()


def test_rack_items_rename_and_delete(app: QApplication, window) -> None:
    st = window.state
    racks = window.racks
    st.edit("t", lambda d: d.update(sockets=96, mode="extended"))
    st.recompute()
    window.navigate("racks")
    pump(app)
    # name a switch; the model stays visible under the name
    plan = st.result.rack.plans[0]
    sw = next(it for it in plan.items if it.group == "access_switch")
    racks.editor.select(plan.key, sw.id)
    pump(app)
    racks.item_name.setText("Комутатор холу")
    racks.item_name.editingFinished.emit()
    st.recompute()
    pump(app)
    assert st.site.layout.labels == {sw.id: "Комутатор холу"}
    assert racks.editor.item(sw.id).label == "Комутатор холу"
    assert racks.item_title.text() == "Комутатор холу" and racks.item_model.text() == sw.model
    racks.item_name.setText("")
    racks.item_name.editingFinished.emit()
    st.recompute()
    pump(app)
    assert st.site.layout.labels == {} and racks.editor.item(sw.id).label == sw.label
    # an added device can be renamed and deleted (it leaves the bill of materials too)
    racks.editor.select(plan.key)
    dev_id = racks.add_extra("device", 30, 1, "FS-148F", "NVR")
    st.recompute()
    pump(app)
    assert racks.editor.sel_item == dev_id and racks.del_item.isVisible()
    racks.item_name.setText("Відеосервер")
    racks.item_name.editingFinished.emit()
    st.recompute()
    pump(app)
    assert st.site.layout.extras[0].label == "Відеосервер"
    assert any(line.group == "rack_device" for line in st.result.bom)
    racks.del_item.click()
    st.recompute()
    pump(app)
    assert not st.site.layout.extras and racks.editor.item(dev_id) is None
    assert not any(line.group == "rack_device" for line in st.result.bom)
    # active equipment can be deleted too — the firewall and switches leave the specification
    fw = next(it for it in st.result.rack.plans[0].items if it.group == "firewall")
    racks.editor.select(plan.key, fw.id)
    racks.editor.select(plan.key, sw.id, add=True)  # Ctrl+click
    pump(app)
    assert racks.editor.sel_items == [fw.id, sw.id] and racks.del_item.isVisible()
    racks.del_item.click()
    st.recompute()
    pump(app)
    assert not st.result.lines("firewall")
    assert st.result.lines("access_switch")[0].qty == 1
    assert racks.restore_btn.isVisible()
    racks.restore_deleted()
    st.recompute()
    pump(app)
    assert st.result.lines("firewall") and st.result.lines("access_switch")[0].qty == 2


def test_rack_multi_select_drag_and_copy(app: QApplication, window) -> None:
    st = window.state
    racks = window.racks
    st.edit("t", lambda d: d.update(sockets=96, location_code="BO123"))
    st.recompute()
    window.navigate("racks")
    pump(app)
    editor = racks.editor
    plan = st.result.rack.plans[0]
    sws = [it for it in plan.items if it.group == "access_switch"]
    editor.select(plan.key, sws[0].id)
    editor.select(plan.key, sws[1].id, add=True)
    pump(app)
    # Ctrl+C copies the names of the selected devices, one per line
    editor.setFocus()
    QTest.keyClick(editor, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    pump(app)
    assert QApplication.clipboard().text() == "BO123-1A-ASW01\nBO123-1A-ASW02"
    # the "copy names" button copies every device of the cabinets shown
    editor.select(plan.key)
    racks.copy_names()
    assert QApplication.clipboard().text().splitlines()[0] == "BO123-1A-FW01"
    # dragging one of two selected devices moves both by the same distance
    editor.select(plan.key, sws[0].id)
    editor.select(plan.key, sws[1].id, add=True)
    rect = editor.item_rect_in_widget(sws[1].id)
    target = editor.unit_point(plan.key, 3)
    assert rect is not None and target is not None
    _mouse(editor, "press", rect.center())
    _mouse(editor, "move", rect.center() + (target - rect.center()) / 2)
    _mouse(editor, "move", target)
    _mouse(editor, "release", target)
    pump(app)
    pos = st.site.layout.positions
    delta = 3 - sws[1].u
    assert (pos[sws[0].id].u, pos[sws[1].id].u) == (sws[0].u + delta, 3)
    st.undo.undo()
    pump(app)
    assert not st.site.layout.positions


def test_floors_of_one_location(app: QApplication, window) -> None:
    st = window.state
    st.edit("t", lambda d: d.update(sockets=96, location_code="BO123"))
    first = st.current_id
    new_id = st.add_site()
    pump(app)
    assert st.site.floor == 2 and st.site.name == "2 поверх" and st.site.location_code == "BO123"
    st.edit("t", lambda d: d.update(sockets=48, location_id=57))
    st.recompute()
    pump(app)
    # location-wide fields follow on every floor; one undo step reverts both
    assert st.project.site(first).input.location_id == 57
    # one firewall for the location, on the first floor; this floor links to it with an ODF
    assert not st.result.lines("firewall")
    assert any(it.label == "ODF 1A" for p in st.result.rack.plans for it in p.items)
    combined = st.location_result
    assert combined is not None and combined.lines("firewall")[0].qty == 1
    assert combined.lines("access_switch")[0].qty == 3
    window.navigate("bom")
    pump(app)
    assert window.bom.scope_ctl.isVisible() and window.bom.shown() is combined
    # move the firewall to this floor
    window.navigate("location")
    pump(app)
    window.location.fw_here.switch.setChecked(True)
    pump(app)
    assert st.project.site(new_id).is_hub and st.result.lines("firewall")
    st.undo.undo()
    pump(app)
    assert st.project.site(first).input.location_id is None


def test_location_id_typed_and_cleared(app: QApplication, window) -> None:
    st = window.state
    field = window.location.loc_id
    assert field.text() == "" and st.site.location_id is None
    field.setFocus()
    QTest.keyClicks(field, "57")
    pump(app)
    assert st.site.location_id == 57
    QTest.keyClicks(field, "9")  # 579 > 255 is refused
    pump(app)
    assert field.text() == "57" and st.site.location_id == 57
    field.clear()
    pump(app)
    assert st.site.location_id is None
    field.clearFocus()
    QTest.keyClicks(field, "12")
    pump(app)
    assert st.site.location_id == 12
    field.clearFocus()
    while st.undo.canUndo():
        st.undo.undo()
    pump(app)
    assert st.site.location_id is None and field.text() == ""
