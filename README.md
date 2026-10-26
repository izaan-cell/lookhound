# Lookhound

Find any file on your computer by describing it. Type a few words or drop in a picture, and
Lookhound finds the photos, documents, code and apps that match what you meant. Free,
private, and fully local for macOS and Windows.

Download it at [lookhound.spools.studio](https://lookhound.spools.studio).

## How it works

- **Models:** CLIP for pictures and app icons, all-MiniLM-L6-v2 for documents, and Jina
  Embeddings v2 Base Code for code. They run on your computer with PyTorch.
- **Storage:** one SQLite file, with vectors kept in [sqlite-vec](https://github.com/asg017/sqlite-vec) tables.
- **Watching:** `watchdog` keeps the index current as files change.
- **Window:** Qt (PySide6), the same code on both platforms.

## Running from source

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m lookhound
```

Set `LOOKHOUND_HOME` to a scratch folder to try it without touching your real index.

## Releases

Push a tag like `v0.2.0`. GitHub Actions uses [Briefcase](https://briefcase.readthedocs.io)
to build `Lookhound.dmg` and `Lookhound.msi` and attaches them to a GitHub Release, which is
where the website's download buttons and the in-app updater look.
