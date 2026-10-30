"""One SQLite file holds everything: file rows, and their vectors in sqlite-vec
tables. If the sqlite-vec extension can't load, vectors go in plain BLOB tables
and are compared with numpy instead: slower, but the same answers."""
import sqlite3
import threading
import time
from pathlib import Path

import re

import numpy as np

from .brain import DIMS
from .settings import DB_PATH, ICON_CACHE

SPACES = tuple(DIMS)
# Several threads open the index at start-up; setting it up one at a time keeps
# them from tripping over each other while tables are created or upgraded.
_SETUP = threading.Lock()
# files.ready: 0 = known by name only, 1 = fully read, 2 = read but waiting on the code
# model, 3 = couldn't be read, so kept by name (tried again after each update).
NAME_ONLY, READ, PARTLY, BY_NAME = 0, 1, 2, 3


def _try_load_vec(conn):
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception:
        return False


class _ApswCursor:
    def __init__(self, cursor, conn):
        self._rows = list(cursor)
        self.lastrowid = conn.last_insert_rowid()

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _ApswConnection:
    """Just enough of sqlite3.Connection on top of apsw. Packaged builds use this:
    their Python's sqlite3 can't load extensions, but apsw brings its own SQLite
    that can, so sqlite-vec still works."""

    def __init__(self, path):
        import apsw
        import sqlite_vec

        self._conn = apsw.Connection(path)
        self._conn.set_busy_timeout(30_000)
        self._conn.enable_load_extension(True)
        self._conn.load_extension(sqlite_vec.loadable_path())
        self._in_tx = False

    def execute(self, sql, params=()):
        # Group writes into one transaction until commit(), like sqlite3 does.
        if not self._in_tx and sql.lstrip().split(None, 1)[0].upper() in ("INSERT", "DELETE", "UPDATE"):
            self._conn.execute("BEGIN")
            self._in_tx = True
        return _ApswCursor(self._conn.execute(sql, params), self._conn)

    def commit(self):
        if self._in_tx:
            self._conn.execute("COMMIT")
            self._in_tx = False


def _connect(path):
    """(connection, sqlite-vec loaded?) using the best SQLite available."""
    conn = sqlite3.connect(str(path), timeout=30)
    if _try_load_vec(conn):
        return conn, True
    try:
        apsw_conn = _ApswConnection(str(path))
    except Exception:
        return conn, False
    conn.close()
    return apsw_conn, True


class Shelf:
    """Open one per thread."""

    def __init__(self, path=DB_PATH):
        with _SETUP:
            self._open(path)

    def _open(self, path):
        self.conn, loaded = _connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        # An index first built without sqlite-vec keeps its plain tables.
        row = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'vec_clip'"
        ).fetchone()
        self.has_vec = loaded and (row is None or "vec0" in row[0])
        self._create()

    def _create(self):
        c = self.conn
        c.execute(
            """CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                mtime REAL NOT NULL,
                preview TEXT
            )"""
        )
        if "ready" not in [r[1] for r in c.execute("PRAGMA table_info(files)").fetchall()]:
            c.execute("ALTER TABLE files ADD COLUMN ready INTEGER NOT NULL DEFAULT 1")
        if "added" not in [r[1] for r in c.execute("PRAGMA table_info(files)").fetchall()]:
            c.execute("ALTER TABLE files ADD COLUMN added REAL")
        c.execute(
            """CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY,
                file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                space TEXT NOT NULL
            )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS chunks_file ON chunks(file_id)")
        c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        # Exact-word search over names and contents (rowid = files.id). Meaning
        # search misses exact words like function names; this catches them.
        try:
            fresh = not c.execute(
                "SELECT name FROM sqlite_master WHERE name='words'").fetchone()
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS words USING "
                      "fts5(name, body, tokenize='unicode61 remove_diacritics 2')")
            if fresh:  # an older index: make every name searchable straight away
                for fid, name in c.execute("SELECT id, name FROM files").fetchall():
                    c.execute("INSERT INTO words (rowid, name, body) VALUES (?,?,'')",
                              (fid, _name_words(name)))
            self.has_words = True
        except Exception as exc:
            print(f"[lookhound] exact-word search unavailable: {exc}")
            self.has_words = False
        for space, dim in DIMS.items():
            if self.has_vec:
                c.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_{space} USING "
                    f"vec0(embedding float[{dim}] distance_metric=cosine)"
                )
            else:
                c.execute(
                    f"CREATE TABLE IF NOT EXISTS vec_{space} "
                    "(rowid INTEGER PRIMARY KEY, embedding BLOB NOT NULL)"
                )
        c.commit()

    # ---- writing -------------------------------------------------------

    def mtime_of(self, path):
        row = self.conn.execute(
            "SELECT mtime FROM files WHERE path=? AND ready=?", (path, READ)).fetchone()
        return row[0] if row else None

    def get_meta(self, key):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value))
        self.conn.commit()

    def state(self, path):
        """(mtime, ready) of a file we know, or None."""
        return self.conn.execute(
            "SELECT mtime, ready FROM files WHERE path=?", (path,)).fetchone()

    def note(self, path, name, kind, mtime, added):
        """Remember a file by name right away; put() fills in the rest later."""
        self.conn.execute(
            "INSERT INTO files (path, name, kind, mtime, ready, added) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET added=excluded.added WHERE added IS NULL",
            (path, name, kind, mtime, NAME_ONLY, added),
        )
        if self.has_words:
            fid = self.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()[0]
            if not self.conn.execute("SELECT 1 FROM words WHERE rowid=?", (fid,)).fetchone():
                self.conn.execute("INSERT INTO words (rowid, name, body) VALUES (?,?,'')",
                                  (fid, _name_words(name)))

    def commit(self):
        self.conn.commit()

    def put(self, path, name, kind, mtime, preview, vectors, commit=True, ready=READ, added=None,
            words=""):
        """vectors: {space: ndarray of shape (n, dim)}"""
        c = self.conn
        self._drop(path)
        cur = c.execute(
            "INSERT INTO files (path, name, kind, mtime, preview, ready, added) VALUES (?,?,?,?,?,?,?)",
            (path, name, kind, mtime, preview, ready, added),
        )
        self._add_vectors(cur.lastrowid, vectors)
        if self.has_words:
            c.execute("INSERT INTO words (rowid, name, body) VALUES (?,?,?)",
                      (cur.lastrowid, _name_words(name), words))
        if commit:
            c.commit()

    def finish(self, path, vectors):
        """Add the vectors a partly read file was waiting for."""
        row = self.conn.execute(
            "SELECT id FROM files WHERE path=? AND ready=?", (path, PARTLY)).fetchone()
        if row:
            self._add_vectors(row[0], vectors)
            self.conn.execute("UPDATE files SET ready=? WHERE id=?", (READ, row[0]))

    def _add_vectors(self, file_id, vectors):
        c = self.conn
        for space, rows in vectors.items():
            for vec in rows:
                chunk_id = c.execute(
                    "INSERT INTO chunks (file_id, space) VALUES (?,?)", (file_id, space)
                ).lastrowid
                c.execute(
                    f"INSERT INTO vec_{space} (rowid, embedding) VALUES (?,?)",
                    (chunk_id, np.asarray(vec, dtype=np.float32).tobytes()),
                )

    def _drop(self, path):
        c = self.conn
        row = c.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
        if not row:
            return
        for chunk_id, space in c.execute(
            "SELECT id, space FROM chunks WHERE file_id=?", (row[0],)
        ).fetchall():
            c.execute(f"DELETE FROM vec_{space} WHERE rowid=?", (chunk_id,))
        c.execute("DELETE FROM files WHERE id=?", (row[0],))
        if self.has_words:
            c.execute("DELETE FROM words WHERE rowid=?", (row[0],))

    def remove(self, path):
        """Forget a deleted file (or folder, with everything in it), including the
        preview picture made for it."""
        prefix = path.rstrip("/\\") + ("\\" if "\\" in path else "/")
        gone = self.conn.execute(
            "SELECT path, preview FROM files WHERE path = ? OR substr(path, 1, ?) = ?",
            (path, len(prefix), prefix),
        ).fetchall()
        for old, preview in gone:
            self._drop(old)
            if preview and Path(preview).parent == ICON_CACHE:
                Path(preview).unlink(missing_ok=True)
        self.conn.commit()

    def all_paths(self):
        return [r[0] for r in self.conn.execute("SELECT path FROM files")]

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    # ---- reading -------------------------------------------------------

    def nearest(self, space, query_vec, k=200):
        """Best cosine similarity per file: {file_id: similarity}."""
        q = np.asarray(query_vec, dtype=np.float32)
        if self.has_vec:
            rows = self.conn.execute(
                f"""SELECT chunks.file_id, v.distance FROM vec_{space} v
                    JOIN chunks ON chunks.id = v.rowid
                    WHERE v.embedding MATCH ? AND k = ?""",
                (q.tobytes(), k),
            ).fetchall()
            pairs = [(fid, 1.0 - dist) for fid, dist in rows]
        else:
            rows = self.conn.execute(
                f"SELECT chunks.file_id, v.embedding FROM vec_{space} v "
                "JOIN chunks ON chunks.id = v.rowid"
            ).fetchall()
            if not rows:
                return {}
            mat = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32)
            sims = mat.reshape(len(rows), -1) @ q
            top = np.argsort(-sims)[:k]
            pairs = [(rows[i][0], float(sims[i])) for i in top]
        best = {}
        for fid, sim in pairs:
            if sim > best.get(fid, -1.0):
                best[fid] = sim
        return best

    def newest(self, limit=3, days=30):
        """Files added in the last month, newest first (apps don't count as news)."""
        rows = self.conn.execute(
            "SELECT path, name, kind FROM files WHERE kind != 'app' AND added > ? "
            "ORDER BY added DESC LIMIT ?",
            (time.time() - days * 86400, limit),
        ).fetchall()
        return [{"path": r[0], "name": r[1], "kind": r[2]} for r in rows]

    def word_hits(self, text, limit=300):
        """Exact-word matches for a query: {file_id: (content score 0-1, share of
        the query's words found in the name)}. Names count far more than contents."""
        terms = query_terms(text)
        if not terms or not self.has_words:
            return {}
        match = " OR ".join(f'"{t}"*' for t in terms)
        try:
            rows = self.conn.execute(
                "SELECT rowid, bm25(words, 8.0, 1.0), name FROM words WHERE words MATCH ? "
                "ORDER BY 2 LIMIT ?", (match, limit)).fetchall()
        except Exception:
            return {}
        if not rows:
            return {}
        best = rows[0][1] or -1.0  # bm25: more negative is better
        hits = {}
        for fid, rank, name in rows:
            name_terms = set(query_terms(name, keep_short=True))
            in_name = sum(any(n.startswith(t) for n in name_terms) for t in terms) / len(terms)
            hits[fid] = (max(0.0, rank / best) if best else 0.0, in_name)
        return hits

    def describe(self, file_ids):
        if not file_ids:
            return {}
        marks = ",".join("?" * len(file_ids))
        rows = self.conn.execute(
            f"SELECT id, path, name, kind, preview FROM files WHERE id IN ({marks})",
            list(file_ids),
        ).fetchall()
        return {
            r[0]: {"path": r[1], "name": r[2], "kind": r[3], "preview": r[4]} for r in rows
        }


STOP_WORDS = {
    "a", "an", "and", "the", "of", "for", "to", "in", "on", "my", "that", "this",
    "with", "from", "is", "it", "me", "i", "find", "file", "files", "where",
}


def query_terms(text, keep_short=False):
    """Lowercase words of a query or name, split on punctuation, underscores,
    dashes and camelCase, without filler words."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    words = re.findall(r"[^\W_]+", text.lower())
    if keep_short:
        return words
    return [w for w in words if w not in STOP_WORDS and (len(w) > 1 or w.isdigit())]


def _name_words(name):
    """A file name as searchable words: report_final-v2.pdf -> report final v2 pdf."""
    return " ".join(query_terms(name, keep_short=True))
