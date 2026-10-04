from __future__ import annotations

import os

import pytest

from sitesizer.core.catalog import Catalog, load_default_catalog, prototype_compat_catalog
from sitesizer.core.models import ApGroup, SiteInput

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def _qapp() -> object:
    """One QApplication for the whole run (exporters and GUI tests share it)."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    return load_default_catalog()


@pytest.fixture(scope="session")
def compat_catalog(catalog: Catalog) -> Catalog:
    return prototype_compat_catalog(catalog)


def make_site(**kw: object) -> SiteInput:
    groups = kw.pop("aps", None)
    data: dict[str, object] = {"tier": 3, **kw}
    if groups is not None:
        data["ap_groups"] = [ApGroup(zone=z, qty=q).model_dump() for z, q in groups]  # type: ignore[union-attr]
    return SiteInput.model_validate(data)
