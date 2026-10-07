"""Render GUI screenshots (light + dark) for visual review and the README.

    python tools/screenshots.py [out_dir] [--size 1440x900] [--pages location,bom] [--themes light,dark]

Runs offscreen with isolated settings, so it never touches your real configuration.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def demo_project():
    from sitesizer.core.presets import preset
    from sitesizer.core.project import Project

    project = Project(name="Демо: мережа компанії", customer="ТОВ «Приклад»", author="Мережевий інженер")
    hq = preset("hq").build(name="Київ — головний офіс")  # type: ignore[union-attr]
    hq.redundant_psu = True
    hq.location_code, hq.location_id, hq.floors = "KV001", 10, 5
    project.add_site(hq, is_hub=True)
    wh = preset("warehouse").build(name="Склад Бровари")  # type: ignore[union-attr]
    wh.location_code, wh.location_id = "BR014", 14
    project.add_site(wh)
    rd = preset("rnd_office").build(name="Львів R&D")  # type: ignore[union-attr]
    project.add_site(rd)
    farm = preset("farm3d").build(name="Дніпро 3D-ферма")  # type: ignore[union-attr]
    project.add_site(farm)
    return project


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default=str(ROOT / "docs" / "screenshots"))
    ap.add_argument("--size", default="1440x900")
    ap.add_argument(
        "--pages", default="location,racks,ipplan,bom,topology,power,compare,projects,catalog,help,settings"
    )
    ap.add_argument("--themes", default="light,dark")
    ap.add_argument("--site", type=int, default=0, help="index of the site to show")
    ap.add_argument("--lang", default="uk")
    ap.add_argument("--scale", type=float, default=1.0)
    args = ap.parse_args()
    w, h = (int(x) for x in args.size.lower().split("x"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QCoreApplication, QSettings
    from PySide6.QtWidgets import QApplication

    QCoreApplication.setOrganizationName("LocalCountShots")
    QCoreApplication.setApplicationName("LocalCountShots")
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from sitesizer.gui.main_window import MainWindow
    from sitesizer.gui.state import AppState
    from sitesizer.gui.theme import load_fonts, set_font_family, theme_manager

    set_font_family(load_fonts())
    ini = Path(tempfile.mkdtemp()) / "shots.ini"
    qs = QSettings(str(ini), QSettings.Format.IniFormat)
    state = AppState(qs)
    state.settings.onboarding_done = True
    state.settings.language = args.lang
    from sitesizer.i18n import set_language

    set_language(args.lang)
    state.project = demo_project()
    state.current_id = state.project.sites[args.site].id
    for theme in args.themes.split(","):
        theme_manager.apply(theme, args.scale)
        win = MainWindow(state)
        win._tour_shown = True
        win.resize(w, h)
        win.show()
        for page in args.pages.split(","):
            win.navigate(page)
            for _ in range(6):
                app.processEvents()
            img = win.grab()
            path = out / f"{page}-{theme}.png"
            img.save(str(path))
            print(path)
        win.close()
        win.deleteLater()
        app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
