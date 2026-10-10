"""BoM view: grouped, searchable, sortable table + "Why this line?" panel."""

from __future__ import annotations

import html
import uuid
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import (
    QMimeData,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QRectF,
    QSize,
    QSortFilterProxyModel,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ...core.models import BomLine, SiteResult
from ...core.pricing import format_money, summarize_prices
from ...core.report import BOM_GROUP_ORDER, bom_tsv
from ...i18n import current, tr
from .. import icons
from ..state import AppState
from ..theme import theme_manager, tokens
from ..widgets.controls import (
    Card,
    FieldRow,
    Pill,
    SegmentedControl,
    button,
    clear_layout,
    hline,
    label,
    paint_pill,
    px,
)
from ..widgets.overlays import confirm

LINE_ROLE = Qt.ItemDataRole.UserRole + 1
SORT_ROLE = Qt.ItemDataRole.UserRole + 2
GROUP_ROLE = Qt.ItemDataRole.UserRole + 3
KIND_ROLE = Qt.ItemDataRole.UserRole + 4  # "group" | "line"

COL_MODEL, COL_CODE, COL_QTY, COL_PRICE_MIN, COL_PRICE, COL_TOTAL = range(6)
NCOLS = 6
EDITABLE = (COL_QTY, COL_PRICE_MIN, COL_PRICE)

TAG_KEYS = ("manual", "ha", "n+1", "tier", "psu", "auto", "addon", "spare", "license", "eoo", "unverified")


def tag_color(tag: str) -> str:
    t = tokens()
    return {
        "ha": t.categories["firewall"],
        "n+1": t.categories["core_switch"],
        "tier": t.info,
        "psu": t.categories["wifi_switch"],
        "auto": t.warning,
        "addon": t.text_muted,
        "spare": t.categories["spare"],
        "license": t.categories["license"],
        "eoo": t.error,
        "unverified": t.warning,
        "manual": t.warning,
    }.get(tag, t.text_muted)


class BomDelegate(QStyledItemDelegate):
    """Paints group headers, model+description, qty badges and tags; edits quantity and prices."""

    def __init__(self, view: QTreeView, commit: Callable[[BomLine, int, float], None]) -> None:
        super().__init__(view)
        self.view = view
        self.commit = commit

    # -- editing ---------------------------------------------------------------------------
    def createEditor(
        self, parent: QWidget, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QWidget:
        line: BomLine | None = index.data(LINE_ROLE)
        col = index.column()
        if col == COL_QTY:
            ed: QWidget = QSpinBox(parent)
            ed.setRange(0, 1_000_000)  # type: ignore[attr-defined]
        else:
            ed = QDoubleSpinBox(parent)
            ed.setRange(0, 1_000_000_000)  # type: ignore[attr-defined]
            ed.setDecimals(2)  # type: ignore[attr-defined]
            ed.setGroupSeparatorShown(True)  # type: ignore[attr-defined]
        ed.setFrame(False)  # type: ignore[attr-defined]
        ed.setAlignment(Qt.AlignmentFlag.AlignRight)  # type: ignore[attr-defined]
        del line
        return ed

    def setEditorData(self, editor: QWidget, index: QModelIndex | QPersistentModelIndex) -> None:
        line: BomLine | None = index.data(LINE_ROLE)
        if line is None:
            return
        value = {COL_QTY: line.qty, COL_PRICE_MIN: line.price_min, COL_PRICE: line.unit_price}.get(index.column())
        editor.setValue(value or 0)  # type: ignore[attr-defined]
        editor.selectAll()  # type: ignore[attr-defined]

    def setModelData(self, editor: QWidget, _model: Any, index: QModelIndex | QPersistentModelIndex) -> None:
        line: BomLine | None = index.data(LINE_ROLE)
        if line is not None:
            editor.interpretText()  # type: ignore[attr-defined]
            self.commit(line, index.column(), float(editor.value()))  # type: ignore[attr-defined]

    def updateEditorGeometry(
        self, editor: QWidget, option: QStyleOptionViewItem, _index: QModelIndex | QPersistentModelIndex
    ) -> None:
        r = QRect(option.rect)
        editor.setGeometry(r.adjusted(px(4), px(6), -px(4), -(r.height() - px(34))))

    # -- metrics ---------------------------------------------------------------------------
    def _fonts(self) -> tuple[QFont, QFont, QFont]:
        tm = theme_manager
        return (tm.font(None, QFont.Weight.DemiBold), tm.font(tm.type.caption), tm.font())

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        kind = index.data(KIND_ROLE)
        if kind == "group":
            return QSize(option.rect.width(), px(38))
        return QSize(option.rect.width(), px(54))

    # -- painting --------------------------------------------------------------------------
    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        t = tokens()
        kind = index.data(KIND_ROLE)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRect(option.rect)
        if kind == "group":
            p.fillRect(r, t.q("surface_sunken"))
            if index.column() == 0:
                group = str(index.data(GROUP_ROLE))
                dot = QRectF(r.left() + px(4), r.center().y() - px(4), px(8), px(8))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(t.category(group))
                p.drawEllipse(dot)
                p.setPen(t.q("text"))
                bold, small, _ = self._fonts()
                p.setFont(bold)
                text = str(index.data(Qt.ItemDataRole.DisplayRole))
                fm = QFontMetrics(bold)
                tx = int(dot.right()) + px(10)
                p.drawText(QRect(tx, r.top(), r.width(), r.height()), Qt.AlignmentFlag.AlignVCenter, text)
                p.setFont(small)
                p.setPen(t.q("text_muted"))
                count = str(index.data(SORT_ROLE) or "")
                p.drawText(
                    QRect(tx + fm.horizontalAdvance(text) + px(10), r.top(), r.width(), r.height()),
                    Qt.AlignmentFlag.AlignVCenter,
                    count,
                )
            p.setPen(t.q("border"))
            p.drawLine(r.bottomLeft(), r.bottomRight())
            p.restore()
            return

        if selected:
            p.fillRect(r, t.q("selection"))
        elif hovered:
            p.fillRect(r, t.q("surface_hover"))
        line: BomLine | None = index.data(LINE_ROLE)
        bold, small, normal = self._fonts()
        col = index.column()
        inner = r.adjusted(px(8), px(8), -px(8), -px(8))
        muted_ref = line is not None and line.is_reference
        if col == COL_MODEL and line is not None:
            p.setFont(bold)
            p.setPen(t.q("text_muted" if muted_ref else "text"))
            fm = QFontMetrics(bold)
            model = fm.elidedText(line.model, Qt.TextElideMode.ElideRight, inner.width())
            p.drawText(QRect(inner.left(), inner.top(), inner.width(), fm.height()), Qt.AlignmentFlag.AlignLeft, model)
            x = inner.left() + fm.horizontalAdvance(model) + px(8)
            for tag in line.tags:
                if tag not in TAG_KEYS or tag == "addon":
                    continue
                text = tr(f"tag.{tag}")
                w = QFontMetrics(theme_manager.font(theme_manager.type.caption - 1)).horizontalAdvance(text) + px(12)
                if x + w > inner.right():
                    break
                paint_pill(p, QRectF(x, inner.top() + 1, w, px(17)), text, t.q(tag_color(tag)))
                x += w + px(4)
            p.setFont(small)
            p.setPen(t.q("text_muted"))
            sfm = QFontMetrics(small)
            desc = sfm.elidedText(line.description or line.category, Qt.TextElideMode.ElideRight, inner.width())
            p.drawText(
                QRect(inner.left(), inner.top() + fm.height() + px(3), inner.width(), sfm.height()),
                Qt.AlignmentFlag.AlignLeft,
                desc,
            )
        elif col == COL_QTY and line is not None:
            text = "—" if line.qty is None else f"{line.qty} {line.unit}".strip()
            p.setFont(bold)
            fm = QFontMetrics(bold)
            w = max(fm.horizontalAdvance(text) + px(16), px(30))
            badge = QRectF(r.center().x() - w / 2, inner.top() - 1, w, px(22))
            changed = line.qty is not None and line.calc_qty is not None and line.qty != line.calc_qty
            if line.qty is not None:
                p.setPen(QPen(QColor(t.warning), 1.2) if changed else Qt.PenStyle.NoPen)
                p.setBrush(t.q("surface_alt"))
                p.drawRoundedRect(badge, px(11), px(11))
            p.setPen(t.q("text" if line.qty is not None else "text_faint"))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
            if changed:
                p.setFont(small)
                p.setPen(t.q("text_muted"))
                p.drawText(
                    QRectF(r.left(), badge.bottom() + px(2), r.width(), px(16)),
                    Qt.AlignmentFlag.AlignCenter,
                    tr("ui.bom_calc", n=line.calc_qty),
                )
        elif col == COL_CODE and line is not None:
            p.setFont(small)
            p.setPen(t.q("text_muted"))
            p.drawText(inner, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, line.code)
        elif col in (COL_PRICE_MIN, COL_PRICE, COL_TOTAL):
            p.setFont(bold if col == COL_TOTAL else normal)
            text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
            editable = line is not None and line.qty is not None and col != COL_TOTAL
            if not text and editable and (hovered or selected):
                p.setPen(t.q("text_faint"))
                text = tr("ui.bom_set_price")
            else:
                p.setPen(t.q("text"))
            p.drawText(inner, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, text)
        p.setPen(t.q("border"))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        p.restore()


class BomProxy(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self.setRecursiveFilteringEnabled(True)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setSortRole(SORT_ROLE)

    def filterAcceptsRow(self, row: int, parent: QModelIndex | QPersistentModelIndex) -> bool:
        needle = self.filterRegularExpression().pattern()
        if not needle:
            return True
        model = self.sourceModel()
        idx = model.index(row, 0, parent)
        line: BomLine | None = idx.data(LINE_ROLE)
        if line is None:
            return False
        hay = " ".join([line.model, line.code, line.category, line.description]).lower()
        return needle.lower() in hay

    def lessThan(self, a: QModelIndex | QPersistentModelIndex, b: QModelIndex | QPersistentModelIndex) -> bool:
        if a.data(KIND_ROLE) == "group":
            # groups keep their canonical order whatever column is sorted
            asc = self.sortOrder() == Qt.SortOrder.AscendingOrder
            ga = int(a.siblingAtColumn(0).data(Qt.ItemDataRole.UserRole + 10) or 0)
            gb = int(b.siblingAtColumn(0).data(Qt.ItemDataRole.UserRole + 10) or 0)
            return ga < gb if asc else ga > gb
        va, vb = a.data(SORT_ROLE), b.data(SORT_ROLE)
        if va is None:
            return False
        if vb is None:
            return True
        try:
            return va < vb  # type: ignore[no-any-return]
        except TypeError:
            return str(va) < str(vb)


class LineDetail(QScrollArea):
    """Right-hand "Why this line?" panel."""

    open_catalog = Signal(str)

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(px(300))
        self.setMaximumWidth(px(420))
        self.inner = QWidget()
        self.setWidget(self.inner)
        self.lay = QVBoxLayout(self.inner)
        self.lay.setContentsMargins(px(4), 0, px(4), px(12))
        self.lay.setSpacing(px(12))
        self.show_line(None)

    def _clear(self) -> None:
        clear_layout(self.lay)

    def show_line(self, line: BomLine | None) -> None:
        self._clear()
        card = Card(margins=16, spacing=10)
        self.lay.addWidget(card)
        self.lay.addStretch(1)
        t = tokens()
        if line is None:
            ic = QLabel()
            ic.setPixmap(icons.pixmap("circle-help", px(28), "text_faint"))
            card.add(ic)
            card.add(label(tr("ui.why_title"), "subtitle"))
            card.add(label(tr("ui.why_empty"), "muted", wrap=True))
            return
        head = QHBoxLayout()
        head.setSpacing(px(6))
        head.addWidget(Pill(line.category, t.category(line.group)))
        head.addStretch(1)
        card.add(head)
        card.add(label(line.model, "title", selectable=True))
        if line.description:
            card.add(label(line.description, "muted", wrap=True))
        qty = tr("ui.why_qty_ref") if line.qty is None else tr("ui.why_qty", n=line.qty)
        card.add(label(qty, "subtitle"))
        if line.code:
            card.add(label(tr("ui.why_code", code=line.code), "muted", selectable=True))
        if line.manual:
            card.add(
                label(tr("ui.why_manual", n=line.calc_qty if line.calc_qty is not None else "—"), "caption", wrap=True)
            )
        tags = [tg for tg in line.tags if tg in TAG_KEYS]
        if tags:
            card.add(hline())
            card.add(label(tr("ui.why_tags").upper(), "section"))
            for tg in tags:
                row = QHBoxLayout()
                row.setSpacing(px(8))
                row.addWidget(Pill(tr(f"tag.{tg}"), tag_color(tg)), 0, Qt.AlignmentFlag.AlignTop)
                row.addWidget(label(tr(f"tag.{tg}.help"), "caption", wrap=True), 1)
                card.add(row)
        card.add(hline())
        card.add(label(tr("ui.why_reason").upper(), "section"))
        card.add(label(line.reason, wrap=True, selectable=True))
        if line.details:
            card.add(label(tr("ui.why_details").upper(), "section"))
            for d in line.details:
                card.add(label("•  " + d, "muted", wrap=True, selectable=True))
        dev = self.state.catalog.models.get(line.model)
        if dev is not None:
            card.add(hline())
            card.add(label(tr("ui.why_catalog").upper(), "section"))
            specs = spec_lines(line.model, self.state)
            for s in specs:
                card.add(label(s, "muted", wrap=True))
            verified = tr(f"verified.{dev.verified}")
            card.add(label(verified, "caption", wrap=True))
            url = dev.datasheet or (dev.source if dev.source.startswith("http") else "")
            if url:
                ds = button(tr("ui.datasheet"), None, "external-link", url)
                ds.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
                card.add(ds)
            if dev.notes:
                card.add(label(dev.notes, "caption", wrap=True))
            go = button(tr("ui.open_in_catalog"), "ghost", "database")
            go.clicked.connect(lambda: self.open_catalog.emit(line.model))
            card.add(go)


def spec_lines(model: str, state: AppState) -> list[str]:
    dev = state.catalog.models.get(model)
    if dev is None:
        return []
    out = []
    if dev.ports:
        out.append(tr("spec.ports", n=dev.ports.count, speed=f"{dev.ports.speed_gbps:g}"))
    if dev.uplinks:
        out.append(tr("spec.uplinks", n=dev.uplinks.count, speed=f"{dev.uplinks.speed_gbps:g}", type=dev.uplinks.type))
    if dev.poe and dev.poe.budget_w:
        out.append(tr("spec.poe", w=f"{dev.poe.budget_w:g}", at=dev.poe.ports_at, bt=dev.poe.ports_bt))
    if dev.psu:
        out.append(tr("spec.psu_dual") if dev.dual_psu else tr("spec.psu_single"))
    if dev.firewall:
        fw = dev.firewall
        out.append(
            tr(
                "spec.fw",
                sw=fw.switch_limit(state.site.fortios_version or state.catalog.rules.fortios_default),
                ap=fw.max_aps,
                tp=f"{fw.throughput_gbps.threat:g}",
            )
        )
    if dev.ap:
        out.append(tr("spec.ap", w=f"{dev.ap.power_w:g}", cls=dev.ap.poe_class, up=f"{dev.ap.uplink_gbps:g}"))
    if dev.power_max_w:
        out.append(tr("spec.power", w=f"{dev.power_max_w:g}"))
    return out


class AddLineDialog(QDialog):
    """Add a BoM line: pick a catalog item (prices, code and unit are filled in) or type one."""

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle(tr("ui.bom_add"))
        self.setMinimumWidth(px(520))
        lay = QVBoxLayout(self)
        lay.setSpacing(px(10))
        t = current()
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model.addItem(tr("ui.bom_add_free"), "")
        for key, dev in state.catalog.template_models():
            self.model.addItem(f"{key} — {dev.spec_name or t.pick(dev.name)}"[:90], key)
        comp = self.model.completer()
        if comp is not None:
            comp.setFilterMode(Qt.MatchFlag.MatchContains)
            comp.setCompletionMode(comp.CompletionMode.PopupCompletion)
        self.model.currentIndexChanged.connect(self._prefill)
        lay.addWidget(FieldRow(tr("ui.bom_add_item"), self.model))
        self.name = QLineEdit()
        lay.addWidget(FieldRow(tr("col.description"), self.name))
        self.code = QLineEdit()
        self.code.setFixedWidth(px(160))
        lay.addWidget(FieldRow(tr("col.code"), self.code))
        self.unit = QComboBox()
        self.unit.setEditable(True)
        self.unit.addItems(["шт.", "м", "компл.", "посл."])
        self.unit.setFixedWidth(px(160))
        lay.addWidget(FieldRow(tr("col.unit"), self.unit))
        self.section = QComboBox()
        for key in ("sks", "network", "works"):
            self.section.addItem(tr(f"section.{key}"), key)
        self.section.setFixedWidth(px(160))
        lay.addWidget(FieldRow(tr("ui.bom_section"), self.section))
        self.qty = QSpinBox()
        self.qty.setRange(0, 1_000_000)
        self.qty.setValue(1)
        self.qty.setFixedWidth(px(160))
        lay.addWidget(FieldRow(tr("ui.col_qty_short"), self.qty))
        self.price = QDoubleSpinBox()
        self.price_min = QDoubleSpinBox()
        for w in (self.price, self.price_min):
            w.setRange(0, 1_000_000_000)
            w.setDecimals(2)
            w.setGroupSeparatorShown(True)
            w.setFixedWidth(px(160))
        lay.addWidget(FieldRow(tr("col.unit_price"), self.price))
        lay.addWidget(FieldRow(tr("xl.col_price_min"), self.price_min))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _prefill(self) -> None:
        key = self.model.currentData()
        dev = self.state.catalog.models.get(key or "")
        if dev is None:
            return
        from ...core.catalog import section_of

        self.name.setText(dev.spec_name or current().pick(dev.name))
        self.code.setText(dev.code)
        self.unit.setCurrentText(dev.unit)
        idx = self.section.findData(section_of(key, dev))
        self.section.setCurrentIndex(max(0, idx))
        self.price.setValue(dev.price or 0)
        self.price_min.setValue(dev.price_min or 0)

    def line(self) -> dict[str, Any]:
        key = self.model.currentData() or ""
        return {
            "id": uuid.uuid4().hex[:8],
            "model": key,
            "name": self.name.text().strip() or (self.model.currentText() if not key else ""),
            "code": self.code.text().strip(),
            "unit": self.unit.currentText().strip() or "шт.",
            "qty": self.qty.value(),
            "price": self.price.value() or None,
            "price_min": self.price_min.value() or None,
            "section": self.section.currentData(),
        }


class BomView(QWidget):
    open_catalog = Signal(str)

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(px(12))

        bar = QHBoxLayout()
        bar.setSpacing(px(8))
        self.scope = "location"
        """``location`` = every floor added up (read-only), ``floor`` = the current floor (editable)."""
        self.scope_ctl = SegmentedControl(
            [("location", tr("ui.bom_scope_location")), ("floor", tr("ui.bom_scope_floor"))], expand=False
        )
        self.scope_ctl.valueChanged.connect(self._set_scope)
        self.scope_ctl.setFixedWidth(px(280))
        scope_row = QHBoxLayout()
        scope_row.addWidget(self.scope_ctl)
        scope_row.addStretch(1)
        root.addLayout(scope_row)
        self.search = QLineEdit()
        self.search.setObjectName("SearchEdit")
        self.search.setPlaceholderText(tr("ui.bom_search"))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(px(240))
        self.search.setMaximumWidth(px(340))
        self.search.addAction(icons.icon("search", size=14), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setStyleSheet("")
        self.search.textChanged.connect(self._on_search)
        bar.addWidget(self.search)
        self.count_label = label("", "caption")
        bar.addWidget(self.count_label)
        bar.addStretch(1)
        self.add_btn = button(tr("ui.bom_add"), "primary", "plus", tr("ui.bom_add_tip"))
        self.add_btn.clicked.connect(self.add_line)
        bar.addWidget(self.add_btn)
        self.reset_btn = button(tr("ui.bom_reset"), "ghost", "refresh-ccw", tr("ui.bom_reset_tip"))
        self.reset_btn.clicked.connect(self.reset_manual)
        bar.addWidget(self.reset_btn)
        self.expand_btn = button(tr("ui.collapse_all"), "ghost", "chevron-down")
        self.expand_btn.clicked.connect(self._toggle_expand)
        bar.addWidget(self.expand_btn)
        self.copy_btn = button(tr("ui.copy_bom"), None, "copy", tr("ui.copy_bom_tip"))
        self.copy_btn.clicked.connect(self.copy_to_clipboard)
        bar.addWidget(self.copy_btn)
        root.addLayout(bar)
        self.edit_hint = label(tr("ui.bom_edit_hint"), "caption", wrap=True)
        root.addWidget(self.edit_hint)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self.model = QStandardItemModel(0, NCOLS)
        self.proxy = BomProxy()
        self.proxy.setSourceModel(self.model)
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setItemDelegate(BomDelegate(self.tree, self.commit))
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setUniformRowHeights(False)
        self.tree.setSortingEnabled(True)
        self.tree.setMouseTracking(True)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setAccessibleName(tr("nav.bom"))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionsMovable(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.tree.selectionModel().currentRowChanged.connect(self._on_current)
        split.addWidget(self.tree)
        self.detail = LineDetail(state)
        self.detail.open_catalog.connect(self.open_catalog)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([px(900), px(340)])
        root.addWidget(split, 1)

        self.totals = Card(margins=14, spacing=6)
        self.totals_lay = QHBoxLayout()
        self.totals_lay.setSpacing(px(28))
        self.totals.add(self.totals_lay)
        root.addWidget(self.totals)

        self._expanded = True
        state.resultChanged.connect(self.on_result)
        state.settingsChanged.connect(self._on_settings)
        theme_manager.changed.connect(self.tree.viewport().update)
        if state.result:
            self.on_result(state.result)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.detail.setMaximumWidth(px(280) if self.width() < px(980) else px(380))

    def _on_settings(self) -> None:
        if self.state.result is not None:
            self.on_result(self.state.result)

    def _set_scope(self, scope: str) -> None:
        self.scope = scope
        if self.state.result is not None:
            self.on_result(self.state.result)

    def _multi_floor(self) -> bool:
        return len(self.state.project.sites) > 1

    def shown(self) -> SiteResult | None:
        """The result on screen: the whole location, or the current floor."""
        if self.scope == "location" and self._multi_floor():
            return self.state.location_result
        return self.state.result

    def _headers(self) -> None:
        labels = [
            tr("col.model"),
            tr("col.code"),
            tr("ui.col_qty_short"),
            tr("xl.col_price_min"),
            tr("col.unit_price"),
            tr("col.total_price"),
        ]
        self.model.setHorizontalHeaderLabels(labels)
        header = self.tree.header()
        header.setSectionResizeMode(COL_MODEL, QHeaderView.ResizeMode.Stretch)
        for col, width in ((COL_CODE, 100), (COL_QTY, 100), (COL_PRICE_MIN, 120), (COL_PRICE, 120), (COL_TOTAL, 130)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(col, px(width))

    def on_result(self, result: SiteResult) -> None:
        multi = self._multi_floor()
        self.scope_ctl.setVisible(multi)
        readonly = multi and self.scope == "location"
        if readonly and self.state.location_result is not None:
            result = self.state.location_result
        self.edit_hint.setText(tr("ui.bom_scope_hint") if readonly else tr("ui.bom_edit_hint"))
        self.add_btn.setEnabled(not readonly)
        cur = self.tree.currentIndex()
        cur_key = None
        if cur.isValid():
            line: BomLine | None = cur.siblingAtColumn(0).data(LINE_ROLE)
            cur_key = line.key if line else None
        self.model.clear()
        self._headers()
        currency = self.state.catalog.meta.currency
        groups: dict[str, list[tuple[int, BomLine]]] = {}
        for i, line in enumerate(result.bom):
            groups.setdefault(line.group, []).append((i, line))
        t = current()
        order = {g: i for i, g in enumerate(BOM_GROUP_ORDER)}
        restore: QModelIndex | None = None
        for g in sorted(groups, key=lambda g: order.get(g, 99)):
            lines = groups[g]
            title = tr(f"group.{g}")
            gi = [QStandardItem(title)] + [QStandardItem("") for _ in range(NCOLS - 1)]
            for it in gi:
                it.setData("group", KIND_ROLE)
                it.setData(g, GROUP_ROLE)
                it.setData(order.get(g, 99), Qt.ItemDataRole.UserRole + 10)
                it.setEditable(False)
            qty = sum(line.qty or 0 for _, line in lines)
            gi[0].setData(t.plural("plural.pcs", qty) if qty else "", SORT_ROLE)
            for i, line in lines:
                cells = [
                    QStandardItem(line.model),
                    QStandardItem(line.code),
                    QStandardItem("" if line.qty is None else str(line.qty)),
                    QStandardItem(format_money(line.price_min, currency) if line.price_min is not None else ""),
                    QStandardItem(format_money(line.unit_price, currency) if line.unit_price is not None else ""),
                    QStandardItem(format_money(line.total_price, currency) if line.total_price is not None else ""),
                ]
                sort_vals: list[object] = [
                    i,
                    line.code,
                    line.qty if line.qty is not None else -1,
                    line.price_min or 0,
                    line.unit_price or 0,
                    line.total_price or 0,
                ]
                for c, it in enumerate(cells):
                    it.setData(line, LINE_ROLE)
                    it.setData("line", KIND_ROLE)
                    it.setData(line.group, GROUP_ROLE)
                    it.setData(sort_vals[c], SORT_ROLE)
                    it.setEditable(c in EDITABLE and line.qty is not None and not readonly)
                    if c in EDITABLE and line.qty is not None and not readonly:
                        it.setToolTip(tr("ui.bom_edit_tip"))
                gi[0].appendRow(cells)
            self.model.appendRow(gi)
        self.proxy.sort(-1)
        for r in range(self.proxy.rowCount()):
            idx = self.proxy.index(r, 0)
            self.tree.setFirstColumnSpanned(r, QModelIndex(), True)
            self.tree.setExpanded(idx, self._expanded)
            if cur_key:
                for c in range(self.proxy.rowCount(idx)):
                    child = self.proxy.index(c, 0, idx)
                    ln: BomLine | None = child.data(LINE_ROLE)
                    if ln and ln.key == cur_key:
                        restore = child
        if restore is not None:
            self.tree.setCurrentIndex(restore)
        else:
            self.detail.show_line(None)
        purch = [line for line in result.bom if line.qty]
        self.count_label.setText(tr("ui.bom_count", lines=len(purch), pcs=sum(line.qty or 0 for line in purch)))
        edits = self.state.site.bom
        self.reset_btn.setEnabled(bool(edits.overrides or edits.custom) and not readonly)
        self._update_totals(result)

    # ---- manual edits --------------------------------------------------------------------
    def commit(self, line: BomLine, col: int, value: float) -> None:
        """Store a manual quantity / price for ``line`` (an undoable edit of the location)."""
        field = {COL_QTY: "qty", COL_PRICE: "price", COL_PRICE_MIN: "price_min"}[col]
        new: int | float | None = int(value) if field == "qty" else round(value, 2)
        if line.group == "custom":
            cid = line.key.split(":", 1)[1]

            def mutate_custom(d: dict[str, Any]) -> None:
                for c in d.setdefault("bom", {}).setdefault("custom", []):
                    if c["id"] == cid:
                        c[field] = new if (field == "qty" or new) else None

            self.state.edit(tr("ui.bom_edit"), mutate_custom)
            return
        if field == "qty" and new == line.calc_qty:
            new = None

        def mutate(d: dict[str, Any]) -> None:
            overrides = d.setdefault("bom", {}).setdefault("overrides", {})
            ov = overrides.setdefault(line.key, {})
            ov[field] = new
            if all(v is None for v in ov.values()):
                overrides.pop(line.key, None)

        self.state.edit(tr("ui.bom_edit"), mutate)

    def revert(self, line: BomLine) -> None:
        if line.group == "custom":
            return
        self.state.edit(
            tr("ui.bom_revert"), lambda d: d.setdefault("bom", {}).setdefault("overrides", {}).pop(line.key, None)
        )

    def remove_custom(self, line: BomLine) -> None:
        cid = line.key.split(":", 1)[1]

        def mutate(d: dict[str, Any]) -> None:
            bom = d.setdefault("bom", {})
            bom["custom"] = [c for c in bom.get("custom", []) if c["id"] != cid]

        self.state.edit(tr("ui.bom_remove"), mutate)

    def add_line(self) -> None:
        dlg = AddLineDialog(self.state, self)
        if dlg.exec():
            line = dlg.line()
            self.state.edit(tr("ui.bom_add"), lambda d: d.setdefault("bom", {}).setdefault("custom", []).append(line))

    def reset_manual(self) -> None:
        if confirm(self, tr("ui.bom_reset"), tr("ui.bom_reset_text"), tr("ui.bom_reset"), danger=True):
            self.state.edit(tr("ui.bom_reset"), lambda d: d.update(bom={}))

    def _context(self, pos: QPoint) -> None:
        idx = self.tree.indexAt(pos)
        line: BomLine | None = idx.siblingAtColumn(0).data(LINE_ROLE) if idx.isValid() else None
        menu = QMenu(self)
        if line is not None and line.qty is not None:
            for col, key in ((COL_QTY, "ui.bom_edit_qty"), (COL_PRICE, "ui.bom_edit_price")):
                a = QAction(icons.icon("pencil", size=16), tr(key), menu)
                a.triggered.connect(lambda _=False, c=col: self.tree.edit(idx.siblingAtColumn(c)))
                menu.addAction(a)
            if line.manual and line.group != "custom":
                a = QAction(icons.icon("refresh-ccw", size=16), tr("ui.bom_revert"), menu)
                a.triggered.connect(lambda: self.revert(line))
                menu.addAction(a)
            if line.group == "custom":
                a = QAction(icons.icon("trash-2", "error", 16), tr("ui.bom_remove"), menu)
                a.triggered.connect(lambda: self.remove_custom(line))
                menu.addAction(a)
            dev = self.state.catalog.models.get(line.model)
            if dev is not None and dev.datasheet:
                url = dev.datasheet
                a = QAction(icons.icon("external-link", size=16), tr("ui.datasheet"), menu)
                a.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
                menu.addAction(a)
            menu.addSeparator()
        a = QAction(icons.icon("plus", size=16), tr("ui.bom_add"), menu)
        a.triggered.connect(self.add_line)
        menu.addAction(a)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _update_totals(self, result: SiteResult) -> None:
        clear_layout(self.totals_lay)
        s = self.state.settings
        summary = summarize_prices(
            result.bom, self.state.catalog.meta.currency, s.discount_pct, s.vat_pct if s.show_vat else 0
        )
        if summary is None:
            self.totals.hide()
            return
        self.totals.show()
        cur = summary.currency

        def metric(title: str, value: str, strong: bool = False) -> None:
            col = QVBoxLayout()
            col.setSpacing(px(2))
            col.addWidget(label(title, "caption"))
            col.addWidget(label(value, "metric" if strong else None))
            w = QWidget()
            w.setLayout(col)
            self.totals_lay.addWidget(w)

        metric(tr("price.subtotal"), format_money(summary.subtotal, cur))
        if summary.discount_pct:
            metric(tr("price.discount", pct=f"{summary.discount_pct:g}"), "− " + format_money(summary.discount, cur))
        if summary.vat_pct:
            metric(tr("price.vat", pct=f"{summary.vat_pct:g}"), format_money(summary.vat, cur))
        metric(tr("price.total"), format_money(summary.total, cur), strong=True)
        self.totals_lay.addStretch(1)
        if not summary.complete:
            self.totals_lay.addWidget(label(tr("price.incomplete", n=summary.unpriced_lines), "caption"))

    def _on_current(self, cur: QModelIndex, _prev: QModelIndex) -> None:
        line = cur.siblingAtColumn(0).data(LINE_ROLE) if cur.isValid() else None
        self.detail.show_line(line)

    def _on_search(self, text: str) -> None:
        self.proxy.setFilterFixedString(text.strip())
        for r in range(self.proxy.rowCount()):
            self.tree.setFirstColumnSpanned(r, QModelIndex(), True)
            self.tree.setExpanded(self.proxy.index(r, 0), True)

    def _toggle_expand(self) -> None:
        self._expanded = not self._expanded
        if self._expanded:
            self.tree.expandAll()
        else:
            self.tree.collapseAll()
        self.expand_btn.setText(tr("ui.collapse_all") if self._expanded else tr("ui.expand_all"))

    def copy_to_clipboard(self) -> None:
        result = self.shown()
        if result is None:
            return
        t = current()
        tsv = bom_tsv(result, t, any(line.unit_price is not None for line in result.bom))
        rows = [r.split("\t") for r in tsv.strip("\n").split("\n")]
        head = "".join(
            f"<th style='background:#1F4E78;color:#fff;padding:4px 8px;text-align:left'>{html.escape(c)}</th>"
            for c in rows[0]
        )
        body = "".join(
            "<tr>"
            + "".join(
                f"<td style='border:1px solid #B7B7B7;padding:4px 8px;vertical-align:top'>{html.escape(c)}</td>"
                for c in r
            )
            + "</tr>"
            for r in rows[1:]
        )
        mime = QMimeData()
        mime.setText(tsv)
        mime.setHtml(
            f"<table style='border-collapse:collapse;font-family:Segoe UI,Arial;font-size:10pt'>"
            f"<tr>{head}</tr>{body}</table>"
        )
        QApplication.clipboard().setMimeData(mime)
        self.state.message.emit("success", tr("ui.copied"))
