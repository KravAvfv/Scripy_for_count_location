"""Racks page: drag & drop cabinet editor with cabinet and device properties.

Cabinets stand in telecom rooms (closets): a row of chips shows every cabinet or only the ones of
one room, and rooms are added, renamed and removed right here (the room count is
``SiteInput.closets``). Every change is an undoable edit of ``SiteInput.layout``; the engine re-applies the manual layout
on top of the automatic one, so DAC lengths, organizers and panels in the BoM follow at once.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.models import SiteResult
from ...core.passive import POWERED_GROUPS, drop_room
from ...exporters.diagram import style_from_tokens
from ...exporters.rack import RackDiagram
from ...i18n import current, tr
from .. import icons
from ..state import AppState
from ..theme import theme_manager, tokens
from ..widgets.controls import (
    Card,
    Chip,
    FieldRow,
    FlowLayout,
    SpinBox,
    button,
    clear_layout,
    hline,
    icon_button,
    label,
    px,
)
from ..widgets.overlays import confirm
from ..widgets.rack_editor import RackEditor

EXTRA_KINDS = ("manager", "panel", "shelf", "blank", "odf")
RACK_SIZES = (9, 12, 15, 18, 24, 27, 32, 42, 47)


class AddDeviceDialog(QDialog):
    """Pick a catalog device (it goes into the bill of materials) or describe a custom one."""

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle(tr("rk.add_device"))
        self.setMinimumWidth(px(500))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(24), px(20), px(24), px(18))
        lay.setSpacing(px(10))
        lay.addWidget(label(tr("rk.add_device"), "subtitle"))
        lay.addWidget(label(tr("rk.add_device_sub"), "muted", wrap=True))
        t = current()
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model.addItem(tr("rk.custom_device"), "")
        for key, dev in state.catalog.models.items():
            if dev.kind in ("license", "work", "ap") or dev.rack_size_u:
                continue
            if dev.kind == "accessory" and not dev.rack_units and dev.order is None:
                continue  # cables, jacks, transceivers… (imported template items stay selectable)
            self.model.addItem(f"{key} — {dev.spec_name or t.pick(dev.name)}"[:90], key)
        comp = self.model.completer()
        if comp is not None:
            comp.setFilterMode(Qt.MatchFlag.MatchContains)
            comp.setCompletionMode(comp.CompletionMode.PopupCompletion)
        self.model.currentIndexChanged.connect(self._prefill)
        lay.addWidget(FieldRow(tr("rk.device_model"), self.model))
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("rk.device_name_ph"))
        lay.addWidget(FieldRow(tr("rk.device_name"), self.name))
        self.height = SpinBox()
        self.height.setRange(1, 10)
        self.height.setSuffix(" U")
        self.height.setFixedWidth(px(100))
        lay.addWidget(FieldRow(tr("rk.device_height"), self.height))
        self.note = label("", "caption", wrap=True)
        lay.addWidget(self.note)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button(tr("ui.cancel"))
        cancel.clicked.connect(self.reject)
        ok = button(tr("rk.add_device_ok"), "primary", "plus")
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self._prefill()

    def _prefill(self) -> None:
        key = self.model.currentData() or ""
        dev = self.state.catalog.models.get(key)
        self.height.setValue((dev.rack_units if dev and dev.rack_units else 1) if dev else self.height.value())
        self.note.setText(tr("rk.device_bom") if dev else tr("rk.device_custom_note"))

    def _accept(self) -> None:
        if not self.model.currentData() and not self.name.text().strip():
            self.name.setFocus()
            return
        self.accept()

    def values(self) -> tuple[str, str, int]:
        return self.model.currentData() or "", self.name.text().strip(), self.height.value()


class RacksView(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._loading = False
        self.view_room: int | None = None
        """Telecom room whose cabinets the editor shows (``None`` = every cabinet)."""
        self._chip_keys: list[tuple[int | None, str]] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(px(12))

        bar = QHBoxLayout()
        bar.setSpacing(px(8))
        add = button(tr("rk.add_rack"), "primary", "plus", tr("rk.add_rack_tip"))
        menu = QMenu(add)
        for size in (24, 42):
            act = QAction(f"{size}U", menu)
            act.triggered.connect(lambda _=False, s=size: self.add_rack(s))
            menu.addAction(act)
        add.setMenu(menu)
        bar.addWidget(add)
        self.copy_btn = button(tr("rk.copy_names"), None, "copy", tr("rk.copy_names_tip"))
        self.copy_btn.clicked.connect(
            lambda: self.copy_names(self.editor.sel_items if len(self.editor.sel_items) > 1 else None)
        )
        bar.addWidget(self.copy_btn)
        self.restore_btn = button(tr("rk.restore", n=0), "ghost", "undo-2", tr("rk.restore_tip"))
        self.restore_btn.clicked.connect(self.restore_deleted)
        bar.addWidget(self.restore_btn)
        self.reset_btn = button(tr("rk.reset"), "ghost", "refresh-ccw", tr("rk.reset_tip"))
        self.reset_btn.clicked.connect(self.reset_layout)
        bar.addWidget(self.reset_btn)
        self.hint = label(tr("rk.hint"), "caption", wrap=True)
        bar.addWidget(self.hint, 1)
        zoom_out = icon_button("zoom-out", tr("ui.zoom_out"))
        zoom_out.clicked.connect(lambda: self.editor.set_zoom(self.editor.zoom / 1.15))
        zoom_in = icon_button("zoom-in", tr("ui.zoom_in"))
        zoom_in.clicked.connect(lambda: self.editor.set_zoom(self.editor.zoom * 1.15))
        bar.addWidget(zoom_out)
        bar.addWidget(zoom_in)
        root.addLayout(bar)

        self.chips_box = QWidget()
        self.chips = FlowLayout(self.chips_box, spacing=6)
        root.addWidget(self.chips_box)

        body = QHBoxLayout()
        body.setSpacing(px(16))
        root.addLayout(body, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.empty = label(tr("rk.room_empty"), "muted", wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.hide()
        self.editor = RackEditor()
        self.editor.moved.connect(self.move_item)
        self.editor.movedMany.connect(self.move_items)
        self.editor.deleteManyRequested.connect(self.delete_items)
        self.editor.copyRequested.connect(lambda ids: self.copy_names(ids or None))
        self.editor.selectionChanged.connect(lambda *_: self._load_side())
        self.editor.contextRequested.connect(self._context)
        self.editor.deleteRequested.connect(self.delete_item)
        self.scroll.setWidget(self.editor)
        body.addWidget(self.scroll, 1)
        body.addWidget(self.empty, 1)

        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side_scroll.setFixedWidth(px(340))
        side = QWidget()
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, px(6), 0)
        side_lay.setSpacing(px(12))
        side_scroll.setWidget(side)
        body.addWidget(side_scroll)

        # ---- telecom room ----------------------------------------------------------------
        self.room_card = Card(tr("rk.room"), tr("rk.room_sub"))
        self.room_name = QLineEdit()
        self.room_name.setClearButtonEnabled(True)
        self.room_name.editingFinished.connect(lambda: self.rename_room(self.room_name.text()))
        self.room_card.add(self.room_name)
        self.room_info = label("", "caption", wrap=True)
        self.room_card.add(self.room_info)
        room_row = QHBoxLayout()
        room_row.setSpacing(px(6))
        room_row.addStretch(1)
        self.del_room = button(tr("rk.delete_room"), "danger", "trash-2")
        self.del_room.clicked.connect(lambda: self.delete_room(self.current_room()))
        room_row.addWidget(self.del_room)
        self.room_card.add(room_row)
        side_lay.addWidget(self.room_card)

        # ---- cabinet ---------------------------------------------------------------------
        self.rack_card = Card(tr("rk.rack"), tr("rk.rack_sub"))
        self.rack_name = QLineEdit()
        self.rack_name.setPlaceholderText(tr("rk.name_auto"))
        self.rack_name.editingFinished.connect(lambda: self._set_prop("name", self.rack_name.text().strip() or None))
        self.rack_card.add(self.rack_name)
        self.floor = SpinBox()
        self.floor.setRange(-10, 300)
        self.floor.setFixedWidth(px(90))
        self.floor.valueChanged.connect(lambda v: self._set_prop("floor", int(v)))
        self.rack_card.add(FieldRow(tr("rk.floor"), self.floor))
        self.letter = QLineEdit()
        self.letter.setMaxLength(3)
        self.letter.setFixedWidth(px(90))
        self.letter.editingFinished.connect(
            lambda: self._set_prop("letter", self.letter.text().strip().upper() or None)
        )
        self.rack_card.add(FieldRow(tr("rk.letter"), self.letter, tr("rk.letter_caption")))
        self.size = QComboBox()
        for s in RACK_SIZES:
            self.size.addItem(f"{s}U", s)
        self.size.setFixedWidth(px(90))
        self.size.currentIndexChanged.connect(lambda _i: self._set_prop("size_u", int(self.size.currentData())))
        self.rack_card.add(FieldRow(tr("rk.size"), self.size))
        self.rack_info = label("", "caption", wrap=True)
        self.rack_card.add(self.rack_info)
        add_row = QHBoxLayout()
        add_row.setSpacing(px(6))
        self.add_extra_btn = button(tr("rk.add_item"), None, "plus")
        extra_menu = QMenu(self.add_extra_btn)
        act = QAction(icons.icon("server", size=16), tr("rk.add_device"), extra_menu)
        act.triggered.connect(lambda: self.add_device())
        extra_menu.addAction(act)
        extra_menu.addSeparator()
        for kind in EXTRA_KINDS:
            act = QAction(tr(f"rack.extra.{kind}"), extra_menu)
            act.triggered.connect(lambda _=False, k=kind: self.add_extra(k))
            extra_menu.addAction(act)
        self.add_extra_btn.setMenu(extra_menu)
        add_row.addWidget(self.add_extra_btn)
        add_row.addStretch(1)
        self.del_rack = button(tr("rk.delete_rack"), "danger", "trash-2")
        self.del_rack.clicked.connect(self.delete_rack)
        add_row.addWidget(self.del_rack)
        self.rack_card.add(add_row)
        side_lay.addWidget(self.rack_card)

        # ---- device ----------------------------------------------------------------------
        self.item_card = Card(tr("rk.item"), "")
        self.item_title = label("", "subtitle", wrap=True)
        self.item_card.add(self.item_title)
        self.item_model = label("", "muted", wrap=True, selectable=True)
        self.item_card.add(self.item_model)
        self.item_name = QLineEdit()
        self.item_name.setPlaceholderText(tr("rk.name_auto"))
        self.item_name.setClearButtonEnabled(True)
        self.item_name.editingFinished.connect(lambda: self.rename_item(self.editor.sel_item, self.item_name.text()))
        self.name_row = FieldRow(tr("rk.item_name"), self.item_name)
        self.item_card.add(self.name_row)
        self.item_info = label("", "muted", wrap=True)
        self.item_card.add(self.item_info)
        row = QHBoxLayout()
        row.setSpacing(px(6))
        self.up_btn = icon_button("chevron-up", tr("rk.up"))
        self.up_btn.clicked.connect(lambda: self._nudge(1))
        self.down_btn = icon_button("chevron-down", tr("rk.down"))
        self.down_btn.clicked.connect(lambda: self._nudge(-1))
        row.addWidget(self.up_btn)
        row.addWidget(self.down_btn)
        row.addStretch(1)
        self.auto_btn = button(tr("rk.item_auto"), "ghost", "refresh-ccw")
        self.auto_btn.clicked.connect(lambda: self.reset_items(list(self.editor.sel_items)))
        row.addWidget(self.auto_btn)
        self.copy_item = icon_button("copy", tr("rk.copy_name"))
        self.copy_item.clicked.connect(lambda: self.copy_names(list(self.editor.sel_items)))
        row.addWidget(self.copy_item)
        self.del_item = button(tr("ui.delete"), "danger", "trash-2", tr("rk.item_delete"))
        self.del_item.clicked.connect(lambda: self.delete_items(list(self.editor.sel_items)))
        row.addWidget(self.del_item)
        self.item_card.add(row)
        self.item_card.add(label(tr("rk.deleted_bom"), "caption", wrap=True))
        side_lay.addWidget(self.item_card)

        # ---- summary ---------------------------------------------------------------------
        self.sum_card = Card(tr("rk.summary"), "")
        self.summary = label("", "muted", wrap=True)
        self.sum_card.add(self.summary)
        self.sum_card.add(hline())
        rules = state.catalog.rules
        self.sum_card.add(
            label(
                tr("rk.legend", short=rules.dac_short_per_switches, long=rules.dac_long_per_switches),
                "caption",
                wrap=True,
            )
        )
        side_lay.addWidget(self.sum_card)
        side_lay.addStretch(1)

        state.resultChanged.connect(self.on_result)
        theme_manager.changed.connect(self._redraw)
        if state.result:
            self.on_result(state.result)

    # =====================================================================================
    # result → widgets
    # =====================================================================================
    def on_result(self, r: SiteResult) -> None:
        self._redraw()
        lines = {line.model: line.qty or 0 for line in r.bom}
        rules = self.state.catalog.rules
        ps = r.rack.passive
        self.summary.setText(
            tr(
                "rk.summary_text",
                racks=len(r.rack.plans),
                used=r.rack.units_total,
                managers=ps.managers,
                panels=ps.panels,
                short=lines.get(rules.dac_short_model, 0),
                long=lines.get(rules.dac_long_model, 0),
            )
        )
        self.reset_btn.setEnabled(not r.input.layout.is_empty)
        hidden = len(r.input.layout.hidden)
        self.restore_btn.setText(tr("rk.restore", n=hidden))
        self.restore_btn.setVisible(hidden > 0)

    def _redraw(self) -> None:
        r = self.state.result
        if r is None:
            return
        tk = tokens()
        diagram = RackDiagram(
            r,
            self.state.catalog,
            self.state.settings.language,
            style=style_from_tokens(tk),
            colors=dict(tk.categories),
            dark=tk.dark,
        )
        if self.view_room is not None and self.view_room >= len(r.rack.rooms):
            self.view_room = None
        if self.view_room is not None:
            diagram.plans = [p for p in diagram.plans if p.room == self.view_room]
        self._load_chips(r)
        self.editor.set_diagram(diagram if diagram.plans else None)
        self.scroll.setVisible(bool(diagram.plans))
        self.empty.setVisible(not diagram.plans)
        self._load_side()

    def _load_chips(self, r: SiteResult) -> None:
        wanted: list[tuple[int | None, str]] = [(None, tr("rk.all_racks", n=len(r.rack.plans)))]
        for i, name in enumerate(r.rack.rooms):
            racks = sum(1 for p in r.rack.plans if p.room == i)
            wanted.append((i, f"{name} · {current().plural('plural.racks', racks)}"))
        if wanted != self._chip_keys:
            clear_layout(self.chips)
            for room, text in wanted:
                chip = Chip(text, checkable=True)
                chip.setProperty("room", -1 if room is None else room)
                chip.clicked.connect(lambda _=False, k=room: self.show_room(k))
                self.chips.addWidget(chip)
            self.add_room_btn = Chip(tr("rk.add_room"), icon_name="plus")
            self.add_room_btn.setToolTip(tr("rk.add_room_tip"))
            self.add_room_btn.clicked.connect(self.add_room)
            self.chips.addWidget(self.add_room_btn)
            self._chip_keys = wanted
        shown = -1 if self.view_room is None else self.view_room
        for i in range(self.chips.count()):
            item = self.chips.itemAt(i)
            w = item.widget() if item is not None else None
            if isinstance(w, Chip) and w.isCheckable():
                w.setChecked(w.property("room") == shown)
        self.chips_box.updateGeometry()

    def show_room(self, room: int | None) -> None:
        """Show the cabinets of one telecom room (``None`` = all of them)."""
        self.view_room = room
        r = self.state.result
        if room is not None and r is not None:
            first = next((p.key for p in r.rack.plans if p.room == room), "")
            self.editor.sel_rack, self.editor.sel_item = first, ""
        self._redraw()
        self.scroll.horizontalScrollBar().setValue(0)
        self.scroll.verticalScrollBar().setValue(0)

    def current_room(self) -> int:
        """The room shown, or else the room of the selected cabinet."""
        if self.view_room is not None:
            return self.view_room
        plan = self.editor.plan(self.editor.sel_rack)
        return plan.room if plan is not None else 0

    def _load_side(self) -> None:
        plan, it = self.editor.selected()
        r = self.state.result
        self._loading = True
        try:
            room = self.current_room()
            if r is not None and room < len(r.rack.rooms):
                if not self.room_name.hasFocus():
                    custom = self.state.site.layout.room_names.get(str(room), "")
                    self.room_name.setText(custom)
                    self.room_name.setPlaceholderText(tr("rk.name_auto") if custom else r.rack.rooms[room])
                racks = [p for p in r.rack.plans if p.room == room]
                self.room_info.setText(
                    tr(
                        "rk.room_info",
                        kind=tr("rk.room_main") if room == 0 else tr("rk.room_remote"),
                        racks=len(racks),
                        used=sum(p.used_u for p in racks),
                    )
                )
                self.del_room.setEnabled(room > 0)
                self.del_room.setToolTip("" if room > 0 else tr("rk.room_main_keep"))
            self.rack_card.setVisible(plan is not None)
            self.rack_card.setEnabled(plan is not None)
            if plan is not None:
                props = self.state.site.layout.props.get(plan.key)
                if not self.rack_name.hasFocus():
                    self.rack_name.setText(props.name if props and props.name else "")
                    self.rack_name.setPlaceholderText(plan.name)
                self.floor.setValue(plan.floor)
                if not self.letter.hasFocus():
                    self.letter.setText(plan.letter)
                idx = self.size.findData(plan.size_u)
                if idx < 0:
                    self.size.addItem(f"{plan.size_u}U", plan.size_u)
                    idx = self.size.count() - 1
                self.size.setCurrentIndex(idx)
                self.rack_info.setText(
                    tr("rk.rack_info", name=plan.name, used=plan.used_u, free=plan.free_u, model=plan.model or "—")
                )
                self.del_rack.setEnabled(self._rack_count() > 1)
            self.item_card.setVisible(it is not None)
            many = len(self.editor.sel_items)
            if it is not None and many > 1:
                group = self.editor.selected_items()
                self.item_title.setText(tr("rk.selected_n", n=many))
                self.item_model.setText("\n".join(g.label for g in group))
                self.item_model.setVisible(True)
                self.name_row.setVisible(False)
                self.item_info.setText(f"U{group[-1].u}–U{group[0].top}")
                self.auto_btn.setVisible(any(g.manual and not g.extra for g in group))
                self.del_item.setText(tr("rk.delete_selected", n=many))
            elif it is not None:
                self.name_row.setVisible(True)
                self.del_item.setText(tr("ui.delete"))
                span = f"U{it.u}" if it.height == 1 else f"U{it.u}–U{it.top}"
                self.item_title.setText(it.label)
                self.item_model.setText(it.model)
                self.item_model.setVisible(bool(it.model))
                if not self.item_name.hasFocus():
                    custom = self._custom_label(it.id)
                    self.item_name.setText(custom)
                    self.item_name.setPlaceholderText(tr("rk.name_auto") if custom else it.label)
                state = tr("rk.item_manual") if it.manual else tr("rk.item_auto_placed")
                self.item_info.setText(f"{span} · {it.height}U · {state}")
                self.auto_btn.setVisible(it.manual and not it.extra)
        finally:
            self._loading = False

    def _rack_count(self) -> int:
        r = self.state.result
        return len(r.rack.plans) if r else 0

    def _custom_label(self, item_id: str) -> str:
        """The name the user gave this item ("" = automatic)."""
        lay = self.state.site.layout
        ex = next((e for e in lay.extras if e.id == item_id), None)
        if ex is not None:
            return ex.label
        return lay.labels.get(item_id, "")

    # =====================================================================================
    # edits (all undoable through AppState.edit)
    # =====================================================================================
    def _layout(self, text: str, fn: Callable[[dict[str, Any]], None]) -> None:
        def mutate(d: dict[str, Any]) -> None:
            lay = d.setdefault("layout", {})
            for key, empty in (
                ("added", []),
                ("removed", []),
                ("props", {}),
                ("positions", {}),
                ("hidden", []),
                ("extras", []),
                ("labels", {}),
                ("room_names", {}),
            ):
                lay.setdefault(key, empty)
            fn(lay)

        self.state.edit(text, mutate)

    def _set_prop(self, key: str, value: Any) -> None:
        if self._loading or not self.editor.sel_rack:
            return
        rack = self.editor.sel_rack
        self._layout(tr("rk.rack"), lambda lay: lay["props"].setdefault(rack, {}).update({key: value}))

    def _room_count(self) -> int:
        r = self.state.result
        return len(r.rack.rooms) if r else 1

    def add_room(self) -> None:
        """A new telecom room: switches are spread over it and it gets a fibre backbone."""
        n = self._room_count()
        self.view_room = n
        self.editor.sel_rack, self.editor.sel_item = "", ""
        self.state.set_field("closets", n + 1, tr("rk.add_room"), merge=False)

    def rename_room(self, name: str) -> None:
        room, name = self.current_room(), name.strip()
        if self._loading or name == self.state.site.layout.room_names.get(str(room), ""):
            return

        def fn(lay: dict[str, Any]) -> None:
            if name:
                lay["room_names"][str(room)] = name
            else:
                lay["room_names"].pop(str(room), None)

        self._layout(tr("rk.room"), fn)

    def delete_room(self, room: int) -> None:
        r = self.state.result
        if r is None or room <= 0 or room >= len(r.rack.rooms):
            return
        if not confirm(
            self, tr("rk.delete_room"), tr("rk.delete_room_text", name=r.rack.rooms[room]), tr("ui.delete"), danger=True
        ):
            return
        cabinets = {p.key for p in r.rack.plans if p.room == room}
        rooms = len(r.rack.rooms)

        def mutate(d: dict[str, Any]) -> None:
            d["closets"] = rooms - 1
            drop_room(d.setdefault("layout", {}), room, cabinets)

        self.view_room = None if self.view_room is None else room - 1
        self.editor.sel_rack, self.editor.sel_item = "", ""
        self.state.edit(tr("rk.delete_room"), mutate)

    def rename_item(self, item_id: str, name: str) -> None:
        name = name.strip()
        if self._loading or not item_id or self.editor.item(item_id) is None or name == self._custom_label(item_id):
            return

        def fn(lay: dict[str, Any]) -> None:
            for ex in lay["extras"]:
                if ex["id"] == item_id:
                    ex["label"] = name
                    return
            if name:
                lay["labels"][item_id] = name
            else:
                lay["labels"].pop(item_id, None)

        self._layout(tr("rk.item_name"), fn)

    def move_item(self, item_id: str, rack: str, u: int) -> None:
        def fn(lay: dict[str, Any]) -> None:
            for ex in lay["extras"]:
                if ex["id"] == item_id:
                    ex["rack"], ex["u"] = rack, u
                    return
            lay["positions"][item_id] = {"rack": rack, "u": u}

        self.editor.sel_item = item_id
        self._layout(tr("rk.moved"), fn)

    def move_items(self, moves: list[tuple[str, str, int]]) -> None:
        """Several devices dragged together: one undoable edit."""

        def fn(lay: dict[str, Any]) -> None:
            for item_id, rack, u in moves:
                ex = next((ex for ex in lay["extras"] if ex["id"] == item_id), None)
                if ex is not None:
                    ex["rack"], ex["u"] = rack, u
                else:
                    lay["positions"][item_id] = {"rack": rack, "u": u}

        if moves:
            self.editor.sel_rack = moves[0][1]
            self.editor.sel_items = [m[0] for m in moves]
        self._layout(tr("rk.moved"), fn)

    def copy_names(self, ids: list[str] | None = None) -> None:
        """Names to the clipboard, one per line: the given items, or every device in the cabinets shown."""
        diagram = self.editor.diagram
        if diagram is None:
            return
        names: list[str] = []
        if ids:
            wanted = set(ids)
            for plan in diagram.plans:
                for it in sorted(plan.items, key=lambda i: -i.u):
                    if it.id in wanted and it.label:
                        names.append(it.label)
        else:
            for plan in diagram.plans:
                for it in sorted(plan.items, key=lambda i: -i.u):
                    if it.group in POWERED_GROUPS and it.label:
                        names.append(it.label)
        if not names:
            return
        QGuiApplication.clipboard().setText("\n".join(names))
        self.state.message.emit("success", tr("rk.copied", n=len(names)))

    def restore_deleted(self) -> None:
        if self.state.site.layout.hidden:
            self._layout(tr("rk.restore", n=len(self.state.site.layout.hidden)), lambda lay: lay.update(hidden=[]))

    def reset_items(self, ids: list[str]) -> None:
        def fn(lay: dict[str, Any]) -> None:
            for item_id in ids:
                lay["positions"].pop(item_id, None)

        if ids:
            self._layout(tr("rk.item_auto"), fn)

    def _nudge(self, step: int) -> None:
        plan, it = self.editor.selected()
        group = self.editor.selected_items()
        if plan is not None and len(group) > 1:
            if self.editor.group_fits(plan, group, step):
                self.move_items([(g.id, plan.key, g.u + step) for g in group])
            return
        if plan is None or it is None:
            return
        target = it.u + step
        while 1 <= target <= plan.size_u and not self.editor.fits(plan, it, target):
            target += step
        if self.editor.fits(plan, it, target):
            self.move_item(it.id, plan.key, target)

    def reset_item(self, item_id: str) -> None:
        if item_id:
            self._layout(tr("rk.item_auto"), lambda lay: lay["positions"].pop(item_id, None))

    def delete_item(self, item_id: str) -> None:
        self.delete_items([item_id])

    def delete_items(self, ids: list[str]) -> None:
        """Remove anything from the cabinets — switches and the firewall too: the specification follows."""
        ids = [i for i in ids if self.editor.item(i) is not None]
        if not ids:
            return

        def fn(lay: dict[str, Any]) -> None:
            for item_id in ids:
                before = len(lay["extras"])
                lay["extras"] = [ex for ex in lay["extras"] if ex["id"] != item_id]
                if len(lay["extras"]) == before and item_id not in lay["hidden"]:
                    lay["hidden"].append(item_id)
                lay["positions"].pop(item_id, None)
                lay["labels"].pop(item_id, None)

        self.editor.sel_item = ""
        self.editor.sel_items = []
        self._layout(tr("rk.item_delete"), fn)

    def add_extra(
        self, kind: str, u: int | None = None, height: int = 1, model: str = "", label: str = ""
    ) -> str | None:
        """Put an item into the selected cabinet at ``u`` (or the highest free slot); returns its id."""
        plan = self.editor.plan(self.editor.sel_rack)
        if plan is None:
            return None
        slot = None
        if u is not None:
            slot = plan.free_slot(height, min(plan.size_u, u + height - 1))
        if slot is None:
            slot = plan.free_slot(height)
        if slot is None:
            self.state.message.emit("warning", tr("rk.no_space"))
            return None
        new_id = f"extra:{uuid.uuid4().hex[:8]}"
        item = {"id": new_id, "kind": kind, "rack": plan.key, "u": slot, "height": height}
        if model:
            item["model"] = model
        if label:
            item["label"] = label
        self.editor.sel_item = new_id
        self._layout(tr(f"rack.extra.{kind}"), lambda lay: lay["extras"].append(item))
        return new_id

    def add_device(self, u: int | None = None) -> None:
        if self.editor.plan(self.editor.sel_rack) is None:
            return
        dlg = AddDeviceDialog(self.state, self)
        if dlg.exec():
            model, label_text, height = dlg.values()
            self.add_extra("device" if model else "custom", u, height, model, label_text)

    def add_rack(self, size: int) -> None:
        taken = {p.key for p in (self.editor.diagram.plans if self.editor.diagram else [])}
        taken |= set(self.state.site.layout.added)
        n = 1
        while f"user-{n}" in taken:
            n += 1
        key = f"user-{n}"

        room = self.current_room()

        def fn(lay: dict[str, Any]) -> None:
            lay["added"].append(key)
            lay["props"].setdefault(key, {}).update(size_u=size, room=room)

        self.editor.sel_rack, self.editor.sel_item = key, ""
        self._layout(tr("rk.add_rack"), fn)

    def delete_rack(self) -> None:
        plan = self.editor.plan(self.editor.sel_rack)
        if plan is None:
            return
        if not confirm(
            self, tr("rk.delete_rack"), tr("rk.delete_rack_text", name=plan.name), tr("ui.delete"), danger=True
        ):
            return
        key = plan.key

        def fn(lay: dict[str, Any]) -> None:
            if key in lay["added"]:
                lay["added"].remove(key)
                lay["props"].pop(key, None)
            elif key not in lay["removed"]:
                lay["removed"].append(key)
            lay["positions"] = {k: v for k, v in lay["positions"].items() if v.get("rack") != key}
            lay["extras"] = [ex for ex in lay["extras"] if ex.get("rack") != key]

        self.editor.sel_rack, self.editor.sel_item = "", ""
        self._layout(tr("rk.delete_rack"), fn)

    def reset_layout(self) -> None:
        if self.state.site.layout.is_empty:
            return
        if confirm(self, tr("rk.reset"), tr("rk.reset_text"), tr("rk.reset"), danger=True):
            self.state.edit(tr("rk.reset"), lambda d: d.update(layout={}))

    # =====================================================================================
    # context menu
    # =====================================================================================
    def _context(self, rack: str, item_id: str, u: int, pos: QPoint) -> None:
        menu = QMenu(self)
        it = self.editor.item(item_id) if item_id else None
        if it is not None:
            ids = list(self.editor.sel_items) if item_id in self.editor.sel_items else [item_id]
            many = len(ids) > 1
            a = QAction(icons.icon("copy", size=16), tr("rk.copy_names") if many else tr("rk.copy_name"), menu)
            a.triggered.connect(lambda: self.copy_names(ids))
            menu.addAction(a)
            if any((g := self.editor.item(i)) is not None and g.manual and not g.extra for i in ids):
                a = QAction(icons.icon("refresh-ccw", size=16), tr("rk.item_auto"), menu)
                a.triggered.connect(lambda: self.reset_items(ids))
                menu.addAction(a)
            text = tr("rk.delete_selected", n=len(ids)) if many else tr("rk.item_delete")
            a = QAction(icons.icon("trash-2", "error", 16), text, menu)
            a.triggered.connect(lambda: self.delete_items(ids))
            menu.addAction(a)
        else:
            sub = menu.addMenu(icons.icon("plus", size=16), tr("rk.add_here", u=u))
            dev_act = QAction(icons.icon("server", size=16), tr("rk.add_device"), sub)
            dev_act.triggered.connect(lambda: self.add_device(u))
            sub.addAction(dev_act)
            sub.addSeparator()
            for kind in EXTRA_KINDS:
                a = QAction(tr(f"rack.extra.{kind}"), sub)
                a.triggered.connect(lambda _=False, k=kind: self.add_extra(k, u))
                sub.addAction(a)
        menu.addSeparator()
        a = QAction(icons.icon("trash-2", "error", 16), tr("rk.delete_rack"), menu)
        a.setEnabled(self._rack_count() > 1)
        a.triggered.connect(self.delete_rack)
        menu.addAction(a)
        menu.exec(pos)
