"""Racks page: drag & drop cabinet editor with cabinet and device properties.

Every change is an undoable edit of ``SiteInput.layout``; the engine re-applies the manual layout
on top of the automatic one, so DAC lengths, organizers and panels in the BoM follow at once.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction
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
from ...core.passive import PASSIVE_GROUPS
from ...exporters.diagram import style_from_tokens
from ...exporters.rack import RackDiagram
from ...i18n import current, tr
from .. import icons
from ..state import AppState
from ..theme import theme_manager, tokens
from ..widgets.controls import Card, FieldRow, SpinBox, button, hline, icon_button, label, px
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
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(px(12))

        bar = QHBoxLayout()
        bar.setSpacing(px(8))
        add = button(tr("rk.add_rack"), "primary", "plus")
        menu = QMenu(add)
        for size in (24, 42):
            act = QAction(f"{size}U", menu)
            act.triggered.connect(lambda _=False, s=size: self.add_rack(s))
            menu.addAction(act)
        add.setMenu(menu)
        bar.addWidget(add)
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

        body = QHBoxLayout()
        body.setSpacing(px(16))
        root.addLayout(body, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.editor = RackEditor()
        self.editor.moved.connect(self.move_item)
        self.editor.selectionChanged.connect(lambda *_: self._load_side())
        self.editor.contextRequested.connect(self._context)
        self.editor.deleteRequested.connect(self.delete_item)
        self.scroll.setWidget(self.editor)
        body.addWidget(self.scroll, 1)

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
        self.auto_btn.clicked.connect(lambda: self.reset_item(self.editor.sel_item))
        row.addWidget(self.auto_btn)
        self.del_item = icon_button("trash-2", tr("rk.item_delete"), "error")
        self.del_item.clicked.connect(lambda: self.delete_item(self.editor.sel_item))
        row.addWidget(self.del_item)
        self.item_card.add(row)
        side_lay.addWidget(self.item_card)

        # ---- summary ---------------------------------------------------------------------
        self.sum_card = Card(tr("rk.summary"), "")
        self.summary = label("", "muted", wrap=True)
        self.sum_card.add(self.summary)
        self.sum_card.add(hline())
        self.sum_card.add(label(tr("rk.legend", u=state.catalog.rules.dac_short_max_u), "caption", wrap=True))
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
        self.editor.set_diagram(diagram if r.rack.plans else None)
        self._load_side()

    def _load_side(self) -> None:
        plan, it = self.editor.selected()
        self._loading = True
        try:
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
                self.del_rack.setEnabled(len(self.editor.diagram.plans) > 1 if self.editor.diagram else False)
            self.item_card.setVisible(it is not None)
            if it is not None:
                span = f"U{it.u}" if it.height == 1 else f"U{it.u}–U{it.top}"
                self.item_title.setText(it.label + (f" ({it.model})" if it.model and it.model not in it.label else ""))
                state = tr("rk.item_manual") if it.manual else tr("rk.item_auto_placed")
                self.item_info.setText(f"{span} · {it.height}U · {state}")
                self.auto_btn.setVisible(it.manual and not it.extra)
                self.del_item.setVisible(it.extra or it.group in PASSIVE_GROUPS)
        finally:
            self._loading = False

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
            ):
                lay.setdefault(key, empty)
            fn(lay)

        self.state.edit(text, mutate)

    def _set_prop(self, key: str, value: Any) -> None:
        if self._loading or not self.editor.sel_rack:
            return
        rack = self.editor.sel_rack
        self._layout(tr("rk.rack"), lambda lay: lay["props"].setdefault(rack, {}).update({key: value}))

    def move_item(self, item_id: str, rack: str, u: int) -> None:
        def fn(lay: dict[str, Any]) -> None:
            for ex in lay["extras"]:
                if ex["id"] == item_id:
                    ex["rack"], ex["u"] = rack, u
                    return
            lay["positions"][item_id] = {"rack": rack, "u": u}

        self.editor.sel_item = item_id
        self._layout(tr("rk.moved"), fn)

    def _nudge(self, step: int) -> None:
        plan, it = self.editor.selected()
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
        it = self.editor.item(item_id)
        if it is None:
            return
        if not (it.extra or it.group in PASSIVE_GROUPS):
            self.state.message.emit("info", tr("rk.cannot_delete"))
            return

        def fn(lay: dict[str, Any]) -> None:
            before = len(lay["extras"])
            lay["extras"] = [ex for ex in lay["extras"] if ex["id"] != item_id]
            if len(lay["extras"]) == before and item_id not in lay["hidden"]:
                lay["hidden"].append(item_id)
            lay["positions"].pop(item_id, None)

        self.editor.sel_item = ""
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

        def fn(lay: dict[str, Any]) -> None:
            lay["added"].append(key)
            lay["props"].setdefault(key, {})["size_u"] = size

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
            if it.manual and not it.extra:
                a = QAction(icons.icon("refresh-ccw", size=16), tr("rk.item_auto"), menu)
                a.triggered.connect(lambda: self.reset_item(item_id))
                menu.addAction(a)
            if it.extra or it.group in PASSIVE_GROUPS:
                a = QAction(icons.icon("trash-2", "error", 16), tr("rk.item_delete"), menu)
                a.triggered.connect(lambda: self.delete_item(item_id))
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
        a.setEnabled(bool(self.editor.diagram and len(self.editor.diagram.plans) > 1))
        a.triggered.connect(self.delete_rack)
        menu.addAction(a)
        menu.exec(pos)
