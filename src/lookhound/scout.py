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
from .brain import WARM_ORDER
from .settings import __version__
from .shelf import BY_NAME, PARTLY, READ, Shelf

SETTLE_SECONDS = 1.5  # wait for a file to stop changing before reading it
BATCH = 32  # files read and embedded together; the models are much faster in bulk
# Bump when files need reading again to benefit from a change in how they're read
# (2: whole files and exact words, not just each file's opening).
INDEX_FORMAT = "2"


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
        self.retry_unreadable = False  # set when Lookhound has been updated
        self.reread_all = False  # set once when INDEX_FORMAT changes

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

    def _prepare(self, shelf, path, later=None):
        """What the models need from one file, or None if there's nothing to do.
        A file read before (same modified time) is skipped; one still waiting on
        the code model just rejoins that queue without being read again."""
        try:
            if not path.exists() or not self._inside_folders(path):
                shelf.remove(str(path))
                return None
            if path.is_dir() and sniff.kind_of(path) != "app":
                return None
            mtime = path.stat().st_mtime
            known = shelf.state(str(path))
            if known and known[0] == mtime and known[1] == READ and not self.reread_all:
                return None
            if known and known[0] == mtime and known[1] == BY_NAME and not self.retry_unreadable:
                return None
            found = sniff.sniff(path, self.icon_for)
            if (known and known[0] == mtime and known[1] == PARTLY and later is not None
                    and not self.reread_all):
                if found and "code" in found[2]:
                    later.append((str(path), found[2]["code"]))
                    return None
            if not found:
                return None
            return (path, mtime) + found
        except Exception as exc:  # one unreadable file shouldn't stop the rest
            print(f"[lookhound] skipped {path}: {exc}")
            return None

    def _flush(self, shelf, batch, later=None):
        """Embed a batch of prepared files together, one model call per space.
        With `later`, the slow code model is skipped and its work queued there."""
        if not batch:
            return
        try:
            vectors = [{} for _ in batch]
            spaces = {space for *_, payloads in batch for space in payloads if not space.startswith("_")}
            if later is not None:
                spaces.discard("code")
            for space in spaces:
                items, owners = [], []
                for i, (*_, payloads) in enumerate(batch):
                    for item in payloads.get(space, []):
                        items.append(item)
                        owners.append(i)
                embedded = self.brain.embed(space, items)
                for i in set(owners):
                    vectors[i][space] = embedded[[n for n, o in enumerate(owners) if o == i]]
        except Exception:
            if len(batch) > 1:  # find the one file that broke it
                for one in batch:
                    self._flush(shelf, [one], later)
                return
            print(f"[lookhound] skipped {batch[0][0]}")
            return
        for (path, mtime, kind, preview, payloads), vecs in zip(batch, vectors):
            waits = later is not None and "code" in payloads
            try:
                added = sniff.added_time(path)
            except OSError:
                added = None
            ready = BY_NAME if payloads.get("_by_name") else PARTLY if waits else READ
            shelf.put(str(path), path.name, kind, mtime, preview, vecs, commit=False,
                      ready=ready, added=added, words=payloads.get("_words", ""),
                      summary=payloads.get("_summary"))
            if waits:
                later.append((str(path), payloads["code"]))
        shelf.commit()

    def _read_code(self, shelf, later):
        """The code model's pass: slower, so it runs once everything is searchable."""
        for start in range(0, len(later), BATCH):
            if self._stop.is_set():
                return
            batch = later[start:start + BATCH]
            self.status.emit(f"Reading your code more closely… {start:,} of {len(later):,}")
            items = [item for _, code in batch for item in code]
            try:
                embedded = self.brain.embed("code", items)
            except Exception as exc:
                print(f"[lookhound] code pass skipped a batch: {exc}")
                continue
            at = 0
            for path, code in batch:
                shelf.finish(path, {"code": embedded[at:at + len(code)]})
                at += len(code)
            shelf.commit()

    def _index(self, shelf, path):
        self._flush(shelf, [p for p in [self._prepare(shelf, path)] if p])

    def _survey(self, shelf):
        """Find every file and note it by name. Takes seconds, needs no models."""
        self.busy_changed.emit(True)
        self.status.emit("Looking around your folders…")
        paths = list(self._walk())
        # First every file by name, so the count is right and names are searchable at once.
        for n, path in enumerate(paths, 1):
            try:
                shelf.note(str(path), path.name, sniff.kind_of(path), path.stat().st_mtime,
                           sniff.added_time(path))
            except OSError:
                pass
            if n % 500 == 0:
                shelf.commit()
                self.count_changed.emit(shelf.count())
        shelf.commit()
        self.count_changed.emit(shelf.count())
        on_disk = {str(p) for p in paths}
        for stale in set(shelf.all_paths()) - on_disk:
            shelf.remove(stale)
        return paths

    def _read_all(self, shelf, paths):
        """Read every file properly, a batch at a time."""
        self.busy_changed.emit(True)
        total = len(paths)
        batch, later, read = [], [], 0
        for n, path in enumerate(paths, 1):
            prepared = self._prepare(shelf, path, later)
            if prepared:
                batch.append(prepared)
                read += 1
            if len(batch) >= BATCH or (n == total and batch):
                self._flush(shelf, batch, later)
                batch = []
                # Only files actually being read count; ones already known are just checked.
                if self.reread_all:
                    self.status.emit(f"Learning your files more deeply (once, after this update)… {n:,} of {total:,}")
                else:
                    self.status.emit(f"Getting to know your files… {read:,} read, {n:,} of {total:,} checked")
        self.count_changed.emit(shelf.count())
        self._read_code(shelf, later)
        shelf.set_meta("version", __version__)
        shelf.set_meta("format", INDEX_FORMAT)
        self.retry_unreadable = self.reread_all = False
        if read or later:
            self.status.emit("All tucked in. I'll keep watch over your folders.")
        else:
            self.status.emit("All tucked in. Nothing new since last time.")
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
        returning = shelf.count() > 0
        # After an update, files that couldn't be read before get another go.
        self.retry_unreadable = shelf.get_meta("version") != __version__
        # Once, after an update that reads files more thoroughly: re-read them all.
        # Search keeps working meanwhile; each file's old entry stays until replaced.
        self.reread_all = returning and shelf.get_meta("format") != INDEX_FORMAT
        paths = self._survey(shelf)
        if returning:
            # Same index as last time (updates keep it too); just check for changes.
            self.status.emit("Welcome back. Checking for anything new…")
            step = "Waking up"
        else:
            self.status.emit("Settling in. The very first time takes a few minutes.")
            step = "Settling in"
        self.brain.warm_up(
            lambda name: self.status.emit(f"{step} ({WARM_ORDER.index(name) + 1} of 3)…"),
            lambda name: self.status.emit("The internet is being slow. I'll keep trying…"),
        )
        self._watch()
        self._read_all(shelf, paths)
        while not self._stop.is_set():
            action, path = self._jobs.get()
            if action == "rescan":
                self._watch()
                self._read_all(shelf, self._survey(shelf))
            else:
                self._index(shelf, path)
                self.count_changed.emit(shelf.count())
