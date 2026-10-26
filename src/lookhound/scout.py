"""Background indexing: one pass over the watched folders on start, then live
updates from the filesystem watcher. Runs on its own thread with its own DB
connection, and reports progress back to the window through Qt signals."""
import os
import queue
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import sniff
from .brain import DIMS
from .shelf import Shelf

SETTLE_SECONDS = 1.5  # wait for a file to stop changing before reading it


class _Relay(FileSystemEventHandler):
    def __init__(self, scout):
        self.scout = scout

    def on_any_event(self, event):
        if event.event_type in ("opened", "closed", "closed_no_write"):
            return
        self.scout.nudge(event.src_path)
        if getattr(event, "dest_path", None):
            self.scout.nudge(event.dest_path)


class Scout(QObject):
    status = Signal(str)
    count_changed = Signal(int)
    busy_changed = Signal(bool)

    def __init__(self, brain, folders, icon_for=None):
        super().__init__()
        self.brain = brain
        self.folders = [Path(f) for f in folders]
        self.icon_for = icon_for
        self._jobs = queue.Queue()
        self._pending = {}  # path -> time of last change
        self._pending_lock = threading.Lock()
        self._observer = None
        self._stop = threading.Event()

    # ---- public ----------------------------------------------------------

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="scout").start()
        threading.Thread(target=self._settle_loop, daemon=True, name="settle").start()

    def set_folders(self, folders):
        self.folders = [Path(f) for f in folders]
        self._jobs.put(("rescan", None))

    def nudge(self, raw_path):
        path = self._resolve(Path(raw_path))
        if path is not None:
            with self._pending_lock:
                self._pending[path] = time.monotonic()

    def stop(self):
        self._stop.set()
        if self._observer:
            self._observer.stop()

    # ---- internals -------------------------------------------------------

    def _resolve(self, path):
        """Map a raw event path to the thing we index, or None to ignore it."""
        for i, part in enumerate(path.parts):
            p = Path(*path.parts[: i + 1])
            if sniff.kind_of(p) == "app":
                return p  # anything inside an app bundle counts as the app
            if i < len(path.parts) - 1 and sniff.skip_dir(p) and p not in self.folders:
                return None
        return path

    def _settle_loop(self):
        while not self._stop.is_set():
            time.sleep(0.5)
            now = time.monotonic()
            with self._pending_lock:
                ready = [p for p, t in self._pending.items() if now - t > SETTLE_SECONDS]
                for p in ready:
                    del self._pending[p]
            for p in ready:
                self._jobs.put(("touch", p))

    def _walk(self):
        for root in self.folders:
            if not root.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                here = Path(dirpath)
                keep = []
                for d in dirnames:
                    sub = here / d
                    if sniff.kind_of(sub) == "app":
                        yield sub
                    elif not sniff.skip_dir(sub):
                        keep.append(d)
                dirnames[:] = keep
                for f in filenames:
                    if not f.startswith(".") and sniff.kind_of(here / f):
                        yield here / f

    def _inside_folders(self, path):
        return any(path == f or f in path.parents for f in self.folders)

    def _index(self, shelf, path):
        try:
            if not path.exists() or not self._inside_folders(path):
                shelf.remove(str(path))
                return
            if path.is_dir() and sniff.kind_of(path) != "app":
                return
            mtime = path.stat().st_mtime
            if shelf.mtime_of(str(path)) == mtime:
                return
            found = sniff.sniff(path, self.icon_for)
            if not found:
                return
            kind, preview, payloads = found
            vectors = {space: self.brain.embed(space, items) for space, items in payloads.items()}
            shelf.put(str(path), path.name, kind, mtime, preview, vectors)
        except Exception as exc:  # one unreadable file shouldn't stop the rest
            print(f"[lookhound] skipped {path}: {exc}")

    def _full_scan(self, shelf):
        self.busy_changed.emit(True)
        self.status.emit("Getting to know your files…")
        on_disk = set()
        for n, path in enumerate(self._walk(), 1):
            on_disk.add(str(path))
            self._index(shelf, path)
            if n % 25 == 0:
                self.status.emit(f"Getting to know your files… {n:,} so far")
                self.count_changed.emit(shelf.count())
        for stale in set(shelf.all_paths()) - on_disk:
            shelf.remove(stale)
        self.count_changed.emit(shelf.count())
        self.status.emit("All tucked in. I'll keep watch over your folders.")
        self.busy_changed.emit(False)

    def _watch(self):
        if self._observer:
            self._observer.stop()
        self._observer = Observer()
        relay = _Relay(self)
        for folder in self.folders:
            if folder.is_dir():
                self._observer.schedule(relay, str(folder), recursive=True)
        self._observer.start()

    def _run(self):
        shelf = Shelf()
        self.count_changed.emit(shelf.count())
        self.status.emit("Settling in. The very first time takes a few minutes.")
        self.busy_changed.emit(True)
        self.brain.warm_up(lambda name: self.status.emit(f"Settling in ({list(DIMS).index(name) + 1} of 3)…"))
        self._watch()
        self._full_scan(shelf)
        while not self._stop.is_set():
            action, path = self._jobs.get()
            if action == "rescan":
                self._watch()
                self._full_scan(shelf)
            else:
                self._index(shelf, path)
                self.count_changed.emit(shelf.count())
