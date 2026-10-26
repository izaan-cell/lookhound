"""Builds Lookhound.dmg with the drag-to-Applications window in Lookhound's style.
Briefcase's own DMG window is fixed at 350x150, too small for a designed background,
so the release workflow calls this after `briefcase package macOS`.

    python packaging/make_dmg.py path/to/Lookhound.app dist/Lookhound.dmg
"""
import sys
from pathlib import Path

import dmgbuild

HERE = Path(__file__).resolve().parent
W, H = 640, 400                       # matches make_dmg_background.py
APP_X, APPS_X, ICON_Y = 180, 460, 250
TITLE_BAR = 28


def main(app_path, dmg_path):
    app = Path(app_path).resolve()
    settings = {
        "files": [str(app)],
        "symlinks": {"Applications": "/Applications"},
        "icon": str(HERE.parent / "src/lookhound/resources/lookhound.icns"),
        "background": str(HERE / "dmg-background.png"),   # dmgbuild picks up the @2x file too
        "window_rect": ((200, 160), (W, H + TITLE_BAR)),
        "icon_size": 112,
        "text_size": 13,
        "icon_locations": {app.name: (APP_X, ICON_Y), "Applications": (APPS_X, ICON_Y)},
        "show_status_bar": False,
        "show_tab_view": False,
        "show_toolbar": False,
        "show_pathbar": False,
        "show_sidebar": False,
        "default_view": "icon-view",
        "format": "ULMO",  # LZMA: the smallest download, opens on macOS 10.15+
    }
    dmgbuild.build_dmg(str(dmg_path), "Lookhound", settings=settings)


if __name__ == "__main__":
    main(*sys.argv[1:3])
