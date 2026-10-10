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
    """One location (BO123) with three floors; the firewall stands on the 7th."""
    from sitesizer.core.models import ApGroup, RoomInput, SiteInput
    from sitesizer.core.project import Project

    project = Project(name="BO123 — офіс", customer="ТОВ «Приклад»", author="Мережевий інженер")
    common = {"location_code": "BO123", "location_id": 57, "tier": 3, "redundant_psu": False}
    floors = [
        (7, 150, 12, [ApGroup(zone="low_density", qty=10)], True),
        (5, 200, 20, [ApGroup(zone="low_density", qty=12)], False),
        (1, 60, 30, [ApGroup(zone="corridor", qty=6)], False),
    ]
    for n, sockets, cameras, aps, fw in floors:
        site = SiteInput(name=f"{n} поверх", floor=n, sockets=sockets, cameras=cameras, ap_groups=aps, **common)
        if fw:
            # the firewall floor has two telecom rooms, each with its own endpoints
            site = site.model_copy(
                update={
                    "sockets": 0,
                    "cameras": 0,
                    "closets": 2,
                    "rooms": [
                        RoomInput(sockets=100, cameras=12, ajax=2, skud=4),
                        RoomInput(sockets=50, other=3),
                    ],
                }
            )
        project.add_site(site, is_hub=fw)
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
