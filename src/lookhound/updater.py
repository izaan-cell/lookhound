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
    """If newer releases exist, returns a dict: version (the newest), url (its
    installer for this platform), page (its release notes) and behind (how many
    released versions are newer than this one). Otherwise None."""
    if not UPDATE_REPO:
        return None
    url = f"https://api.github.com/repos/{UPDATE_REPO}/releases?per_page=50"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        releases = json.load(resp)
    want = ".dmg" if IS_MAC else ".msi" if IS_WIN else None
    newer = []
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        if _as_tuple(release.get("tag_name", "")) <= _as_tuple(__version__):
            continue
        for asset in release.get("assets", []):
            if want and asset["name"].lower().endswith(want):
                newer.append((release, asset["browser_download_url"]))
                break
    if not newer:
        return None
    newest, download = max(newer, key=lambda r: _as_tuple(r[0]["tag_name"]))
    return {
        "version": newest["tag_name"].lstrip("v"),
        "url": download,
        "page": newest.get("html_url") or f"https://github.com/{UPDATE_REPO}/releases/latest",
        "behind": len(newer),
    }


def install(download_url):
    target = Path(tempfile.mkdtemp()) / download_url.rsplit("/", 1)[-1]
    urllib.request.urlretrieve(download_url, target)
    if IS_MAC:
        subprocess.Popen(["open", str(target)])
    elif IS_WIN:
        subprocess.Popen(["msiexec", "/i", str(target)])
    return target
