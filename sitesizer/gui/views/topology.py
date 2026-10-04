"""Topology view: live diagram with pan/zoom, single-site or whole-project, PNG/SVG export."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from ...exporters.diagram import Diagram, ProjectDiagram, SiteDiagram, style_from_tokens
from ...i18n import tr
from ..state import AppState
from ..theme import theme_manager, tokens
from ..widgets.controls import SegmentedControl, button, icon_button, label, px, vline
from ..widgets.diagram_view import DiagramCanvas


class TopologyView(QWidget):
    export_requested = Signal(str)  # "png" | "svg"

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(px(12))
        bar = QHBoxLayout()
        bar.setSpacing(px(8))
        self.scope = SegmentedControl(
            [("site", tr("ui.topo_site")), ("project", tr("ui.topo_project"))],
            [tr("ui.topo_site_tip"), tr("ui.topo_project_tip")],
            expand=False,
        )
        self.scope.valueChanged.connect(lambda _v: self.refresh())
        bar.addWidget(self.scope)
        bar.addStretch(1)
        self.zoom_out = icon_button("zoom-out", tr("ui.zoom_out"))
        self.zoom_label = label("100%", "caption")
        self.zoom_label.setMinimumWidth(px(44))
        self.zoom_in = icon_button("zoom-in", tr("ui.zoom_in"))
        self.fit_btn = icon_button("maximize", tr("ui.zoom_fit"))
        for w in (self.zoom_out, self.zoom_label, self.zoom_in, self.fit_btn):
            bar.addWidget(w)
        bar.addWidget(vline())
        png = button("PNG", None, "image", tr("ui.export_png"))
        svg = button("SVG", None, "download", tr("ui.export_svg"))
        png.clicked.connect(lambda: self.export_requested.emit("png"))
        svg.clicked.connect(lambda: self.export_requested.emit("svg"))
        bar.addWidget(png)
        bar.addWidget(svg)
        root.addLayout(bar)

        frame = QFrame()
        frame.setObjectName("Card")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(1, 1, 1, 1)
        self.canvas = DiagramCanvas()
        fl.addWidget(self.canvas)
        root.addWidget(frame, 1)
        root.addWidget(label(tr("ui.topo_hint"), "caption"))

        self.zoom_out.clicked.connect(lambda: self.canvas.zoom_by(1 / 1.2))
        self.zoom_in.clicked.connect(lambda: self.canvas.zoom_by(1.2))
        self.fit_btn.clicked.connect(self.canvas.fit)
        self.canvas.zoomChanged.connect(lambda z: self.zoom_label.setText(f"{round(z * 100)}%"))
        state.resultChanged.connect(self._on_result)
        state.projectChanged.connect(self.refresh)
        theme_manager.changed.connect(self._on_theme)

    def _on_theme(self) -> None:
        self.canvas.apply_theme()
        self.refresh()

    def build_diagram(self, export: bool = False) -> Diagram | None:
        style = None if export else style_from_tokens(tokens())
        kwargs = {"style": style} if style else {}
        lang = self.state.settings.language
        if self.scope.value() == "project":
            entries = []
            for entry in self.state.project.sites:
                r = self.state.result_for(entry.id)
                if r is not None:
                    entries.append((entry.input.name, entry.is_hub, r))
            return ProjectDiagram(
                entries, self.state.catalog, lang, title=self.state.project.name if export else "", **kwargs
            )
        if self.state.result is None:
            return None
        return SiteDiagram(self.state.result, self.state.catalog, lang, title=export, **kwargs)

    def _on_result(self, _result: object) -> None:
        self.refresh()

    def refresh(self) -> None:
        self.canvas.set_diagram(self.build_diagram())
