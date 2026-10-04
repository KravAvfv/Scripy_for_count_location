"""Lucide line icons (ISC licence), recoloured on the fly for the active theme."""

from __future__ import annotations

import hashlib
import tempfile
from functools import cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .theme import ASSETS, tokens

ICON_DIR = ASSETS / "icons"
_CACHE_DIR = Path(tempfile.gettempdir()) / "sitesizer-icons"
STROKE = "1.75"


@cache
def _svg_source(name: str) -> str:
    path = ICON_DIR / f"{name}.svg"
    if not path.exists():
        path = ICON_DIR / "circle-help.svg"
    return path.read_text(encoding="utf-8")


def _svg(name: str, color: str, stroke: str = STROKE) -> bytes:
    src = _svg_source(name).replace('stroke="currentColor"', f'stroke="{color}"')
    src = src.replace('stroke-width="2"', f'stroke-width="{stroke}"')
    return src.encode("utf-8")


def _resolve(color: str | None) -> str:
    if color is None:
        return tokens().text_muted
    if color.startswith("#"):
        return color
    return str(getattr(tokens(), color))


def pixmap(name: str, size: int = 18, color: str | None = None, stroke: str = STROKE) -> QPixmap:
    """Crisp, DPI-aware pixmap of an icon."""
    app = QGuiApplication.instance()
    dpr = app.devicePixelRatio() if isinstance(app, QGuiApplication) else 1.0
    return _pixmap_cached(name, size, _resolve(color), stroke, float(dpr))


@cache
def _pixmap_cached(name: str, size: int, color: str, stroke: str, dpr: float) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(_svg(name, color, stroke)))
    pm = QPixmap(QSize(round(size * dpr), round(size * dpr)))
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size * dpr, size * dpr))
    painter.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str | None = None, size: int = 18, active_color: str | None = None) -> QIcon:
    """QIcon with an optional colour for the checked/active state."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, size, color), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(pixmap(name, size, active_color or color), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(pixmap(name, size, active_color or color), QIcon.Mode.Active, QIcon.State.On)
    ic.addPixmap(pixmap(name, size, "text_faint"), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic


def icon_file(name: str, color: str, stroke: str = STROKE) -> str:
    """Path to a recoloured SVG on disk (for QSS ``image: url(...)``)."""
    resolved = _resolve(color)
    key = hashlib.sha1(f"{name}{resolved}{stroke}".encode()).hexdigest()[:10]
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / f"{name}-{key}.svg"
    if not path.exists():
        path.write_bytes(_svg(name, resolved, stroke))
    return path.as_posix()


def clear_cache() -> None:
    _pixmap_cached.cache_clear()


def tinted(color: QColor, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(alpha)
    return c
