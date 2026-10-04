"""Tiny i18n layer (no Qt dependency).

Strings live in ``uk.json`` / ``en.json`` next to this module as flat ``key -> template`` maps.
Templates use ``str.format`` placeholders. Plural forms are stored as lists:
Ukrainian ``[one, few, many]`` and English ``[one, other]``.

Catalog data carries its own localized labels as ``{"uk": "...", "en": "..."}`` dicts; use
:meth:`Translator.pick` for those.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("uk", "en")
DEFAULT_LANGUAGE = "uk"
_DIR = Path(__file__).resolve().parent


@cache
def _load(lang: str) -> dict[str, Any]:
    path = _DIR / f"{lang}.json"
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def plural_index(lang: str, n: float) -> int:
    """Index into the plural-forms list for ``n``."""
    n_int = abs(int(n))
    if n != int(n):  # fractional values use the "few/other" form
        return 1
    if lang == "uk":
        if n_int % 10 == 1 and n_int % 100 != 11:
            return 0
        if 2 <= n_int % 10 <= 4 and not 12 <= n_int % 100 <= 14:
            return 1
        return 2
    return 0 if n_int == 1 else 1


class Translator:
    """Looks up templates for one language, falling back to Ukrainian, then to the key."""

    def __init__(self, lang: str = DEFAULT_LANGUAGE) -> None:
        self.lang = lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE

    def _raw(self, key: str) -> Any:
        table = _load(self.lang)
        if key in table:
            return table[key]
        fallback = _load(DEFAULT_LANGUAGE)
        if key in fallback:
            log.debug("i18n: '%s' missing in %s, using %s", key, self.lang, DEFAULT_LANGUAGE)
            return fallback[key]
        log.warning("i18n: missing key '%s'", key)
        return key

    def t(self, key: str, **kwargs: Any) -> str:
        """Translate ``key`` and format it with ``kwargs``."""
        raw = self._raw(key)
        if isinstance(raw, list):
            raw = raw[-1]
        try:
            return str(raw).format(**kwargs)
        except (KeyError, IndexError, ValueError):
            log.exception("i18n: bad format for key '%s'", key)
            return str(raw)

    def plural(self, key: str, n: float, **kwargs: Any) -> str:
        """Translate a plural key; ``{n}`` is available in the template."""
        raw = self._raw(key)
        forms = raw if isinstance(raw, list) else [raw]
        idx = min(plural_index(self.lang, n), len(forms) - 1)
        n_text = f"{n:g}" if isinstance(n, float) else str(n)
        return str(forms[idx]).format(n=n_text, **kwargs)

    def pick(self, value: Mapping[str, str] | str | None) -> str:
        """Choose the right language from a localized ``{"uk": .., "en": ..}`` value."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return value.get(self.lang) or value.get(DEFAULT_LANGUAGE) or next(iter(value.values()), "")

    def has(self, key: str) -> bool:
        return key in _load(self.lang) or key in _load(DEFAULT_LANGUAGE)


_current = Translator(DEFAULT_LANGUAGE)


def set_language(lang: str) -> None:
    global _current
    _current = Translator(lang)


def current() -> Translator:
    return _current


def tr(key: str, **kwargs: Any) -> str:
    return _current.t(key, **kwargs)
