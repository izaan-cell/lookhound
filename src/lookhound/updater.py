"""Checks GitHub Releases for a newer version, downloads the installer for this
platform (.dmg or .msi) and opens it. Does nothing until UPDATE_REPO is set."""
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from .settings import IS_MAC, IS_WIN, UPDATE_REPO, __version__


def _as_tuple(version):
    return tuple(int(p) for p in version.lstrip("v").split(".") if p.isdigit())


def check():
    """Returns (version, download_url) if a newer release exists, else None."""
    if not UPDATE_REPO:
        return None
    url = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        release = json.load(resp)
    latest = release.get("tag_name", "")
    if _as_tuple(latest) <= _as_tuple(__version__):
        return None
    want = ".dmg" if IS_MAC else ".msi" if IS_WIN else None
    for asset in release.get("assets", []):
        if want and asset["name"].lower().endswith(want):
            return latest, asset["browser_download_url"]
    return None


def install(download_url):
    target = Path(tempfile.mkdtemp()) / download_url.rsplit("/", 1)[-1]
    urllib.request.urlretrieve(download_url, target)
    if IS_MAC:
        subprocess.Popen(["open", str(target)])
    elif IS_WIN:
        subprocess.Popen(["msiexec", "/i", str(target)])
    return target
