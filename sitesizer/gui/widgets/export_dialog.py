"""Export dialog: the user ticks what to produce (Excel sheets, PDF sections, pictures)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ...exporters.pdf import PDF_SECTIONS
from ...exporters.xlsx import SHEETS
from ...i18n import tr
from .controls import button, hline, label, px

DEFAULTS: dict[str, Any] = {
    "xlsx": True,
    "sheets": ["spec", "racks", "ip", "prices"],
    "only_used": False,
    "split": True,
    "pdf": False,
    "pdf_sections": list(PDF_SECTIONS),
    "png_topology": False,
    "png_racks": False,
}


class ExportDialog(QDialog):
    def __init__(self, parent: QWidget, choices: dict[str, Any], folder: str, name: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("ex.title"))
        self.setModal(True)
        c = {**DEFAULTS, **(choices or {})}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(24), px(20), px(24), px(18))
        lay.setSpacing(px(10))
        lay.addWidget(label(tr("ex.title"), "subtitle"))
        lay.addWidget(label(tr("ex.sub"), "muted", wrap=True))

        self.xlsx = QCheckBox(tr("ex.xlsx"))
        self.xlsx.setChecked(bool(c["xlsx"]))
        self.xlsx.setStyleSheet("font-weight: 600;")
        lay.addWidget(self.xlsx)
        grid = QGridLayout()
        grid.setContentsMargins(px(24), 0, 0, 0)
        grid.setHorizontalSpacing(px(18))
        self.sheets: dict[str, QCheckBox] = {}
        for i, key in enumerate(SHEETS):
            cb = QCheckBox(tr(f"ex.sheet.{key}"))
            cb.setChecked(key in c["sheets"])
            cb.setToolTip(tr(f"ex.sheet.{key}.tip"))
            grid.addWidget(cb, i // 2, i % 2)
            self.sheets[key] = cb
        self.only_used = QCheckBox(tr("ex.only_used"))
        self.only_used.setChecked(bool(c["only_used"]))
        grid.addWidget(self.only_used, (len(SHEETS) + 1) // 2, 0, 1, 2)
        self.split = QCheckBox(tr("ex.split"))
        self.split.setChecked(bool(c["split"]))
        self.split.setToolTip(tr("ex.split.tip"))
        grid.addWidget(self.split, (len(SHEETS) + 1) // 2 + 1, 0, 1, 2)
        lay.addLayout(grid)
        self.xlsx.toggled.connect(
            lambda on: [w.setEnabled(on) for w in (*self.sheets.values(), self.only_used, self.split)]
        )

        lay.addWidget(hline())
        self.pdf = QCheckBox(tr("ex.pdf"))
        self.pdf.setChecked(bool(c["pdf"]))
        self.pdf.setStyleSheet("font-weight: 600;")
        lay.addWidget(self.pdf)
        pgrid = QGridLayout()
        pgrid.setContentsMargins(px(24), 0, 0, 0)
        pgrid.setHorizontalSpacing(px(18))
        self.sections: dict[str, QCheckBox] = {}
        for i, key in enumerate(PDF_SECTIONS):
            cb = QCheckBox(tr(f"ex.pdf.{key}"))
            cb.setChecked(key in c["pdf_sections"])
            pgrid.addWidget(cb, i // 3, i % 3)
            self.sections[key] = cb
        lay.addLayout(pgrid)
        self.pdf.toggled.connect(lambda on: [w.setEnabled(on) for w in self.sections.values()])

        lay.addWidget(hline())
        lay.addWidget(label(tr("ex.images"), None))
        row = QHBoxLayout()
        row.setContentsMargins(px(24), 0, 0, 0)
        self.png_topology = QCheckBox(tr("ex.png_topology"))
        self.png_topology.setChecked(bool(c["png_topology"]))
        self.png_racks = QCheckBox(tr("ex.png_racks"))
        self.png_racks.setChecked(bool(c["png_racks"]))
        row.addWidget(self.png_topology)
        row.addWidget(self.png_racks)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(hline())
        frow = QHBoxLayout()
        frow.setSpacing(px(8))
        self.folder = QLineEdit(folder)
        self.folder.setMinimumWidth(px(320))
        browse = button(tr("ex.browse"), None, "folder-open")
        browse.clicked.connect(self._browse)
        frow.addWidget(label(tr("ex.folder")))
        frow.addWidget(self.folder, 1)
        frow.addWidget(browse)
        lay.addLayout(frow)
        nrow = QHBoxLayout()
        nrow.setSpacing(px(8))
        self.name = QLineEdit(name)
        nrow.addWidget(label(tr("ex.name")))
        nrow.addWidget(self.name, 1)
        lay.addLayout(nrow)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = button(tr("ui.cancel"))
        cancel.clicked.connect(self.reject)
        ok = button(tr("ex.go"), "primary", "download")
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        lay.addLayout(buttons)
        self.setMinimumWidth(px(560))
        for w in (*self.sheets.values(), self.only_used, self.split):
            w.setEnabled(self.xlsx.isChecked())
        for w in self.sections.values():
            w.setEnabled(self.pdf.isChecked())

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("ex.folder"), self.folder.text() or str(Path.home()))
        if path:
            self.folder.setText(path)

    def _accept(self) -> None:
        if not (
            self.xlsx.isChecked() or self.pdf.isChecked() or self.png_topology.isChecked() or self.png_racks.isChecked()
        ):
            return
        self.accept()

    def choices(self) -> dict[str, Any]:
        return {
            "xlsx": self.xlsx.isChecked(),
            "sheets": [k for k, cb in self.sheets.items() if cb.isChecked()],
            "only_used": self.only_used.isChecked(),
            "split": self.split.isChecked(),
            "pdf": self.pdf.isChecked(),
            "pdf_sections": [k for k, cb in self.sections.items() if cb.isChecked()],
            "png_topology": self.png_topology.isChecked(),
            "png_racks": self.png_racks.isChecked(),
        }

    def target(self) -> tuple[Path, str]:
        folder = Path(self.folder.text().strip() or Path.home())
        name = self.name.text().strip() or "LocalCount"
        return folder, name
