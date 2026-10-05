"""Design tokens, light/dark themes and the generated Qt stylesheet.

Every colour used in the UI comes from :class:`Tokens`; custom-painted widgets read them from
:func:`tokens` and repaint on :data:`theme_manager.changed`. The stylesheet is generated from the
same tokens so QSS-styled and painted widgets always match.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent.parent / "assets"
FONT_FAMILY = "Inter"
FALLBACK_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Noto Sans", "Arial"]


@dataclass(frozen=True)
class Tokens:
    name: str
    bg: str
    sidebar: str
    surface: str
    surface_alt: str
    surface_hover: str
    surface_sunken: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    text_faint: str
    primary: str
    primary_hover: str
    primary_pressed: str
    primary_text: str
    primary_soft: str
    primary_soft_text: str
    focus: str
    success: str
    success_bg: str
    warning: str
    warning_bg: str
    error: str
    error_bg: str
    info: str
    info_bg: str
    shadow: str
    scrollbar: str
    selection: str
    # diagram (in-app; exports always use LIGHT)
    dia_bg: str
    dia_dark: str
    dia_dark_text: str
    dia_light: str
    dia_light_edge: str
    dia_light_text: str
    dia_line: str
    dia_muted: str
    dia_wan: str
    categories: dict[str, str] = field(default_factory=dict)

    @property
    def dark(self) -> bool:
        return self.name == "dark"

    def q(self, attr: str, alpha: float | None = None) -> QColor:
        """QColor for a token name (or a literal ``#hex``), optionally with alpha 0..1."""
        value = attr if attr.startswith("#") else getattr(self, attr)
        c = QColor(value)
        if alpha is not None:
            c.setAlphaF(alpha)
        return c

    def category(self, group: str) -> QColor:
        return QColor(self.categories.get(group, self.text_faint))


CATEGORY_COLORS_LIGHT = {
    "ap": "#3E7CB1",
    "wifi_switch": "#4F8A74",
    "access_switch": "#5F72A0",
    "camera_switch": "#A0784A",
    "core_switch": "#1F4E78",
    "firewall": "#94505B",
    "power": "#857637",
    "transceiver": "#4E8592",
    "cabling": "#717E8B",
    "fiber": "#3F8F8A",
    "rack": "#6A6690",
    "license": "#4A7A64",
    "spare": "#86688A",
    "reference": "#8A96A3",
}
CATEGORY_COLORS_DARK = {
    "ap": "#6FA3D2",
    "wifi_switch": "#78B49C",
    "access_switch": "#8D9DC6",
    "camera_switch": "#CFA272",
    "core_switch": "#7FA9D3",
    "firewall": "#C98591",
    "power": "#C2B26C",
    "transceiver": "#7FB4C1",
    "cabling": "#A3AFBA",
    "fiber": "#79C2BC",
    "rack": "#A39FC6",
    "license": "#7DB298",
    "spare": "#BB9BBE",
    "reference": "#8E9AA6",
}

LIGHT = Tokens(
    name="light",
    bg="#F4F6F9",
    sidebar="#EDF1F5",
    surface="#FFFFFF",
    surface_alt="#EAF0F6",
    surface_hover="#F1F5F9",
    surface_sunken="#F7F9FB",
    border="#DCE3EA",
    border_strong="#B7C4D1",
    text="#1F2D3A",
    text_muted="#5F6C79",
    text_faint="#7C8894",
    primary="#1F4E78",
    primary_hover="#255A88",
    primary_pressed="#1A4266",
    primary_text="#FFFFFF",
    primary_soft="#E1EAF3",
    primary_soft_text="#1F4E78",
    focus="#5B8FC7",
    success="#2F7A55",
    success_bg="#E7F2EC",
    warning="#93651A",
    warning_bg="#FAF1DF",
    error="#A3433B",
    error_bg="#F8E7E5",
    info="#2F6690",
    info_bg="#E5EEF6",
    shadow="#101828",
    scrollbar="#C5D0DB",
    selection="#D6E3F0",
    dia_bg="#FFFFFF",
    dia_dark="#1F4E78",
    dia_dark_text="#FFFFFF",
    dia_light="#EAF0F6",
    dia_light_edge="#B7C4D1",
    dia_light_text="#1F2D3A",
    dia_line="#9AA7B4",
    dia_muted="#7C8894",
    dia_wan="#4C9A6A",
    categories=CATEGORY_COLORS_LIGHT,
)

DARK = Tokens(
    name="dark",
    bg="#111519",
    sidebar="#14191E",
    surface="#1A2027",
    surface_alt="#222A33",
    surface_hover="#232B35",
    surface_sunken="#161B21",
    border="#2B3540",
    border_strong="#3A4653",
    text="#E3E8ED",
    text_muted="#9AA6B2",
    text_faint="#7D8995",
    primary="#3A6C9C",
    primary_hover="#4479AB",
    primary_pressed="#33618D",
    primary_text="#FFFFFF",
    primary_soft="#1F2E3E",
    primary_soft_text="#A9C8E8",
    focus="#6E9FD2",
    success="#74C095",
    success_bg="#18271F",
    warning="#D7A852",
    warning_bg="#2A2317",
    error="#E3827A",
    error_bg="#2E1C1B",
    info="#7EADD9",
    info_bg="#172534",
    shadow="#000000",
    scrollbar="#36414C",
    selection="#26394D",
    dia_bg="#1A2027",
    dia_dark="#2D5D8A",
    dia_dark_text="#FFFFFF",
    dia_light="#222A33",
    dia_light_edge="#3A4653",
    dia_light_text="#E3E8ED",
    dia_line="#5C6977",
    dia_muted="#9AA6B2",
    dia_wan="#6FB58A",
    categories=CATEGORY_COLORS_DARK,
)


@dataclass(frozen=True)
class Typography:
    """Type scale in pixels (before UI scaling)."""

    caption: int = 12
    body: int = 13
    subtitle: int = 15
    title: int = 18
    display: int = 24


class ThemeManager(QObject):
    """Holds the active tokens and scale; emits :attr:`changed` when they change."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.mode = "system"  # system | light | dark
        self.scale = 1.0
        self._tokens = LIGHT
        self.type = Typography()

    @property
    def tokens(self) -> Tokens:
        return self._tokens

    def px(self, value: float) -> int:
        return round(value * self.scale)

    def resolve(self) -> Tokens:
        if self.mode == "light":
            return LIGHT
        if self.mode == "dark":
            return DARK
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme() if hints is not None else Qt.ColorScheme.Unknown
        return DARK if scheme == Qt.ColorScheme.Dark else LIGHT

    def apply(self, mode: str | None = None, scale: float | None = None) -> None:
        if mode is not None:
            self.mode = mode
        if scale is not None:
            self.scale = max(0.8, min(1.6, scale))
        self._tokens = self.resolve()
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setPalette(build_palette(self._tokens))
            font = app_font(self.px(self.type.body))
            app.setFont(font)
            app.setStyleSheet(build_stylesheet(self._tokens, self))
        self.changed.emit()

    def font(self, size: int | None = None, weight: QFont.Weight = QFont.Weight.Normal, italic: bool = False) -> QFont:
        f = app_font(self.px(size or self.type.body))
        f.setWeight(weight)
        f.setItalic(italic)
        return f


theme_manager = ThemeManager()


def tokens() -> Tokens:
    return theme_manager.tokens


def load_fonts() -> str:
    """Register bundled Inter; return the family name actually available."""
    fonts_dir = ASSETS / "fonts"
    loaded = False
    for path in sorted(fonts_dir.glob("*.ttf")):
        if QFontDatabase.addApplicationFont(str(path)) >= 0:
            loaded = True
        else:
            log.warning("Could not load font %s", path.name)
    families = QFontDatabase.families()
    if loaded and FONT_FAMILY in families:
        return FONT_FAMILY
    for fam in FALLBACK_FAMILIES:
        if fam in families:
            return fam
    return QFont().defaultFamily()


_family = FONT_FAMILY


def set_font_family(family: str) -> None:
    global _family
    _family = family


def app_font(pixel_size: int) -> QFont:
    f = QFont(_family)
    f.setFamilies([_family, *FALLBACK_FAMILIES])
    f.setPixelSize(pixel_size)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


def build_palette(t: Tokens) -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: t.bg,
        QPalette.ColorRole.WindowText: t.text,
        QPalette.ColorRole.Base: t.surface,
        QPalette.ColorRole.AlternateBase: t.surface_sunken,
        QPalette.ColorRole.ToolTipBase: t.surface,
        QPalette.ColorRole.ToolTipText: t.text,
        QPalette.ColorRole.PlaceholderText: t.text_faint,
        QPalette.ColorRole.Text: t.text,
        QPalette.ColorRole.Button: t.surface,
        QPalette.ColorRole.ButtonText: t.text,
        QPalette.ColorRole.BrightText: t.error,
        QPalette.ColorRole.Highlight: t.selection,
        QPalette.ColorRole.HighlightedText: t.text,
        QPalette.ColorRole.Link: t.primary if not t.dark else t.info,
        QPalette.ColorRole.Light: t.surface_hover,
        QPalette.ColorRole.Midlight: t.border,
        QPalette.ColorRole.Mid: t.border_strong,
        QPalette.ColorRole.Dark: t.border_strong,
        QPalette.ColorRole.Shadow: t.shadow,
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(t.text_faint))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(t.text_faint))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(t.text_faint))
    return p


def _rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha:.3f})"


def build_stylesheet(t: Tokens, tm: ThemeManager) -> str:
    """The global QSS. Variants are selected with dynamic properties (``variant``, ``role``)."""
    px = tm.px
    ty = tm.type
    r_input = px(6)
    r_card = px(10)
    from .icons import icon_file  # local import: icons depends on this module

    chevron = icon_file("chevron-down", t.text_muted)
    check = icon_file("check", "#FFFFFF", "3")
    return f"""
* {{ outline: none; }}
QWidget {{ color: {t.text}; font-size: {px(ty.body)}px; }}
QMainWindow, #AppRoot {{ background: {t.bg}; }}
QToolTip {{
    background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong};
    border-radius: {px(6)}px; padding: {px(6)}px {px(8)}px; font-size: {px(ty.caption)}px;
}}

/* ---------- typography roles ---------- */
QLabel[role="display"] {{ font-size: {px(ty.display)}px; font-weight: 600; }}
QLabel[role="title"] {{ font-size: {px(ty.title)}px; font-weight: 600; }}
QLabel[role="subtitle"] {{ font-size: {px(ty.subtitle)}px; font-weight: 600; }}
QLabel[role="section"] {{
    font-size: {px(ty.caption)}px; font-weight: 600; color: {t.text_muted};
    letter-spacing: 0.4px; text-transform: uppercase;
}}
QLabel[role="muted"] {{ color: {t.text_muted}; }}
QLabel[role="caption"] {{ color: {t.text_muted}; font-size: {px(ty.caption)}px; }}
QLabel[role="faint"] {{ color: {t.text_faint}; font-size: {px(ty.caption)}px; }}
QLabel[role="metric"] {{ font-size: {px(ty.title)}px; font-weight: 600; }}
QLabel[role="link"] {{ color: {t.primary if not t.dark else t.info}; }}

/* ---------- surfaces ---------- */
#Sidebar {{ background: {t.sidebar}; border-right: 1px solid {t.border}; }}
#TopBar {{ background: {t.bg}; }}
#SummaryBar {{ background: {t.surface}; border-top: 1px solid {t.border}; }}
QFrame#Card, QWidget#Card {{
    background: {t.surface}; border: 1px solid {t.border}; border-radius: {r_card}px;
}}
QFrame#SoftCard {{ background: {t.surface_alt}; border: none; border-radius: {r_card}px; }}
QFrame#Divider {{ background: {t.border}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#VDivider {{ background: {t.border}; max-width: 1px; min-width: 1px; border: none; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QStackedWidget {{ background: transparent; }}

/* ---------- sidebar nav ---------- */
QPushButton#NavItem {{
    text-align: left; padding: {px(7)}px {px(10)}px; border: none; border-radius: {px(7)}px;
    background: transparent; color: {t.text_muted}; font-weight: 500;
}}
QPushButton#NavItem:hover {{ background: {t.surface_hover}; color: {t.text}; }}
QPushButton#NavItem:checked {{ background: {t.primary_soft}; color: {t.primary_soft_text}; font-weight: 600; }}
QPushButton#NavItem:focus {{ border: 1px solid {t.focus}; }}

/* ---------- buttons ---------- */
QPushButton {{
    background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong};
    border-radius: {r_input}px; padding: {px(6)}px {px(12)}px; font-weight: 500;
    min-height: {px(20)}px;
}}
QPushButton:hover {{ background: {t.surface_hover}; }}
QPushButton:pressed {{ background: {t.surface_alt}; }}
QPushButton:focus {{ border: 1px solid {t.focus}; }}
QPushButton:disabled {{ color: {t.text_faint}; border-color: {t.border}; background: {t.surface_sunken}; }}
QPushButton[variant="primary"] {{
    background: {t.primary}; color: {t.primary_text}; border: 1px solid {t.primary};
}}
QPushButton[variant="primary"]:hover {{ background: {t.primary_hover}; border-color: {t.primary_hover}; }}
QPushButton[variant="primary"]:pressed {{ background: {t.primary_pressed}; }}
QPushButton[variant="primary"]:focus {{ border: 1px solid {t.focus}; }}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; color: {t.text_muted}; }}
QPushButton[variant="ghost"]:hover {{ background: {t.surface_hover}; color: {t.text}; }}
QPushButton[variant="ghost"]:checked {{ background: {t.primary_soft}; color: {t.primary_soft_text}; }}
QPushButton[variant="ghost"]:focus {{ border: 1px solid {t.focus}; }}
QPushButton[variant="danger"] {{ color: {t.error}; border-color: {_rgba(t.error, 0.45)}; }}
QPushButton[variant="danger"]:hover {{ background: {t.error_bg}; }}
QPushButton[variant="icon"] {{
    background: transparent; border: 1px solid transparent; padding: {px(4)}px; min-width: {px(24)}px;
    border-radius: {px(6)}px;
}}
QPushButton[variant="icon"]:hover {{ background: {t.surface_hover}; }}
QPushButton[variant="icon"]:focus {{ border: 1px solid {t.focus}; }}
QPushButton[variant="link"] {{
    background: transparent; border: none; padding: 0px; color: {t.primary if not t.dark else t.info};
    text-align: left; min-height: 0px;
}}
QPushButton[variant="link"]:hover {{ text-decoration: underline; }}
QPushButton::menu-indicator {{ image: none; width: 0px; }}

/* chips */
QPushButton[variant="chip"] {{
    background: {t.surface}; border: 1px solid {t.border}; border-radius: {px(14)}px;
    padding: {px(4)}px {px(11)}px; color: {t.text_muted}; font-weight: 500; min-height: {px(18)}px;
}}
QPushButton[variant="chip"]:hover {{ border-color: {t.border_strong}; color: {t.text}; background: {t.surface_hover}; }}
QPushButton[variant="chip"]:checked {{ background: {t.primary_soft}; color: {t.primary_soft_text}; border-color: {_rgba(t.focus, 0.5)}; }}
QPushButton[variant="chip"]:focus {{ border-color: {t.focus}; }}

/* ---------- inputs ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: {r_input}px;
    padding: {px(5)}px {px(8)}px; selection-background-color: {t.selection}; selection-color: {t.text};
    min-height: {px(20)}px;
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: {t.text_faint}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {t.focus};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {t.text_faint}; background: {t.surface_sunken}; }}
QLineEdit[invalid="true"] {{ border: 1px solid {t.error}; background: {t.error_bg}; }}
QLineEdit#TitleEdit {{
    font-size: {px(ty.display)}px; font-weight: 600; border: 1px solid transparent; background: transparent;
    padding: {px(2)}px {px(4)}px;
}}
QLineEdit#TitleEdit:hover {{ border-color: {t.border}; }}
QLineEdit#TitleEdit:focus {{ border-color: {t.focus}; background: {t.surface}; }}
QSpinBox, QDoubleSpinBox {{ padding-right: {px(6)}px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 0px; border: none;
}}
QSpinBox#StepperField, QDoubleSpinBox#StepperField {{
    border-radius: 0px; border-left: none; border-right: none; font-weight: 600; qproperty-alignment: AlignCenter;
}}
QPushButton#StepperMinus, QPushButton#StepperPlus {{
    padding: 0px; min-width: {px(28)}px; max-width: {px(28)}px; background: {t.surface_sunken};
    color: {t.text_muted}; font-size: {px(15)}px;
}}
QPushButton#StepperMinus {{ border-top-right-radius: 0px; border-bottom-right-radius: 0px; }}
QPushButton#StepperPlus {{ border-top-left-radius: 0px; border-bottom-left-radius: 0px; }}
QPushButton#StepperMinus:hover, QPushButton#StepperPlus:hover {{ background: {t.surface_hover}; color: {t.text}; }}

QComboBox {{ padding-right: {px(24)}px; }}
QComboBox::drop-down {{ border: none; width: {px(22)}px; }}
QComboBox::down-arrow {{ image: url({chevron}); width: {px(12)}px; height: {px(12)}px; }}
QComboBox QAbstractItemView {{
    background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: {px(6)}px;
    padding: {px(4)}px; selection-background-color: {t.primary_soft}; selection-color: {t.primary_soft_text};
    outline: none;
}}
QComboBox QAbstractItemView::item {{ min-height: {px(26)}px; padding: 0px {px(6)}px; border-radius: {px(4)}px; }}

QCheckBox {{ spacing: {px(8)}px; }}
QCheckBox::indicator {{
    width: {px(16)}px; height: {px(16)}px; border: 1px solid {t.border_strong}; border-radius: {px(4)}px;
    background: {t.surface};
}}
QCheckBox::indicator:hover {{ border-color: {t.focus}; }}
QCheckBox::indicator:checked {{ background: {t.primary}; border-color: {t.primary}; image: url({check}); }}

/* ---------- tables / trees ---------- */
QTableView, QTreeView, QListView {{
    background: {t.surface}; border: 1px solid {t.border}; border-radius: {r_card}px;
    gridline-color: {t.border}; selection-background-color: {t.selection}; selection-color: {t.text};
    alternate-background-color: {t.surface_sunken};
}}
QTableView::item, QTreeView::item {{ padding: {px(4)}px {px(6)}px; border: none; }}
QTreeView::item:selected, QTableView::item:selected, QListView::item:selected {{
    background: {t.selection}; color: {t.text};
}}
QTreeView::item:hover, QListView::item:hover {{ background: {t.surface_hover}; }}
QTreeView::branch {{ background: transparent; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{
    background: {t.surface_sunken}; color: {t.text_muted}; border: none; border-bottom: 1px solid {t.border};
    padding: {px(7)}px {px(8)}px; font-weight: 600; font-size: {px(ty.caption)}px;
}}
QHeaderView::section:first {{ border-top-left-radius: {r_card}px; }}
QHeaderView::section:last {{ border-top-right-radius: {r_card}px; }}
QTableCornerButton::section {{ background: {t.surface_sunken}; border: none; }}

/* ---------- tabs ---------- */
QTabWidget::pane {{ border: none; top: -1px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {t.text_muted}; padding: {px(8)}px {px(14)}px; border: none;
    border-bottom: 2px solid transparent; font-weight: 500; margin-right: {px(4)}px;
}}
QTabBar::tab:hover {{ color: {t.text}; }}
QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.primary if not t.dark else t.info}; font-weight: 600; }}

/* ---------- menus ---------- */
QMenu {{
    background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: {px(8)}px; padding: {px(6)}px;
}}
QMenu::item {{ padding: {px(7)}px {px(24)}px {px(7)}px {px(10)}px; border-radius: {px(5)}px; }}
QMenu::item:selected {{ background: {t.primary_soft}; color: {t.primary_soft_text}; }}
QMenu::item:disabled {{ color: {t.text_faint}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: {px(5)}px {px(4)}px; }}
QMenu::icon {{ padding-left: {px(6)}px; }}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: {px(10)}px; margin: {px(2)}px; }}
QScrollBar:horizontal {{ background: transparent; height: {px(10)}px; margin: {px(2)}px; }}
QScrollBar::handle {{ background: {t.scrollbar}; border-radius: {px(3)}px; min-height: {px(28)}px; min-width: {px(28)}px; }}
QScrollBar::handle:hover {{ background: {t.text_faint}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- progress / slider ---------- */
QProgressBar {{
    background: {t.surface_alt}; border: none; border-radius: {px(3)}px; max-height: {px(6)}px; text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {t.primary if not t.dark else t.info}; border-radius: {px(3)}px; }}
QSlider::groove:horizontal {{ height: {px(4)}px; background: {t.surface_alt}; border-radius: {px(2)}px; }}
QSlider::handle:horizontal {{
    width: {px(16)}px; height: {px(16)}px; margin: -{px(6)}px 0; border-radius: {px(8)}px;
    background: {t.surface}; border: 1px solid {t.border_strong};
}}
QSlider::sub-page:horizontal {{ background: {t.primary}; border-radius: {px(2)}px; }}

/* ---------- callouts ---------- */
QFrame[callout="info"] {{ background: {t.info_bg}; border: 1px solid {_rgba(t.info, 0.25)}; border-radius: {px(8)}px; }}
QFrame[callout="warning"] {{ background: {t.warning_bg}; border: 1px solid {_rgba(t.warning, 0.28)}; border-radius: {px(8)}px; }}
QFrame[callout="error"] {{ background: {t.error_bg}; border: 1px solid {_rgba(t.error, 0.28)}; border-radius: {px(8)}px; }}
QFrame[callout="success"] {{ background: {t.success_bg}; border: 1px solid {_rgba(t.success, 0.28)}; border-radius: {px(8)}px; }}
QFrame[callout] QLabel {{ background: transparent; }}

QTextBrowser {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: {r_card}px; padding: {px(8)}px; }}
QSplitter::handle {{ background: transparent; }}
QDialog {{ background: {t.bg}; }}
"""
