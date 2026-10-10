"""Catalog editor: models, categories, tiers, rules, AP zones — validated on every change."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, QSortFilterProxyModel, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QScrollArea,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.catalog import CatalogError, catalog_from_dict, load_catalog, load_default_catalog
from ...i18n import current, tr
from ..state import AppState, user_catalog_path
from ..theme import tokens
from ..widgets.controls import (
    Callout,
    Card,
    DoubleSpinBox,
    FieldRow,
    SegmentedControl,
    SpinBox,
    ToggleSwitch,
    button,
    clear_layout,
    label,
    px,
)
from ..widgets.overlays import confirm, prompt_text

# (header key, path inside the model dict, type, applicable kinds or None for all)
MODEL_COLUMNS: list[tuple[str, tuple[str, ...], str, tuple[str, ...] | None]] = [
    ("cat.col.model", ("__key__",), "key", None),
    ("cat.col.kind", ("kind",), "kind", None),
    ("cat.col.family", ("family",), "str", None),
    ("cat.col.code", ("code",), "str", None),
    ("cat.col.price", ("price",), "float?", None),
    ("cat.col.price_min", ("price_min",), "float?", None),
    ("cat.col.unit", ("unit",), "str", None),
    ("cat.col.section", ("section",), "section", None),
    ("cat.col.lifecycle", ("lifecycle",), "lifecycle", None),
    ("cat.col.verified", ("verified",), "verified", None),
    ("cat.col.ports", ("ports", "count"), "int", ("switch",)),
    ("cat.col.port_speed", ("ports", "speed_gbps"), "float", ("switch",)),
    ("cat.col.uplinks", ("uplinks", "count"), "int", ("switch",)),
    ("cat.col.uplink_speed", ("uplinks", "speed_gbps"), "float", ("switch",)),
    ("cat.col.poe_budget", ("poe", "budget_w"), "float", ("switch",)),
    ("cat.col.poe_single", ("poe", "budget_single_psu_w"), "float?", ("switch",)),
    ("cat.col.poe_at", ("poe", "ports_at"), "int", ("switch",)),
    ("cat.col.poe_bt", ("poe", "ports_bt"), "int", ("switch",)),
    ("cat.col.psu_count", ("psu", "count"), "int", ("switch", "firewall")),
    ("cat.col.hot_swap", ("psu", "hot_swap"), "bool", ("switch", "firewall")),
    ("cat.col.redundant", ("psu", "redundant"), "bool", ("switch", "firewall")),
    ("cat.col.power_base", ("power_base_w",), "float?", ("switch", "firewall", "accessory")),
    ("cat.col.power_max", ("power_max_w",), "float?", ("switch", "firewall", "accessory")),
    ("cat.col.ru", ("rack_units",), "int", None),
    ("cat.col.ups_va", ("ups_va",), "int?", ("accessory",)),
    ("cat.col.fw_switches", ("firewall", "max_switches"), "int", ("firewall",)),
    ("cat.col.fw_aps", ("firewall", "max_aps"), "int", ("firewall",)),
    ("cat.col.fw_10g", ("firewall", "ports_10g"), "int", ("firewall",)),
    ("cat.col.fw_threat", ("firewall", "throughput_gbps", "threat"), "float", ("firewall",)),
    ("cat.col.ap_power", ("ap", "power_w"), "float", ("ap",)),
    ("cat.col.ap_poe", ("ap", "poe_class"), "poe", ("ap",)),
    ("cat.col.datasheet", ("datasheet",), "str", None),
    ("cat.col.notes", ("notes",), "str", None),
]
SEARCH_COLUMNS = tuple(
    i
    for i, (h, _p, _t, _k) in enumerate(MODEL_COLUMNS)
    if h in ("cat.col.model", "cat.col.family", "cat.col.code", "cat.col.notes")
)
ENUMS = {
    "kind": ["switch", "firewall", "ap", "accessory", "license", "work"],
    "section": ["", "sks", "network", "works"],
    "lifecycle": ["active", "eoo", "eol"],
    "verified": ["datasheet", "third_party", "assumption"],
    "poe": ["af", "at", "bt"],
}


def kind_defaults(kind: str) -> dict[str, Any]:
    """The sections a model of ``kind`` must have, with neutral values the user then edits."""
    if kind == "switch":
        return {
            "ports": {"count": 24, "speed_gbps": 1},
            "uplinks": {"count": 4, "speed_gbps": 10, "type": "SFP+"},
            "psu": {"count": 1},
        }
    if kind == "firewall":
        tp = dict.fromkeys(("firewall", "ipsec", "ips", "ngfw", "threat", "ssl"), 0)
        return {"firewall": {"max_switches": 0, "max_aps": 0, "throughput_gbps": tp}, "psu": {"count": 1}}
    if kind == "ap":
        return {"ap": {"power_w": 0}}
    return {}


def new_model(
    kind: str,
    name: str = "",
    family: str = "",
    rack_units: int = 1,
    power_w: float | None = None,
    price: float | None = None,
    code: str = "",
) -> dict[str, Any]:
    """A catalog entry for a model the user adds by hand."""
    model: dict[str, Any] = {"kind": kind, **kind_defaults(kind), "rack_units": rack_units}
    if name:
        model["name"] = {"uk": name, "en": name}
    if family:
        model["family"] = family
    if power_w is not None:
        model["power_base_w"] = model["power_max_w"] = power_w
    if price is not None:
        model["price"] = price
    if code:
        model["code"] = code
    return model


class AddModelDialog(QDialog):
    """A new catalog model: SKU, type and the few fields that matter for sizing and the BoM."""

    def __init__(self, existing: set[str], kind: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.existing = existing
        self.setWindowTitle(tr("cat.add_model"))
        self.setMinimumWidth(px(480))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(24), px(20), px(24), px(18))
        lay.setSpacing(px(10))
        lay.addWidget(label(tr("cat.add_model"), "subtitle"))
        lay.addWidget(label(tr("cat.add_model_sub"), "muted", wrap=True))
        self.sku = QLineEdit()
        self.sku.setPlaceholderText("FS-108F")
        lay.addWidget(FieldRow(tr("cat.col.model"), self.sku))
        self.kind = QComboBox()
        for k in ENUMS["kind"]:
            self.kind.addItem(tr(f"cat.kind_{k}"), k)
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind or "accessory")))
        lay.addWidget(FieldRow(tr("cat.col.kind"), self.kind))
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("cat.add_model_name_ph"))
        lay.addWidget(FieldRow(tr("cat.add_model_name"), self.name))
        self.family = QLineEdit()
        lay.addWidget(FieldRow(tr("cat.col.family"), self.family))
        self.units = SpinBox()
        self.units.setRange(0, 10)
        self.units.setValue(1)
        self.units.setSuffix(" U")
        self.units.setFixedWidth(px(100))
        lay.addWidget(FieldRow(tr("cat.col.ru"), self.units))
        self.power = QLineEdit()
        self.power.setPlaceholderText(tr("cat.add_model_power_ph"))
        lay.addWidget(FieldRow(tr("cat.add_model_power"), self.power))
        self.price = QLineEdit()
        lay.addWidget(FieldRow(tr("cat.col.price"), self.price))
        self.code = QLineEdit()
        lay.addWidget(FieldRow(tr("cat.col.code"), self.code))
        for edit in (self.sku, self.kind, self.name, self.family, self.power, self.price, self.code):
            edit.setFixedWidth(px(280))
        self.note = label(tr("cat.add_model_note"), "caption", wrap=True)
        lay.addWidget(self.note)
        self.problem = label("", "caption", wrap=True)
        self.problem.setStyleSheet(f"color: {tokens().error};")
        self.problem.hide()
        lay.addWidget(self.problem)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button(tr("ui.cancel"))
        cancel.clicked.connect(self.reject)
        ok = button(tr("cat.add_model_ok"), "primary", "plus")
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    @staticmethod
    def _number(text: str) -> float | None:
        text = text.strip().replace(",", ".").replace(" ", "")
        return float(text) if text else None

    def _fail(self, text: str, widget: QWidget) -> None:
        self.problem.setText(text)
        self.problem.show()
        widget.setFocus()

    def _accept(self) -> None:
        sku = self.sku.text().strip()
        if not sku:
            return self._fail(tr("cat.add_model_need_sku"), self.sku)
        if sku in self.existing:
            return self._fail(tr("cat.add_model_exists", model=sku), self.sku)
        for edit in (self.power, self.price):
            try:
                value = self._number(edit.text())
            except ValueError:
                return self._fail(tr("cat.err_number"), edit)
            if value is not None and value < 0:
                return self._fail(tr("cat.err_number"), edit)
        self.accept()

    def values(self) -> tuple[str, dict[str, Any]]:
        return self.sku.text().strip(), new_model(
            self.kind.currentData(),
            name=self.name.text().strip(),
            family=self.family.text().strip(),
            rack_units=self.units.value(),
            power_w=self._number(self.power.text()),
            price=self._number(self.price.text()),
            code=self.code.text().strip(),
        )


def _get(d: dict[str, Any], path: tuple[str, ...]) -> Any:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _set(d: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cur = d
    for k in path[:-1]:
        if cur.get(k) is None:
            cur[k] = {}
        cur = cur[k]
    cur[path[-1]] = value


class ModelsTable(QAbstractTableModel):
    def __init__(self, view: CatalogView) -> None:
        super().__init__()
        self.view = view
        self.keys: list[str] = []

    def reload(self) -> None:
        self.beginResetModel()
        self.keys = list(self.view.data["models"].keys())
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.keys)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return len(MODEL_COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(MODEL_COLUMNS[section][0])
        return None

    def _applicable(self, row: int, col: int) -> bool:
        kinds = MODEL_COLUMNS[col][3]
        return kinds is None or self.view.data["models"][self.keys[row]].get("kind") in kinds

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        key = self.keys[index.row()]
        model = self.view.data["models"][key]
        _h, path, typ, _k = MODEL_COLUMNS[index.column()]
        value = key if typ == "key" else _get(model, path)
        applicable = self._applicable(index.row(), index.column())
        if role == Qt.ItemDataRole.CheckStateRole and typ == "bool" and applicable:
            return Qt.CheckState.Checked if value else Qt.CheckState.Unchecked
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if not applicable:
                return "" if role == Qt.ItemDataRole.EditRole else "·"
            if typ == "bool":
                return None
            if value is None:
                return "" if role == Qt.ItemDataRole.EditRole else "—"
            if isinstance(value, float):
                return f"{value:g}"
            return str(value)
        if role == Qt.ItemDataRole.ForegroundRole:
            if not applicable:
                return QColor(tokens().text_faint)
            if typ == "lifecycle" and value != "active":
                return QColor(tokens().error)
            if typ == "verified" and value == "third_party":
                return QColor(tokens().warning)
        if role == Qt.ItemDataRole.ToolTipRole:
            return model.get("notes") or model.get("source") or None
        if role == Qt.ItemDataRole.UserRole:
            return model.get("kind")
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        typ = MODEL_COLUMNS[index.column()][2]
        if typ == "key" or not self._applicable(index.row(), index.column()):
            return f
        if typ == "bool":
            return f | Qt.ItemFlag.ItemIsUserCheckable
        return f | Qt.ItemFlag.ItemIsEditable

    def setData(
        self, index: QModelIndex | QPersistentModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        key = self.keys[index.row()]
        _h, path, typ, _k = MODEL_COLUMNS[index.column()]
        data = copy.deepcopy(self.view.data)
        model = data["models"][key]
        try:
            if typ == "bool":
                new: Any = value == Qt.CheckState.Checked.value or value == Qt.CheckState.Checked
            elif typ in ("int",):
                new = int(str(value).strip())
            elif typ == "int?":
                text = str(value).strip()
                new = int(text) if text else None
            elif typ in ("float", "float?"):
                text = str(value).strip().replace(",", ".")
                new = None if (text == "" and typ == "float?") else float(text)
            elif typ in ENUMS:
                new = str(value).strip()
                if new not in ENUMS[typ]:
                    self.view.show_error(tr("cat.err_enum", values=", ".join(ENUMS[typ])))
                    return False
            else:
                new = str(value)
        except ValueError:
            self.view.show_error(tr("cat.err_number"))
            return False
        _set(model, path, new)
        if typ == "kind":  # a new type needs its own sections (ports, firewall limits, AP power…)
            for section, default in kind_defaults(new).items():
                model.setdefault(section, default)
        if self.view.apply(data):
            self.dataChanged.emit(index, index)
            return True
        return False


class KindFilter(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self.kind = ""
        self.text = ""

    def filterAcceptsRow(self, row: int, parent: QModelIndex | QPersistentModelIndex) -> bool:
        src = self.sourceModel()
        idx = src.index(row, 0, parent)
        if self.kind and src.data(idx, Qt.ItemDataRole.UserRole) != self.kind:
            return False
        if self.text:
            hay = " ".join(str(src.data(src.index(row, c, parent)) or "") for c in SEARCH_COLUMNS).lower()
            return self.text.lower() in hay
        return True


class CatalogView(QWidget):
    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.data: dict[str, Any] = state.catalog.model_dump(mode="json")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(px(12))

        bar = QHBoxLayout()
        bar.setSpacing(px(8))
        self.status = label("", "caption", wrap=True)
        bar.addWidget(self.status, 1)
        for text, icon_name, fn in (
            (tr("cat.import_template"), "file-spreadsheet", self.import_template),
            (tr("cat.import"), "upload", self.import_catalog),
            (tr("cat.export"), "download", self.export_catalog),
            (tr("cat.open_folder"), "folder-open", self.open_folder),
            (tr("cat.reset"), "refresh-ccw", self.reset),
        ):
            b = button(text, "danger" if fn == self.reset else None, icon_name)
            b.clicked.connect(fn)
            bar.addWidget(b)
        root.addLayout(bar)
        self.error = Callout("error")
        self.error.hide()
        root.addWidget(self.error)
        if state.catalog_error:
            self.show_error(state.catalog_error)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._models_tab(), tr("cat.tab_models"))
        self.categories_page = QWidget()
        self.tiers_page = QWidget()
        self.rules_page = QWidget()
        self.zones_page = QWidget()
        for page, title in (
            (self.categories_page, "cat.tab_categories"),
            (self.tiers_page, "cat.tab_tiers"),
            (self.rules_page, "cat.tab_rules"),
            (self.zones_page, "cat.tab_zones"),
        ):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            QVBoxLayout(page).setContentsMargins(px(4), px(12), px(12), px(12))
            self.tabs.addTab(scroll, tr(title))
        self._build_forms()
        state.catalogChanged.connect(self._on_external_change)
        self._update_status()

    # ---- models tab ----------------------------------------------------------------------
    def _models_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, px(12), 0, 0)
        lay.setSpacing(px(10))
        row = QHBoxLayout()
        self.kind_filter = SegmentedControl(
            [
                ("", tr("cat.kind_all")),
                ("switch", tr("cat.kind_switch")),
                ("firewall", tr("cat.kind_firewall")),
                ("ap", tr("cat.kind_ap")),
                ("accessory", tr("cat.kind_accessory")),
                ("license", tr("cat.kind_license")),
                ("work", tr("cat.kind_work")),
            ],
            compact=True,
            expand=False,
        )
        self.kind_filter.valueChanged.connect(self._on_filter)
        row.addWidget(self.kind_filter)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("cat.search"))
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(px(260))
        self.search.textChanged.connect(self._on_filter)
        row.addWidget(self.search)
        row.addStretch(1)
        sheet = button(tr("ui.datasheet"), None, "external-link")
        sheet.clicked.connect(self.open_datasheet)
        row.addWidget(sheet)
        add = button(tr("cat.add_model"), "primary", "plus")
        add.clicked.connect(self.add_model)
        dup = button(tr("cat.duplicate"), None, "copy")
        dup.clicked.connect(self.duplicate_model)
        delete = button(tr("cat.delete"), "danger", "trash-2")
        delete.clicked.connect(self.delete_model)
        row.addWidget(add)
        row.addWidget(dup)
        row.addWidget(delete)
        lay.addLayout(row)
        self.models = ModelsTable(self)
        self.models.reload()
        self.proxy = KindFilter()
        self.proxy.setSourceModel(self.models)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self.table.verticalHeader().setDefaultSectionSize(px(32))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        self.table.resizeColumnsToContents()
        hh.resizeSection(0, px(140))
        lay.addWidget(self.table, 1)
        lay.addWidget(label(tr("cat.models_hint"), "caption", wrap=True))
        return w

    def _on_filter(self, *_args: object) -> None:
        self.proxy.kind = self.kind_filter.value() or ""
        self.proxy.text = self.search.text().strip()
        self.proxy.invalidateFilter()

    def select_model(self, model: str) -> None:
        self.tabs.setCurrentIndex(0)
        self.kind_filter.setValue("")
        self.search.clear()
        self._on_filter()
        if model in self.models.keys:
            src = self.models.index(self.models.keys.index(model), 0)
            idx = self.proxy.mapFromSource(src)
            self.table.selectRow(idx.row())
            self.table.scrollTo(idx)

    def _selected_key(self) -> str | None:
        idx = self.table.currentIndex()
        if not idx.isValid():
            return None
        src = self.proxy.mapToSource(idx)
        return self.models.keys[src.row()]

    def open_datasheet(self) -> None:
        key = self._selected_key()
        if not key:
            self.show_error(tr("cat.select_first"))
            return
        model = self.data["models"][key]
        url = model.get("datasheet") or (model.get("source") if str(model.get("source", "")).startswith("http") else "")
        if url:
            QDesktopServices.openUrl(QUrl(url))
        else:
            self.state.message.emit("info", tr("cat.no_datasheet", model=key))

    def import_template(self) -> None:
        """Import codes, names, units and prices from the company's Excel template."""
        from ...core.template_import import apply_template, read_template

        path, _ = QFileDialog.getOpenFileName(
            self, tr("cat.import_template"), str(Path.home()), "Excel (*.xlsx *.xlsm)"
        )
        if not path:
            return
        try:
            rows = read_template(path)
        except Exception as err:  # openpyxl raises many different errors for broken files
            self.show_error(tr("cat.template_bad", err=err))
            return
        if not rows:
            self.show_error(tr("cat.template_empty"))
            return
        data, report = apply_template(self.data, rows)
        if self.apply(data):
            self.models.reload()
            self._build_forms()
            self.state.message.emit(
                "success",
                tr(
                    "cat.template_done",
                    rows=report.rows,
                    matched=len(report.matched),
                    added=len(report.added),
                    priced=report.priced,
                ),
            )

    def add_model(self) -> None:
        dlg = AddModelDialog(set(self.data["models"]), self.kind_filter.value() or "", self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.insert_model(*dlg.values())

    def insert_model(self, key: str, model: dict[str, Any]) -> bool:
        if key in self.data["models"]:
            self.show_error(tr("cat.add_model_exists", model=key))
            return False
        data = copy.deepcopy(self.data)
        data["models"][key] = model
        if not self.apply(data):
            return False
        self.models.reload()
        self.select_model(key)
        self.state.message.emit("success", tr("cat.add_model_done", model=key))
        return True

    def duplicate_model(self) -> None:
        key = self._selected_key()
        if not key:
            self.show_error(tr("cat.select_first"))
            return
        name = prompt_text(self, tr("cat.duplicate"), tr("cat.duplicate_text"), f"{key}-NEW")
        if not name or name in self.data["models"]:
            return
        data = copy.deepcopy(self.data)
        data["models"][name] = copy.deepcopy(data["models"][key])
        if self.apply(data):
            self.models.reload()
            self.select_model(name)

    def delete_model(self) -> None:
        key = self._selected_key()
        if not key:
            self.show_error(tr("cat.select_first"))
            return
        if not confirm(self, tr("cat.delete"), tr("cat.delete_text", model=key), tr("ui.delete"), danger=True):
            return
        data = copy.deepcopy(self.data)
        del data["models"][key]
        if self.apply(data):
            self.models.reload()

    # ---- forms ---------------------------------------------------------------------------
    def _build_forms(self) -> None:
        self._build_categories()
        self._build_tiers()
        self._build_rules()
        self._build_zones()

    def _edit(self, path: tuple[str, ...], value: Any) -> None:
        data = copy.deepcopy(self.data)
        _set(data, path, value)
        self.apply(data)

    def _build_categories(self) -> None:
        lay = self.categories_page.layout()
        assert lay is not None
        clear_layout(lay)
        t = current()
        switches = [k for k, v in self.data["models"].items() if v.get("kind") == "switch"]
        card = Card(tr("cat.categories_title"), tr("cat.categories_sub"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(px(16))
        grid.setVerticalSpacing(px(10))
        for c, h in enumerate((tr("cat.cat"), tr("cat.base"), tr("cat.premium"), tr("cat.per_switch"))):
            grid.addWidget(label(h, "caption"), 0, c)
        for r, (key, cat) in enumerate(self.data["categories"].items(), start=1):
            grid.addWidget(label(t.pick(cat["label"])), r, 0)
            for c, role in ((1, "base"), (2, "premium")):
                combo = QComboBox()
                combo.addItems(switches)
                combo.setCurrentText(cat[role])
                combo.currentTextChanged.connect(lambda v, k=key, ro=role: self._edit(("categories", k, ro), v))
                grid.addWidget(combo, r, c)
            spin = SpinBox()
            spin.setRange(1, 512)
            spin.setValue(int(cat["endpoints_per_switch"]))
            spin.valueChanged.connect(lambda v, k=key: self._edit(("categories", k, "endpoints_per_switch"), v))
            grid.addWidget(spin, r, 3)
        card.add(grid)
        lay.addWidget(card)
        ladder = Card(tr("cat.ladder_title"), tr("cat.ladder_sub"))
        edit = QLineEdit(", ".join(self.data["firewall_ladder"]))
        edit.editingFinished.connect(
            lambda: self._edit(("firewall_ladder",), [x.strip() for x in edit.text().split(",") if x.strip()])
        )
        ladder.add(edit)
        lay.addWidget(ladder)
        lay.addStretch(1)  # type: ignore[union-attr]

    def _build_tiers(self) -> None:
        lay = self.tiers_page.layout()
        assert lay is not None
        clear_layout(lay)
        t = current()
        card = Card(tr("cat.tiers_title"), tr("cat.tiers_sub"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(px(20))
        grid.setVerticalSpacing(px(10))
        tiers = sorted(self.data["tiers"].items())
        for c, (tid, tier) in enumerate(tiers, start=1):
            grid.addWidget(label(f"{tid} · {t.pick(tier['label'])}", "subtitle"), 0, c)
        effects = [
            "fw_ha",
            "core_redundant",
            "dual_psu",
            "ups",
            "oob",
            "dual_uplinks",
            "dual_wan",
            "spare_percent",
            "fortiguard",
            "forticare",
        ]
        for r, eff in enumerate(effects, start=1):
            grid.addWidget(label(tr(f"tier.{eff}")), r, 0)
            for c, (tid, tier) in enumerate(tiers, start=1):
                value = tier.get(eff)
                if isinstance(value, bool):
                    w: QWidget = QCheckBox()
                    w.setChecked(value)  # type: ignore[attr-defined]
                    w.toggled.connect(lambda v, ti=tid, e=eff: self._edit(("tiers", ti, e), v))  # type: ignore[attr-defined]
                elif eff == "spare_percent":
                    w = DoubleSpinBox()
                    w.setRange(0, 100)
                    w.setSuffix(" %")
                    w.setValue(float(value or 0))
                    w.valueChanged.connect(lambda v, ti=tid: self._edit(("tiers", ti, "spare_percent"), v))
                else:
                    w = QComboBox()
                    opts = ["none", "atp", "utp", "enterprise"] if eff == "fortiguard" else ["none", "premium", "elite"]
                    w.addItems(opts)
                    w.setCurrentText(str(value))
                    w.currentTextChanged.connect(lambda v, ti=tid, e=eff: self._edit(("tiers", ti, e), v))
                grid.addWidget(w, r, c)
        card.add(grid)
        lay.addWidget(card)
        lay.addStretch(1)  # type: ignore[union-attr]

    def _build_rules(self) -> None:
        lay = self.rules_page.layout()
        assert lay is not None
        clear_layout(lay)
        rules = self.data["rules"]
        card = Card(tr("cat.rules_title"), tr("cat.rules_sub"))
        choices = {
            "variant_mode": ["dual_psu", "quantity", "always_base", "always_premium"],
            "power_model": ["datasheet", "legacy"],
        }
        for key, value in rules.items():
            if key in ("ip", "legacy_power_w", "aggregation_auto_threshold", "dac_short_max_u"):
                continue
            title = tr(f"rule.{key}") if current().has(f"rule.{key}") else key
            caption = tr(f"rule.{key}.help") if current().has(f"rule.{key}.help") else ""
            if isinstance(value, bool):
                w: QWidget = ToggleSwitch()
                w.setChecked(value)  # type: ignore[attr-defined]
                w.toggled.connect(lambda v, k=key: self._edit(("rules", k), v))  # type: ignore[attr-defined]
            elif key in choices:
                w = QComboBox()
                w.addItems(choices[key])
                w.setCurrentText(str(value))
                w.currentTextChanged.connect(lambda v, k=key: self._edit(("rules", k), v))
            elif isinstance(value, int):
                w = SpinBox()
                w.setRange(0, 1_000_000)
                w.setValue(value)
                w.valueChanged.connect(lambda v, k=key: self._edit(("rules", k), v))
            elif isinstance(value, float):
                w = DoubleSpinBox()
                w.setRange(0, 1_000_000)
                w.setDecimals(2)
                w.setSingleStep(0.05 if value < 2 else 1)
                w.setValue(value)
                w.valueChanged.connect(lambda v, k=key: self._edit(("rules", k), v))
            elif isinstance(value, list):
                w = QLineEdit(", ".join(str(x) for x in value))
                w.editingFinished.connect(
                    lambda k=key, e=w: self._edit(
                        ("rules", k), [int(x) for x in e.text().replace(" ", "").split(",") if x.strip().isdigit()]
                    )
                )
            else:
                w = QLineEdit(str(value))
                w.editingFinished.connect(lambda k=key, e=w: self._edit(("rules", k), e.text().strip()))
            w.setFixedWidth(px(150))
            card.add(FieldRow(title, w, caption))
        lay.addWidget(card)

        passive = self.data.get("passive") or {}
        pcard = Card(tr("cat.passive_title"), tr("cat.passive_sub"))
        for key, value in passive.items():
            if isinstance(value, dict | list):
                continue
            title = tr(f"passive.{key}") if current().has(f"passive.{key}") else key
            if key == "fiber_default":
                pw: QWidget = QComboBox()
                pw.addItems(["auto", *passive.get("fiber", {}).keys()])  # type: ignore[attr-defined]
                pw.setCurrentText(str(value))  # type: ignore[attr-defined]
                pw.currentTextChanged.connect(lambda v, k=key: self._edit(("passive", k), v))  # type: ignore[attr-defined]
            elif isinstance(value, int):
                pw = SpinBox()
                pw.setRange(0, 1_000_000)
                pw.setValue(value)
                pw.valueChanged.connect(lambda v, k=key: self._edit(("passive", k), v))
            elif isinstance(value, float):
                pw = DoubleSpinBox()
                pw.setRange(0, 100)
                pw.setDecimals(2)
                pw.setSingleStep(0.1)
                pw.setValue(value)
                pw.valueChanged.connect(lambda v, k=key: self._edit(("passive", k), v))
            else:
                pw = QLineEdit(str(value))
                pw.editingFinished.connect(lambda k=key, e=pw: self._edit(("passive", k), e.text().strip()))  # type: ignore[attr-defined]
            pw.setFixedWidth(px(200))
            pcard.add(FieldRow(title, pw))
        panels = passive.get("panels_per_switch") or {}
        for role, key in (("wifi_switch", "wifi"), ("access_switch", "access"), ("camera_switch", "camera")):
            sp = SpinBox()
            sp.setRange(0, 8)
            sp.setValue(int(panels.get(role, 2)))
            sp.valueChanged.connect(lambda v, r=role: self._edit(("passive", "panels_per_switch", r), int(v)))
            sp.setFixedWidth(px(200))
            pcard.add(FieldRow(tr(f"passive.panels_{key}"), sp))
        lay.addWidget(pcard)

        ip = Card(tr("cat.ip_title"), tr("cat.ip_sub"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(px(14))
        for c, h in enumerate((tr("col.segment"), "VLAN", "DHCP")):
            grid.addWidget(label(h, "caption"), 0, c)
        t = current()
        for r, seg in enumerate(rules["ip"]["segments"], start=1):
            grid.addWidget(label(t.pick(seg["label"])), r, 0)
            spin = SpinBox()
            spin.setRange(1, 4094)
            spin.setValue(int(seg["vlan"]))
            spin.valueChanged.connect(lambda v, i=r - 1: self._edit_segment(i, "vlan", v))
            grid.addWidget(spin, r, 1)
            cb = QCheckBox()
            cb.setChecked(bool(seg["dhcp"]))
            cb.toggled.connect(lambda v, i=r - 1: self._edit_segment(i, "dhcp", v))
            grid.addWidget(cb, r, 2)
        ip.add(grid)
        tmpl = QLineEdit(str(rules["ip"].get("id_template", "10.{id}.{vlan}.0/24")))
        tmpl.setFixedWidth(px(200))
        tmpl.editingFinished.connect(lambda e=tmpl: self._edit(("rules", "ip", "id_template"), e.text().strip()))
        ip.add(FieldRow(tr("rule.ip.id_template"), tmpl, tr("ui.ip_id_caption")))
        for key in ("buffer", "smallest_prefix", "static_reserve"):
            value = rules["ip"][key]
            if isinstance(value, float):
                w = DoubleSpinBox()
                w.setRange(0, 10)
                w.setSingleStep(0.05)
            else:
                w = SpinBox()
                w.setRange(0, 1000)
            w.setValue(value)
            w.valueChanged.connect(lambda v, k=key: self._edit(("rules", "ip", k), v))
            w.setFixedWidth(px(150))
            ip.add(FieldRow(tr(f"rule.ip.{key}"), w))
        lay.addWidget(ip)
        lay.addStretch(1)  # type: ignore[union-attr]

    def _edit_segment(self, i: int, key: str, value: Any) -> None:
        data = copy.deepcopy(self.data)
        data["rules"]["ip"]["segments"][i][key] = value
        self.apply(data)

    def _build_zones(self) -> None:
        lay = self.zones_page.layout()
        assert lay is not None
        clear_layout(lay)
        t = current()
        aps = [k for k, v in self.data["models"].items() if v.get("kind") == "ap"]
        card = Card(tr("cat.zones_title"), tr("cat.zones_sub"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(px(16))
        grid.setVerticalSpacing(px(10))
        for c, h in enumerate((tr("ui.zone_type"), tr("ui.ap_model"), tr("cat.clients_per_ap"))):
            grid.addWidget(label(h, "caption"), 0, c)
        for r, (key, zone) in enumerate(self.data["ap_zones"].items(), start=1):
            grid.addWidget(label(t.pick(zone["label"])), r, 0)
            combo = QComboBox()
            combo.addItems(aps)
            combo.setCurrentText(zone["model"])
            combo.currentTextChanged.connect(lambda v, k=key: self._edit(("ap_zones", k, "model"), v))
            grid.addWidget(combo, r, 1)
            spin = SpinBox()
            spin.setRange(0, 500)
            spin.setValue(int(zone.get("clients_per_ap", 15)))
            spin.valueChanged.connect(lambda v, k=key: self._edit(("ap_zones", k, "clients_per_ap"), v))
            grid.addWidget(spin, r, 2)
        card.add(grid)
        lay.addWidget(card)
        lay.addStretch(1)  # type: ignore[union-attr]

    # ---- apply / io ----------------------------------------------------------------------
    def apply(self, data: dict[str, Any]) -> bool:
        try:
            catalog = catalog_from_dict(data)
        except CatalogError as err:
            self.show_error(str(err))
            return False
        self.error.hide()
        self.data = data
        self._applying = True
        try:
            self.state.set_catalog(catalog)
        finally:
            self._applying = False
        self._update_status()
        return True

    def show_error(self, text: str) -> None:
        lines = text.splitlines()
        self.error.text.setText(lines[0] if lines else text)
        self.error.hint.setText("\n".join(lines[1:8]))
        self.error.hint.setVisible(len(lines) > 1)
        self.error.show()

    def _update_status(self) -> None:
        path = user_catalog_path()
        n = len(self.data["models"])
        custom = path.exists()
        self.status.setText(tr("cat.status_custom" if custom else "cat.status_default", n=n, path=str(path)))

    def _on_external_change(self) -> None:
        if getattr(self, "_applying", False):
            return
        self.data = self.state.catalog.model_dump(mode="json")
        self.models.reload()
        self._build_forms()
        self._update_status()

    def import_catalog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("cat.import"), str(Path.home()), "JSON (*.json)")
        if not path:
            return
        try:
            catalog = load_catalog(path)
        except CatalogError as err:
            self.show_error(str(err))
            return
        self.state.set_catalog(catalog)
        self.state.message.emit("success", tr("cat.imported"))

    def export_catalog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("cat.export"), str(Path.home() / "catalog.json"), "JSON (*.json)"
        )
        if path:
            Path(path).write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.state.message.emit("success", tr("ui.exported", name=Path(path).name))

    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(user_catalog_path().parent)))

    def reset(self) -> None:
        if confirm(self, tr("cat.reset"), tr("cat.reset_text"), tr("cat.reset_ok"), danger=True):
            self.state.reset_catalog()
            self.data = load_default_catalog().model_dump(mode="json")
            self.models.reload()
            self._build_forms()
            self._update_status()
            self.state.message.emit("info", tr("cat.reset_done"))
