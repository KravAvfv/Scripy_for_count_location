"""Settings: theme, language, UI scale, report branding, pricing, defaults."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from ... import __version__
from ...i18n import tr
from ..state import AppState, app_data_dir
from ..widgets.controls import Card, DoubleSpinBox, FieldRow, SegmentedControl, ToggleRow, button, label, px


class SettingsView(QWidget):
    start_tour = Signal()

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
        root.addLayout(left, 1)
        root.addLayout(right, 1)
        s = state.settings

        look = Card(tr("set.look"), tr("set.look_sub"))
        self.theme = SegmentedControl(
            [("system", tr("set.theme_system")), ("light", tr("set.theme_light")), ("dark", tr("set.theme_dark"))],
            expand=False,
            compact=True,
        )
        self.theme.setValue(s.theme)
        self.theme.valueChanged.connect(lambda v: state.update_settings(theme=v))
        look.add(FieldRow(tr("set.theme"), self.theme))
        self.lang = SegmentedControl([("uk", "Українська"), ("en", "English")], expand=False, compact=True)
        self.lang.setValue(s.language)
        self.lang.valueChanged.connect(lambda v: state.update_settings(language=v))
        look.add(FieldRow(tr("set.language"), self.lang, tr("set.language_caption")))
        self.scale = SegmentedControl(
            [(0.9, "90%"), (1.0, "100%"), (1.1, "110%"), (1.25, "125%"), (1.4, "140%")], expand=False, compact=True
        )
        self.scale.setValue(min((0.9, 1.0, 1.1, 1.25, 1.4), key=lambda v: abs(v - s.ui_scale)))
        self.scale.valueChanged.connect(lambda v: state.update_settings(ui_scale=float(v)))
        look.add(FieldRow(tr("set.scale"), self.scale, tr("set.scale_caption")))
        left.addWidget(look)

        report = Card(tr("set.report"), tr("set.report_sub"))
        self.author = QLineEdit(s.author)
        self.author.setPlaceholderText(tr("set.author_ph"))
        self.author.textEdited.connect(lambda v: state.update_settings(author=v))
        self.company = QLineEdit(s.company)
        self.company.setPlaceholderText(tr("set.company_ph"))
        self.company.textEdited.connect(lambda v: state.update_settings(company=v))
        for w in (self.author, self.company):
            w.setMinimumWidth(px(240))
        report.add(FieldRow(tr("set.author"), self.author))
        report.add(FieldRow(tr("set.company"), self.company))
        logo_row = QHBoxLayout()
        self.logo = label(Path(s.logo_path).name if s.logo_path else tr("set.no_logo"), "muted")
        logo_row.addWidget(self.logo, 1)
        choose = button(tr("set.logo_choose"), None, "image")
        choose.clicked.connect(self._choose_logo)
        clear = button(tr("set.logo_clear"), "ghost")
        clear.clicked.connect(lambda: (state.update_settings(logo_path=""), self.logo.setText(tr("set.no_logo"))))
        logo_row.addWidget(choose)
        logo_row.addWidget(clear)
        report.add(label(tr("set.logo"), None))
        report.add(logo_row)
        left.addWidget(report)

        price = Card(tr("set.pricing"), tr("set.pricing_sub", cur=state.catalog.meta.currency))
        self.vat_on = ToggleRow(tr("set.vat_on"), tr("set.vat_caption"))
        self.vat_on.setChecked(s.show_vat)
        self.vat_on.toggled.connect(lambda v: state.update_settings(show_vat=v))
        price.add(self.vat_on)
        self.vat = DoubleSpinBox()
        self.vat.setRange(0, 50)
        self.vat.setSuffix(" %")
        self.vat.setValue(s.vat_pct)
        self.vat.setFixedWidth(px(110))
        self.vat.valueChanged.connect(lambda v: state.update_settings(vat_pct=float(v)))
        price.add(FieldRow(tr("set.vat"), self.vat))
        self.discount = DoubleSpinBox()
        self.discount.setRange(0, 100)
        self.discount.setSuffix(" %")
        self.discount.setValue(s.discount_pct)
        self.discount.setFixedWidth(px(110))
        self.discount.valueChanged.connect(lambda v: state.update_settings(discount_pct=float(v)))
        price.add(FieldRow(tr("set.discount"), self.discount, tr("set.discount_caption")))
        right.addWidget(price)

        defaults = Card(tr("set.defaults"), tr("set.defaults_sub"))
        self.fortios = QLineEdit(s.fortios_default)
        self.fortios.setFixedWidth(px(110))
        self.fortios.editingFinished.connect(lambda: state.update_settings(fortios_default=self.fortios.text().strip()))
        defaults.add(FieldRow(tr("set.fortios"), self.fortios, tr("set.fortios_caption")))
        right.addWidget(defaults)

        about = Card(tr("set.about"), tr("set.about_sub", v=__version__))
        row = QHBoxLayout()
        tour = button(tr("ui.tour_start"), None, "sparkles")
        tour.clicked.connect(self.start_tour)
        logs = button(tr("set.open_data"), "ghost", "folder-open")
        logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_data_dir()))))
        row.addWidget(tour)
        row.addWidget(logs)
        row.addStretch(1)
        about.add(row)
        about.add(label(tr("set.shortcuts"), "caption", wrap=True))
        right.addWidget(about)
        left.addStretch(1)
        right.addStretch(1)

    def _choose_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("set.logo_choose"), str(Path.home()), "Images (*.png *.jpg *.jpeg *.svg)"
        )
        if path:
            self.state.update_settings(logo_path=path)
            self.logo.setText(Path(path).name)
