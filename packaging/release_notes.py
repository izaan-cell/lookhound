"""Prints one version's section of CHANGELOG.md, for its GitHub release page.

    python packaging/release_notes.py v0.3.0 > notes.md

Fails if the version has no section, so a release can't go out without notes.
"""
import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def notes(version):
    version = version.lstrip("v")
    text = CHANGELOG.read_text()
    match = re.search(rf"^## {re.escape(version)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not match or not match.group(1).strip():
        raise SystemExit(f"CHANGELOG.md has no notes for {version}")
    return match.group(1).strip() + "\n"


if __name__ == "__main__":
    sys.stdout.write(notes(sys.argv[1]))
