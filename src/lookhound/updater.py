"""Checks GitHub Releases for a newer version, downloads the installer for this
platform (.dmg or .msi), and once Lookhound has quit, swaps the new version in
and opens it again. Does nothing until UPDATE_REPO is set."""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .settings import DATA_DIR, IS_MAC, IS_WIN, UPDATE_REPO, __version__

UPDATES = DATA_DIR / "updates"


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
    # On a Mac the zip unpacks in seconds; the DMG (for people downloading by
    # hand) is the fallback for releases made before the zip was published.
    wants = ["-mac.zip", ".dmg"] if IS_MAC else [".msi"] if IS_WIN else []
    newer = []
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        if _as_tuple(release.get("tag_name", "")) <= _as_tuple(__version__):
            continue
        assets = {a["name"].lower(): a["browser_download_url"] for a in release.get("assets", [])}
        for want in wants:
            match = next((url for name, url in assets.items() if name.endswith(want)), None)
            if match:
                newer.append((release, match))
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


def download(url, on_progress=None):
    """Downloads an installer into Lookhound's updates folder, resuming after a
    dropped connection. on_progress(fraction) is called as it goes."""
    UPDATES.mkdir(parents=True, exist_ok=True)
    target = UPDATES / url.rsplit("/", 1)[-1]
    partial = target.with_suffix(target.suffix + ".part")
    target.unlink(missing_ok=True)
    total = None
    for attempt in range(8):
        have = partial.stat().st_size if partial.exists() else 0
        req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if have and resp.status != 206:  # server ignored the resume; start over
                    have = 0
                    partial.unlink(missing_ok=True)
                size = int(resp.headers.get("Content-Length", 0))
                total = total or (have + size if size else None)
                with open(partial, "ab") as out:
                    while chunk := resp.read(1 << 20):
                        out.write(chunk)
                        if on_progress and total:
                            on_progress(min(1.0, partial.stat().st_size / total))
            if total is None or partial.stat().st_size >= total:
                partial.rename(target)
                return target
        except OSError:
            time.sleep(min(30, 2 ** attempt))
    raise OSError("the download kept failing")


def _this_app():
    """The installed Lookhound.app (Mac) or Lookhound.exe (Windows), or None when
    running from source."""
    exe = Path(sys.executable).resolve()
    if IS_MAC:
        return next((p for p in exe.parents if p.suffix == ".app"), None)
    if IS_WIN and exe.name.lower().startswith("lookhound"):
        return exe
    return None


# Runs after Lookhound quits: unpacks the new app next to the old one, swaps
# them, and opens it again. If anything fails, the old app stays untouched and
# the downloaded file is shown instead.
MAC_SCRIPT = """#!/bin/sh
pid="$1"; pkg="$2"; app="$3"
while kill -0 "$pid" 2>/dev/null; do sleep 0.3; done
work="$app.updating"; rm -rf "$work"; mkdir -p "$work"
case "$pkg" in
  *.zip) ditto -x -k "$pkg" "$work" ;;
  *) mnt=$(hdiutil attach -nobrowse -readonly -noautoopen "$pkg" | tail -1 | awk -F'\\t' '{print $NF}')
     [ -n "$mnt" ] && ditto "$mnt"/*.app "$work/Lookhound.app"
     [ -n "$mnt" ] && hdiutil detach -quiet "$mnt" ;;
esac
new=$(ls -d "$work"/*.app 2>/dev/null | head -1)
if [ -n "$new" ] && [ -f "$new/Contents/Info.plist" ]; then
  rm -rf "$app.old" && mv "$app" "$app.old" && mv "$new" "$app" && rm -rf "$app.old" "$work"
  rm -f "$pkg"
  [ -z "$LOOKHOUND_NO_REOPEN" ] && open "$app"
else
  rm -rf "$work"
  [ -z "$LOOKHOUND_NO_REOPEN" ] && open -R "$pkg"
fi
"""

# Same on Windows: wait for Lookhound to close, install quietly, start it again.
WIN_SCRIPT = """param($ProcessId, $Msi, $Exe)
Wait-Process -Id $ProcessId -ErrorAction SilentlyContinue
$log = Join-Path (Split-Path $Msi) 'update.log'
$p = Start-Process msiexec -ArgumentList '/i', ('"' + $Msi + '"'), '/passive', '/norestart', '/l*v', ('"' + $log + '"') -Wait -PassThru
if ($p.ExitCode -eq 0) { Remove-Item $Msi -ErrorAction SilentlyContinue }
if (-not $env:LOOKHOUND_NO_REOPEN) { Start-Process $Exe }
"""


def restart_into(installer):
    """Starts the swap-and-reopen helper. The caller then quits Lookhound; the
    helper waits for that before touching anything."""
    installer = Path(installer)
    app = _this_app()
    pid = str(os.getpid())
    if IS_MAC and app:
        script = UPDATES / "apply_update.sh"
        script.write_text(MAC_SCRIPT)
        subprocess.Popen(["/bin/sh", str(script), pid, str(installer), str(app)],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif IS_WIN and app:
        script = UPDATES / "apply_update.ps1"
        script.write_text(WIN_SCRIPT)
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-WindowStyle", "Hidden", "-File", str(script),
                          "-ProcessId", pid, "-Msi", str(installer), "-Exe", str(app)],
                         creationflags=flags)
    elif IS_MAC:
        subprocess.Popen(["open", str(installer)])  # running from source: just show it
    elif IS_WIN:
        subprocess.Popen(["msiexec", "/i", str(installer)])
