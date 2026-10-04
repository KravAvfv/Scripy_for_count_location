"""Projects view: project metadata, locations as cards, recent files."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...i18n import current, tr
from ..state import AppState
from ..theme import tokens
from ..widgets.controls import Card, FieldRow, Pill, button, clear_layout, icon_button, label, px


class SiteCard(QFrame):
    def __init__(self, view: ProjectsView, site_id: str) -> None:
        super().__init__()
        self.setObjectName("Card")
        state = view.state
        entry = state.project.site(site_id)
        assert entry is not None
        result = state.result_for(site_id)
        current_site = site_id == state.current_id
        if current_site:
            self.setStyleSheet(f"QFrame#Card {{ border: 1px solid {tokens().focus}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(16), px(14), px(14), px(12))
        lay.setSpacing(px(6))
        head = QHBoxLayout()
        t = current()
        tier = state.catalog.tier(entry.input.tier)
        colors = {1: tokens().error, 2: tokens().warning, 3: tokens().info, 4: tokens().text_faint}
        head.addWidget(Pill(f"{entry.input.tier} · {t.pick(tier.label)}", colors.get(entry.input.tier, tokens().info)))
        if entry.is_hub:
            head.addWidget(Pill("HQ", tokens().warning))
        head.addStretch(1)
        for icon_name, tip, action in (
            ("map-pin", tr("ui.site_open"), "select"),
            ("copy", tr("ui.site_duplicate"), "duplicate"),
            ("star", tr("ui.site_hub"), "hub"),
            ("trash-2", tr("ui.site_delete"), "delete"),
        ):
            b = icon_button(icon_name, tip, "error" if action == "delete" else None, size=16)
            b.setEnabled(
                not (action == "delete" and len(state.project.sites) <= 1) and not (action == "hub" and entry.is_hub)
            )
            b.clicked.connect(lambda _=False, a=action: view.request.emit(a, site_id))
            head.addWidget(b)
        lay.addLayout(head)
        name = label(entry.input.name or tr("ui.untitled"), "subtitle")
        lay.addWidget(name)
        if result is not None:
            fw = result.firewall
            lines = [
                tr("prj.line_fw", fw=f"{fw.model} × {fw.count}" if fw else "—"),
                tr(
                    "prj.line_counts",
                    sw=result.total_switches,
                    ap=result.counts.aps,
                    so=result.counts.sockets,
                    cam=result.counts.cameras,
                ),
            ]
            for text in lines:
                lay.addWidget(label(text, "muted"))
            sev = result.count_by_severity()
            from ...core.models import Severity

            if sev[Severity.ERROR] or sev[Severity.WARNING]:
                lay.addWidget(label(tr("prj.line_checks", e=sev[Severity.ERROR], w=sev[Severity.WARNING]), "caption"))
        open_btn = button(tr("prj.open"), "ghost", "arrow-right")
        open_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        open_btn.clicked.connect(
            lambda: (view.request.emit("select", site_id), view.request.emit("navigate", "location"))
        )
        lay.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignLeft)


class ProjectsView(QWidget):
    request = Signal(str, str)

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        root = QHBoxLayout(body)
        root.setContentsMargins(px(4), px(4), px(12), px(24))
        root.setSpacing(px(16))
        left = QVBoxLayout()
        left.setSpacing(px(16))
        right = QVBoxLayout()
        right.setSpacing(px(16))
        root.addLayout(left, 3)
        root.addLayout(right, 2)

        actions = QHBoxLayout()
        actions.setSpacing(px(8))
        for text, icon_name, act, variant in (
            (tr("ui.new_project"), "folder-plus", "new", None),
            (tr("ui.open_project"), "folder-open", "open", None),
            (tr("ui.save"), "save", "save", "primary"),
            (tr("ui.save_as"), "save", "save_as", None),
        ):
            b = button(text, variant, icon_name)
            b.clicked.connect(lambda _=False, a=act: self.request.emit(a, ""))
            actions.addWidget(b)
        actions.addStretch(1)
        left.addLayout(actions)

        head = QHBoxLayout()
        head.addWidget(label(tr("prj.sites"), "subtitle"), 1)
        add = button(tr("ui.site_add"), "ghost", "plus")
        add.clicked.connect(lambda: self.request.emit("add", ""))
        head.addWidget(add)
        left.addLayout(head)
        self.grid = QGridLayout()
        self.grid.setSpacing(px(14))
        left.addLayout(self.grid)
        left.addStretch(1)

        meta = Card(tr("prj.meta"), tr("prj.meta_sub"))
        self.name = QLineEdit()
        self.name.textEdited.connect(lambda v: self.state.update_project_meta(name=v))
        self.customer = QLineEdit()
        self.customer.textEdited.connect(lambda v: self.state.update_project_meta(customer=v))
        self.author = QLineEdit()
        self.author.textEdited.connect(lambda v: self.state.update_project_meta(author=v))
        for title, w in (
            (tr("prj.name"), self.name),
            (tr("prj.customer"), self.customer),
            (tr("prj.author"), self.author),
        ):
            w.setMinimumWidth(px(220))
            meta.add(FieldRow(title, w))
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText(tr("prj.notes_ph"))
        self.notes.setFixedHeight(px(90))
        self.notes.textChanged.connect(
            lambda: self.state.update_project_meta(notes=self.notes.toPlainText()) if self.notes.hasFocus() else None
        )
        meta.add(label(tr("prj.notes"), None))
        meta.add(self.notes)
        self.path_label = label("", "caption", wrap=True)
        meta.add(self.path_label)
        right.addWidget(meta)

        self.recent = Card(tr("prj.recent"), tr("prj.recent_sub"))
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(px(2))
        self.recent.add(self.recent_box)
        right.addWidget(self.recent)
        right.addStretch(1)

        state.projectChanged.connect(self.refresh)
        state.resultChanged.connect(lambda _r: self.refresh())
        state.settingsChanged.connect(self._refresh_recent)
        self.refresh()
        self._refresh_recent()

    def refresh(self) -> None:
        if not self.isVisible() and self.grid.count():
            self._stale = True
        p = self.state.project
        for w, v in ((self.name, p.name), (self.customer, p.customer), (self.author, p.author)):
            if not w.hasFocus():
                w.setText(v)
        if not self.notes.hasFocus() and self.notes.toPlainText() != p.notes:
            self.notes.blockSignals(True)
            self.notes.setPlainText(p.notes)
            self.notes.blockSignals(False)
        path = self.state.project_path
        self.path_label.setText(tr("prj.path", path=str(path)) if path else tr("prj.not_saved"))
        clear_layout(self.grid)
        for i, entry in enumerate(p.sites):
            self.grid.addWidget(SiteCard(self, entry.id), i // 2, i % 2)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh()

    def _refresh_recent(self) -> None:
        clear_layout(self.recent_box)
        items = [p for p in self.state.settings.recent_projects if p]
        if not items:
            self.recent_box.addWidget(label(tr("prj.recent_empty"), "muted", wrap=True))
            return
        for path in items:
            exists = Path(path).exists()
            b = button(Path(path).name, "ghost", "file-json", path)
            b.setStyleSheet("text-align: left;")
            b.setEnabled(exists)
            b.clicked.connect(lambda _=False, p=path: self.request.emit("open_path", p))
            self.recent_box.addWidget(b)
