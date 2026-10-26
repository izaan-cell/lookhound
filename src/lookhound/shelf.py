"""One SQLite file holds everything: file rows, and their vectors in sqlite-vec
tables. If the sqlite-vec extension can't load, vectors go in plain BLOB tables
and are compared with numpy instead: slower, but the same answers."""
import sqlite3

import numpy as np

from .brain import DIMS
from .settings import DB_PATH

SPACES = tuple(DIMS)


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
        c.execute(
            """CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY,
                file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                space TEXT NOT NULL
            )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS chunks_file ON chunks(file_id)")
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
        row = self.conn.execute("SELECT mtime FROM files WHERE path=?", (path,)).fetchone()
        return row[0] if row else None

    def put(self, path, name, kind, mtime, preview, vectors):
        """vectors: {space: ndarray of shape (n, dim)}"""
        c = self.conn
        self._drop(path)
        cur = c.execute(
            "INSERT INTO files (path, name, kind, mtime, preview) VALUES (?,?,?,?,?)",
            (path, name, kind, mtime, preview),
        )
        file_id = cur.lastrowid
        for space, rows in vectors.items():
            for vec in rows:
                chunk_id = c.execute(
                    "INSERT INTO chunks (file_id, space) VALUES (?,?)", (file_id, space)
                ).lastrowid
                c.execute(
                    f"INSERT INTO vec_{space} (rowid, embedding) VALUES (?,?)",
                    (chunk_id, np.asarray(vec, dtype=np.float32).tobytes()),
                )
        c.commit()

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

    def remove(self, path):
        self._drop(path)
        # A deleted folder takes everything inside it along.
        prefix = path.rstrip("/\\") + ("\\" if "\\" in path else "/")
        for (child,) in self.conn.execute(
            "SELECT path FROM files WHERE substr(path, 1, ?) = ?", (len(prefix), prefix)
        ).fetchall():
            self._drop(child)
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

    def newest(self, limit=3):
        """The most recently changed files (apps don't count as news)."""
        rows = self.conn.execute(
            "SELECT path, name, kind FROM files WHERE kind != 'app' "
            "ORDER BY mtime DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"path": r[0], "name": r[1], "kind": r[2]} for r in rows]

    def name_hits(self, text, limit=50):
        like = "%" + text.replace("%", "").replace("_", "") + "%"
        return [
            r[0]
            for r in self.conn.execute(
                "SELECT id FROM files WHERE name LIKE ? LIMIT ?", (like, limit)
            )
        ]

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
