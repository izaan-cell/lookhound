"""Where Lookhound keeps its stuff, and which folders it watches."""
import json
import os
import sys
from pathlib import Path

from platformdirs import user_data_dir

__version__ = "0.2.0"

# Where the updater looks for new releases.
UPDATE_REPO = "izaan-cell/lookhound"

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

# LOOKHOUND_HOME lets a dev run use a scratch index instead of the real one.
DATA_DIR = Path(os.environ.get("LOOKHOUND_HOME") or user_data_dir("Lookhound", appauthor=False))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "lookhound.db"
MODELS_DIR = DATA_DIR / "models"
ICON_CACHE = DATA_DIR / "icons"
ICON_CACHE.mkdir(exist_ok=True)
PREFS_PATH = DATA_DIR / "prefs.json"


def _default_folders():
    home = Path.home()
    names = ["Desktop", "Documents", "Downloads", "Pictures"]
    folders = [home / n for n in names if (home / n).is_dir()]
    if IS_MAC:
        folders.append(Path("/Applications"))
    if IS_WIN:
        for start_menu in (
            Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs",
            Path("C:/ProgramData/Microsoft/Windows/Start Menu/Programs"),
        ):
            if start_menu.is_dir():
                folders.append(start_menu)
    return [str(f) for f in folders]


def load_prefs():
    try:
        prefs = json.loads(PREFS_PATH.read_text())
    except (OSError, ValueError):
        prefs = {}
    prefs.setdefault("folders", _default_folders())
    return prefs


def save_prefs(prefs):
    PREFS_PATH.write_text(json.dumps(prefs, indent=2))
