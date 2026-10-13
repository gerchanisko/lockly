"""SQLite storage for the vault.

This layer only ever sees opaque ciphertext and non-secret header values. It
knows nothing about keys or entry contents.

Schema (format version 1)::

    meta(key TEXT PRIMARY KEY, value BLOB)        -- vault header values
    entries(id TEXT PRIMARY KEY,                   -- random 128-bit hex id
            nonce BLOB, ciphertext BLOB)           -- XChaCha20-Poly1305
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

SQLITE_MAGIC = b"SQLite format 3\x00"


class DatabaseError(Exception):
    pass


class VaultExistsError(DatabaseError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY NOT NULL,
    value BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    id         TEXT PRIMARY KEY NOT NULL,
    nonce      BLOB NOT NULL,
    ciphertext BLOB NOT NULL
);
"""


def _restrict_permissions(path: Path) -> None:
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)


class Database:
    """Thin, thread-safe wrapper around a SQLite connection."""

    def __init__(self, path: Path, conn: sqlite3.Connection):
        self.path = path
        self._conn = conn
        self._lock = threading.RLock()

    # Opening -------------------------------------------------------------
    @classmethod
    def _connect(cls, path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        # Overwrite deleted content instead of leaving it in free pages.
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    @classmethod
    def open(cls, path: Path) -> "Database":
        path = Path(path)
        if not path.is_file():
            raise DatabaseError(f"No vault found at {path}")
        with open(path, "rb") as fh:
            if fh.read(len(SQLITE_MAGIC)) != SQLITE_MAGIC:
                raise DatabaseError("The file is not a Lockly vault")
        _restrict_permissions(path)
        try:
            conn = cls._connect(path)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError as exc:
            raise DatabaseError("The vault file is damaged or not a Lockly vault") from exc
        if not {"meta", "entries"} <= tables:
            conn.close()
            raise DatabaseError("The file is not a Lockly vault")
        return cls(path, conn)

    @classmethod
    def create(cls, path: Path, meta: dict[str, bytes | str | int]) -> "Database":
        """Create a new vault file without ever clobbering an existing one.

        The database is fully written to a temporary file in the same
        directory and then hard-linked into place. ``os.link`` fails if the
        destination exists, so an existing vault can never be overwritten.
        """
        path = Path(path)
        ensure_private_dir(path.parent)
        if path.exists():
            raise VaultExistsError(f"A vault already exists at {path}")
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            conn = cls._connect(tmp)
            conn.executescript(_SCHEMA)
            with conn:
                conn.execute("BEGIN")
                conn.executemany(
                    "INSERT INTO meta(key, value) VALUES (?, ?)", list(meta.items())
                )
            conn.close()
            try:
                os.link(tmp, path)
            except FileExistsError as exc:
                raise VaultExistsError(f"A vault already exists at {path}") from exc
        finally:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
        _fsync_dir(path.parent)
        return cls.open(path)

    def close(self) -> None:
        with self._lock:
            with contextlib.suppress(sqlite3.Error):
                self._conn.close()

    # Transactions --------------------------------------------------------
    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    # Meta ----------------------------------------------------------------
    def get_meta(self) -> dict[str, bytes | str | int]:
        with self._lock:
            return {k: v for k, v in self._conn.execute("SELECT key, value FROM meta")}

    def set_meta(self, values: dict[str, bytes | str | int], conn: sqlite3.Connection | None = None) -> None:
        def write(c: sqlite3.Connection) -> None:
            c.executemany(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                list(values.items()),
            )

        if conn is not None:
            write(conn)
        else:
            with self.transaction() as c:
                write(c)

    # Entries -------------------------------------------------------------
    def iter_entries(self) -> list[tuple[str, bytes, bytes]]:
        with self._lock:
            return [
                (row[0], bytes(row[1]), bytes(row[2]))
                for row in self._conn.execute("SELECT id, nonce, ciphertext FROM entries")
            ]

    def put_entry(self, entry_id: str, nonce: bytes, ciphertext: bytes, conn: sqlite3.Connection | None = None) -> None:
        sql = (
            "INSERT INTO entries(id, nonce, ciphertext) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET nonce = excluded.nonce, ciphertext = excluded.ciphertext"
        )
        if conn is not None:
            conn.execute(sql, (entry_id, nonce, ciphertext))
        else:
            with self.transaction() as c:
                c.execute(sql, (entry_id, nonce, ciphertext))

    def delete_entry(self, entry_id: str, conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        else:
            with self.transaction() as c:
                c.execute("DELETE FROM entries WHERE id = ?", (entry_id,))

    def entry_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]


def _fsync_dir(directory: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_write(path: Path, data: bytes, *, overwrite: bool = False, mode: int = 0o600) -> None:
    """Write ``data`` to ``path`` atomically.

    The data goes to a temporary file in the same directory, is fsync'ed and
    then renamed over the destination. With ``overwrite=False`` an existing
    file is never replaced.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite and path.exists():
        raise FileExistsError(f"{path} already exists")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if overwrite:
            os.replace(tmp, path)
        else:
            os.link(tmp, path)
            tmp.unlink()
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise
    _fsync_dir(path.parent)
