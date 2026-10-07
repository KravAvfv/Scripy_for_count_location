"""Application chrome: sidebar (navigation + locations), top bar and the live summary bar."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core.models import Severity, SiteResult
from ...core.pricing import format_money, summarize_prices
from ...exporters.diagram import paint_brand
from ...i18n import tr
from .. import icons
from ..state import AppState
from ..theme import theme_manager, tokens
from .controls import button, icon_button, label, px

NAV_MAIN = [
    ("location", "map-pin", "nav.location"),
    ("racks", "server", "nav.racks"),
    ("ipplan", "binary", "nav.ipplan"),
    ("bom", "clipboard-list", "nav.bom"),
    ("topology", "network", "nav.topology"),
    ("power", "zap", "nav.power"),
    ("compare", "git-compare", "nav.compare"),
]
STEPS = ("location", "racks", "ipplan", "bom")
"""The main workflow, numbered in the sidebar and ticked off when complete."""
NAV_BOTTOM = [
    ("projects", "folder-open", "nav.projects"),
    ("catalog", "database", "nav.catalog"),
    ("help", "circle-help", "nav.help"),
    ("settings", "settings", "nav.settings"),
]


class BrandMark(QWidget):
    """Small painted logo: navy rounded square with a three-node topology glyph."""

    def __init__(self, size: int = 30, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(px(size), px(size))

    def paintEvent(self, _e: QPaintEvent) -> None:
        paint_brand(QPainter(self), QRectF(self.rect()))


class SiteItem(QPushButton):
    """A location entry in the sidebar list."""

    context = Signal(str, QPoint)

    def __init__(self, site_id: str, name: str, tier: int, hub: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.site_id = site_id
        self.name = name
        self.tier = tier
        self.hub = hub
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(px(32))
        self.setToolTip(name)
        self.setAccessibleName(name)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self.context.emit(self.site_id, self.mapToGlobal(pos)))

    def paintEvent(self, _e: QPaintEvent) -> None:
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.q("primary_soft"))
            p.drawRoundedRect(r, px(7), px(7))
        elif self.underMouse():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.q("surface_hover"))
            p.drawRoundedRect(r, px(7), px(7))
        if self.hasFocus():
            p.setPen(QPen(t.q("focus"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, px(7), px(7))
        tier_colors = {1: t.error, 2: t.warning, 3: t.info, 4: t.text_faint}
        dot = QRectF(r.left() + px(10), r.center().y() - px(4), px(8), px(8))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(tier_colors.get(self.tier, t.text_faint)))
        p.drawEllipse(dot)
        x = dot.right() + px(10)
        right = r.right() - px(8)
        if self.hub:
            star = icons.pixmap("star", px(13), "warning")
            p.drawPixmap(int(right - px(13)), int(r.center().y() - px(6.5)), star)
            right -= px(18)
        p.setPen(t.q("primary_soft_text" if self.isChecked() else "text"))
        f = theme_manager.font(None, QFont.Weight.DemiBold if self.isChecked() else QFont.Weight.Medium)
        p.setFont(f)
        from PySide6.QtGui import QFontMetrics

        text = QFontMetrics(f).elidedText(self.name, Qt.TextElideMode.ElideRight, int(right - x))
        p.drawText(QRectF(x, r.top(), right - x, r.height()), Qt.AlignmentFlag.AlignVCenter, text)
        p.end()

    def enterEvent(self, e) -> None:
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self.update()
        super().leaveEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        self.context.emit(self.site_id, QPoint(-1, -1))


class Sidebar(QFrame):
    navigate = Signal(str)
    site_action = Signal(str, str)  # action, site id

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.setObjectName("Sidebar")
        self.setFixedWidth(px(236))
        root = QVBoxLayout(self)
        root.setContentsMargins(px(12), px(14), px(12), px(12))
        root.setSpacing(px(2))

        brand = QHBoxLayout()
        brand.setSpacing(px(10))
        brand.addWidget(BrandMark(30))
        col = QVBoxLayout()
        col.setSpacing(0)
        self.app_name = label("LocalCount")
        self.app_name.setStyleSheet(f"font-weight: 700; font-size: {px(15)}px;")
        self.project_label = label("", "faint")
        col.addWidget(self.app_name)
        col.addWidget(self.project_label)
        brand.addLayout(col, 1)
        root.addLayout(brand)
        root.addSpacing(px(16))

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        self.done: set[str] = set()
        root.addWidget(label(tr("ui.steps").upper(), "section"))
        for key, icon_name, text_key in NAV_MAIN:
            if key == STEPS[-1]:
                root.addWidget(self._nav_button(key, icon_name, self._nav_text(key, text_key)))
                root.addSpacing(px(10))
                root.addWidget(label(tr("ui.views").upper(), "section"))
                continue
            root.addWidget(self._nav_button(key, icon_name, self._nav_text(key, text_key)))

        root.addSpacing(px(16))
        head = QHBoxLayout()
        head.setContentsMargins(px(10), 0, 0, 0)
        head.addWidget(label(tr("ui.sites").upper(), "section"))
        head.addStretch(1)
        add = icon_button("plus", tr("ui.site_add"), size=15)
        add.clicked.connect(lambda: self.site_action.emit("add", ""))
        head.addWidget(add)
        root.addLayout(head)
        root.addSpacing(px(4))
        self.sites_scroll = QScrollArea()
        self.sites_scroll.setWidgetResizable(True)
        self.sites_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sites_box = QWidget()
        self.sites_lay = QVBoxLayout(self.sites_box)
        self.sites_lay.setContentsMargins(0, 0, 0, 0)
        self.sites_lay.setSpacing(px(2))
        self.sites_lay.addStretch(1)
        self.sites_scroll.setWidget(self.sites_box)
        root.addWidget(self.sites_scroll, 1)
        self.site_group = QButtonGroup(self)
        self.site_group.setExclusive(True)
        root.addSpacing(px(8))
        for key, icon_name, text_key in NAV_BOTTOM:
            root.addWidget(self._nav_button(key, icon_name, tr(text_key)))

        state.projectChanged.connect(self.refresh_sites)
        state.siteChanged.connect(self._refresh_names)
        theme_manager.changed.connect(self._retint)
        self.refresh_sites()

    def _nav_button(self, key: str, icon_name: str, text: str) -> QPushButton:
        b = QPushButton(text.replace("&", "&&"))
        b.setObjectName("NavItem")
        b.setCheckable(True)
        b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setIcon(icons.icon(icon_name, "text_muted", 17, active_color="primary_soft_text"))
        b.setIconSize(QSize(px(17), px(17)))
        b.setProperty("nav_icon", icon_name)
        b.setAccessibleName(text)
        b.clicked.connect(lambda: self.navigate.emit(key))
        self.group.addButton(b)
        self.buttons[key] = b
        return b

    @staticmethod
    def _nav_text(key: str, text_key: str) -> str:
        return f"{STEPS.index(key) + 1}. {tr(text_key)}" if key in STEPS else tr(text_key)

    def _retint(self) -> None:
        for key, b in self.buttons.items():
            if key in self.done:
                b.setIcon(icons.icon("circle-check", "success", 17))
            else:
                b.setIcon(icons.icon(str(b.property("nav_icon")), "text_muted", 17, active_color="primary_soft_text"))
        self.refresh_sites()

    def set_progress(self, done: set[str]) -> None:
        """Tick off the workflow steps that are complete."""
        if done == self.done:
            return
        self.done = done
        for key in STEPS:
            b = self.buttons.get(key)
            if b is None:
                continue
            if key in done:
                b.setIcon(icons.icon("circle-check", "success", 17))
                b.setToolTip(tr("ui.step_done"))
            else:
                b.setIcon(icons.icon(str(b.property("nav_icon")), "text_muted", 17, active_color="primary_soft_text"))
                b.setToolTip(tr(f"ui.step_todo.{key}"))

    def set_current(self, key: str) -> None:
        b = self.buttons.get(key)
        if b:
            b.setChecked(True)

    def refresh_sites(self) -> None:
        for b in self.site_group.buttons():
            self.site_group.removeButton(b)
            b.hide()
            b.deleteLater()
        while self.sites_lay.count() > 1:
            item = self.sites_lay.takeAt(0)
            if item and item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        for entry in self.state.project.sites:
            item = SiteItem(entry.id, entry.input.name or tr("ui.untitled"), entry.input.tier, entry.is_hub)
            item.setChecked(entry.id == self.state.current_id)
            item.clicked.connect(lambda _=False, sid=entry.id: self.site_action.emit("select", sid))
            item.context.connect(self._site_menu)
            self.site_group.addButton(item)
            self.sites_lay.insertWidget(self.sites_lay.count() - 1, item)
        name = self.state.project.name
        self.project_label.setText(name)
        self.project_label.setToolTip(name)

    def _refresh_names(self) -> None:
        for b in self.site_group.buttons():
            if isinstance(b, SiteItem):
                entry = self.state.project.site(b.site_id)
                if entry and (entry.input.name != b.name or entry.input.tier != b.tier):
                    b.name, b.tier = entry.input.name or tr("ui.untitled"), entry.input.tier
                    b.setToolTip(b.name)
                    b.update()

    def _site_menu(self, site_id: str, pos: QPoint) -> None:
        if pos.x() < 0:
            self.site_action.emit("select", site_id)
            self.navigate.emit("location")
            return
        menu = QMenu(self)
        actions = [
            ("select", "map-pin", tr("ui.site_open")),
            ("duplicate", "copy", tr("ui.site_duplicate")),
            ("hub", "star", tr("ui.site_hub")),
            ("up", "chevron-left", tr("ui.site_up")),
            ("down", "chevron-right", tr("ui.site_down")),
            (None, None, None),
            ("delete", "trash-2", tr("ui.site_delete")),
        ]
        for key, icon_name, text in actions:
            if key is None:
                menu.addSeparator()
                continue
            act = QAction(icons.icon(icon_name or "", "error" if key == "delete" else None, 16), text or "", menu)
            act.setEnabled(not (key == "delete" and len(self.state.project.sites) <= 1))
            act.triggered.connect(lambda _=False, k=key: self.site_action.emit(k, site_id))
            menu.addAction(act)
        menu.exec(pos)

    def set_compact(self, compact: bool) -> None:
        self.setFixedWidth(px(64) if compact else px(236))
        for key, b in self.buttons.items():
            text_key = next(t for k, _i, t in NAV_MAIN + NAV_BOTTOM if k == key)
            b.setText("" if compact else self._nav_text(key, text_key).replace("&", "&&"))
            if compact:
                b.setToolTip(tr(text_key))
        self.app_name.setVisible(not compact)
        self.project_label.setVisible(not compact)
        self.sites_scroll.setVisible(not compact)
        for w in self.findChildren(QLabel):
            if w.property("role") == "section":
                w.setVisible(not compact)


class TopBar(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(px(28), px(18), px(24), px(10))
        lay.setSpacing(px(8))
        col = QVBoxLayout()
        col.setSpacing(px(2))
        self.title = label("", "display")
        self.subtitle = label("", "caption")
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        lay.addLayout(col, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(px(6))
        lay.addLayout(self.actions)

    def add(self, w: QWidget) -> None:
        self.actions.addWidget(w)


class SummaryItem(QWidget):
    def __init__(self, icon_name: str, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon_name = icon_name
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(px(8))
        self.icon = QLabel()
        lay.addWidget(self.icon)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.caption = label(caption, "faint")
        self.value = label("—")
        self.value.setStyleSheet("font-weight: 600;")
        col.addWidget(self.caption)
        col.addWidget(self.value)
        lay.addLayout(col)
        self.retint()

    def retint(self) -> None:
        self.icon.setPixmap(icons.pixmap(self.icon_name, px(16), "text_muted"))

    def set(self, value: str, tooltip: str = "") -> None:
        self.value.setText(value)
        self.setToolTip(tooltip)


class SummaryBar(QFrame):
    open_checks = Signal()

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.setObjectName("SummaryBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(px(28), px(8), px(20), px(8))
        lay.setSpacing(px(28))
        self.fw = SummaryItem("shield", tr("sum.firewall"))
        self.sw = SummaryItem("network", tr("sum.switches"))
        self.ap = SummaryItem("wifi", tr("sum.aps"))
        self.poe = SummaryItem("plug", tr("sum.poe"))
        self.power = SummaryItem("zap", tr("sum.power"))
        self.rack = SummaryItem("server", tr("sum.rack"))
        self.cost = SummaryItem("briefcase", tr("sum.cost"))
        self.items = [self.fw, self.sw, self.ap, self.poe, self.power, self.rack, self.cost]
        for it in self.items:
            lay.addWidget(it)
        lay.addStretch(1)
        self.errors = self._counter("circle-alert", "error")
        self.warnings = self._counter("triangle-alert", "warning")
        self.infos = self._counter("info", "info")
        for b in (self.errors, self.warnings, self.infos):
            lay.addWidget(b)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._compact = False
        state.resultChanged.connect(self.on_result)
        state.settingsChanged.connect(self._on_settings)
        theme_manager.changed.connect(self._retint)

    def _on_settings(self) -> None:
        if self.state.result is not None:
            self.on_result(self.state.result)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        compact = self.width() < px(1100)
        if compact != self._compact:
            self._compact = compact
            self.rack.setVisible(not compact)
            self.poe.setVisible(not compact)
            if self.state.result is not None:
                self.on_result(self.state.result)

    def _counter(self, icon_name: str, color: str) -> QPushButton:
        b = button("0", "ghost")
        b.setIcon(icons.icon(icon_name, color, 16))
        b.setProperty("icon_name", icon_name)
        b.setProperty("icon_color", color)
        b.clicked.connect(self.open_checks)
        return b

    def _retint(self) -> None:
        for it in self.items:
            it.retint()
        for b in (self.errors, self.warnings, self.infos):
            b.setIcon(icons.icon(str(b.property("icon_name")), str(b.property("icon_color")), 16))

    def on_result(self, r: SiteResult) -> None:
        fw = r.firewall
        self.fw.set(f"{fw.model} × {fw.count}" if fw and fw.fits else ("—" if not fw else tr("ui.m_fw_none")))
        self.sw.set(str(r.total_switches))
        self.ap.set(str(r.counts.aps))
        self.poe.set(f"{r.power.poe_w:,.0f}".replace(",", " ") + tr("ui.unit_w"))
        self.power.set(
            f"{r.power.total_w:,.0f}".replace(",", " ") + tr("ui.unit_w"), tr("ui.m_power_sub", va=r.power.ups_va)
        )
        self.rack.set(f"{r.rack.units_with_spare} U")
        s = self.state.settings
        summary = summarize_prices(
            r.bom, self.state.catalog.meta.currency, s.discount_pct, s.vat_pct if s.show_vat else 0
        )
        self.cost.setVisible(summary is not None and not self._compact)
        if summary:
            self.cost.set(format_money(summary.total, summary.currency))
        counts = r.count_by_severity()
        for b, sev in ((self.errors, Severity.ERROR), (self.warnings, Severity.WARNING), (self.infos, Severity.INFO)):
            n = counts[sev]
            b.setText(str(n))
            b.setVisible(n > 0 or sev == Severity.WARNING)
            b.setToolTip(tr(f"sum.{sev.value}", n=n))
