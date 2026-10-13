"""The AP wiring scheme for the installers, opened from the topology page when it is needed."""

from __future__ import annotations

import contextlib
from collections.abc import Callable

from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QWidget

from ...core.ap_wiring import ApWiring, plan_ap_wiring
from ...exporters.ap_wiring import ApWiringDiagram
from ...exporters.diagram import style_from_tokens
from ...i18n import tr
from ..state import AppState
from ..theme import tokens
from .controls import SpinBox, button, icon_button, label, px, vline
from .diagram_view import DiagramCanvas


def location_wiring(state: AppState) -> ApWiring:
    """Every floor of the location (the current one when the location has a single floor)."""
    floors = []
    if state.location is not None and state.location.floors:
        for entry_id, r in state.location.floors:
            entry = state.project.site(entry_id)
            floors.append((entry.input.name if entry else "", r))
    elif state.result is not None:
        floors.append((state.site.name, state.result))
    return plan_ap_wiring(floors, state.catalog, state.settings.language)


class ApWiringDialog(QDialog):
    def __init__(self, state: AppState, export: Callable[[str], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.export = export
        self.setWindowTitle(tr("apw.title"))
        self.resize(px(1100), px(760))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(18), px(14), px(18), px(14))
        lay.setSpacing(px(10))
        lay.addWidget(label(tr("apw.title"), "subtitle"))
        lay.addWidget(label(tr("apw.sub"), "muted", wrap=True))
        bar = QHBoxLayout()
        bar.setSpacing(px(8))
        bar.addWidget(label(tr("apw.columns"), "caption"))
        self.columns = SpinBox()
        self.columns.setRange(0, 200)
        self.columns.setSpecialValueText(tr("apw.columns_line"))
        self.columns.setFixedWidth(px(120))
        self.columns.setToolTip(tr("apw.columns_tip"))
        self.columns.setValue(state.site.ap_columns)
        self.columns.valueChanged.connect(self._on_columns)
        bar.addWidget(self.columns)
        bar.addStretch(1)
        zoom_out = icon_button("zoom-out", tr("ui.zoom_out"))
        zoom_in = icon_button("zoom-in", tr("ui.zoom_in"))
        fit = icon_button("maximize", tr("ui.zoom_fit"))
        bar.addWidget(zoom_out)
        bar.addWidget(zoom_in)
        bar.addWidget(fit)
        bar.addWidget(vline())
        for fmt, text, icon in (
            ("apw_png", "PNG", "image"),
            ("apw_svg", "SVG", "download"),
            ("apw_xlsx", "Excel", "file-spreadsheet"),
        ):
            b = button(text, None, icon)
            b.clicked.connect(lambda _=False, f=fmt: self.export(f))
            bar.addWidget(b)
        lay.addLayout(bar)
        self.canvas = DiagramCanvas()
        lay.addWidget(self.canvas, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = button(tr("ui.close"), "primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        zoom_out.clicked.connect(lambda: self.canvas.zoom_by(1 / 1.2))
        zoom_in.clicked.connect(lambda: self.canvas.zoom_by(1.2))
        fit.clicked.connect(self.canvas.fit)
        state.resultChanged.connect(self.refresh)
        self.refresh()

    def _on_columns(self, value: int) -> None:
        if value != self.state.site.ap_columns:
            self.state.set_field("ap_columns", int(value), tr("apw.columns"))

    def refresh(self, *_a: object) -> None:
        if self.columns.value() != self.state.site.ap_columns and not self.columns.hasFocus():
            self.columns.blockSignals(True)
            self.columns.setValue(self.state.site.ap_columns)
            self.columns.blockSignals(False)
        diagram = ApWiringDiagram(
            location_wiring(self.state), self.state.settings.language, style_from_tokens(tokens())
        )
        self.canvas.set_diagram(diagram)  # type: ignore[arg-type]

    def done(self, r: int) -> None:
        with contextlib.suppress(RuntimeError, TypeError):
            self.state.resultChanged.disconnect(self.refresh)
        super().done(r)
