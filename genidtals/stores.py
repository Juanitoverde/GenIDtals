"""Pluggable storage for sealed records.

A store is a byte-oriented key/value map. Values are already encrypted and
authenticated by the engine, and keys are opaque HMAC digests, so a store
never sees biometric data or user IDs. To keep templates in your own
database, cache or object store, subclass :class:`TemplateStore` and
implement four methods.
"""

import os
import re
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator, Optional

from .errors import StorageError

# Record keys: a one-letter namespace, a slash and a SHA-256 hex digest.
KEY_PATTERN = re.compile(r"^[a-z]/[0-9a-f]{64}$")


def check_key(key: str) -> str:
    if not isinstance(key, str) or not KEY_PATTERN.match(key):
        raise StorageError(f"Invalid record key {key!r}")
    return key


class TemplateStore(ABC):
    """Byte key/value storage. Implementations must be safe to call from multiple threads."""

    @abstractmethod
    def get(self, key: str) -> Optional[bytes]:
        """Return the value for ``key`` or ``None``."""

    @abstractmethod
    def put(self, key: str, value: bytes) -> None:
        """Create or atomically replace ``key``."""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Remove ``key``; return whether it existed."""

    @abstractmethod
    def keys(self, prefix: str = "") -> Iterator[str]:
        """Iterate over keys starting with ``prefix``."""

    def count(self, prefix: str = "t/") -> int:
        return sum(1 for _ in self.keys(prefix))


class MemoryStore(TemplateStore):
    """Process-local store, for tests and ephemeral deployments."""

    def __init__(self):
        self._data: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            return self._data.get(check_key(key))

    def put(self, key, value):
        with self._lock:
            self._data[check_key(key)] = bytes(value)

    def delete(self, key):
        with self._lock:
            return self._data.pop(check_key(key), None) is not None

    def keys(self, prefix=""):
        with self._lock:
            return iter([k for k in self._data if k.startswith(prefix)])


class FileStore(TemplateStore):
    """One file per record under a directory, written atomically with mode 0600."""

    def __init__(self, root):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, key: str) -> Path:
        ns, digest = check_key(key).split("/")
        return self.root / ns / f"{digest}.bin"

    def get(self, key):
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            return None
        except OSError as e:
            raise StorageError(f"Could not read record: {e}") from e

    def put(self, key, value):
        path = self._path(key)
        try:
            path.parent.mkdir(exist_ok=True, mode=0o700)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(value)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError as e:
            raise StorageError(f"Could not write record: {e}") from e

    def delete(self, key):
        try:
            self._path(key).unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as e:
            raise StorageError(f"Could not delete record: {e}") from e

    def keys(self, prefix=""):
        for ns_dir in sorted(p for p in self.root.iterdir() if p.is_dir() and len(p.name) == 1):
            for f in sorted(ns_dir.glob("*.bin")):
                key = f"{ns_dir.name}/{f.stem}"
                if key.startswith(prefix) and KEY_PATTERN.match(key):
                    yield key


class SQLiteStore(TemplateStore):
    """Single-file SQLite database; safe across threads and processes."""

    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            # Create the file with owner-only permissions before SQLite opens it.
            os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600))
        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS records "
                "(key TEXT PRIMARY KEY, value BLOB NOT NULL, updated REAL NOT NULL)")
            self._conn.commit()
        except sqlite3.Error as e:
            raise StorageError(f"Could not open SQLite store {self.path}: {e}") from e

    def _run(self, sql, params=(), fetch=False):
        try:
            with self._lock:
                cur = self._conn.execute(sql, params)
                rows = cur.fetchall() if fetch else cur.rowcount
                self._conn.commit()
                return rows
        except sqlite3.Error as e:
            raise StorageError(f"SQLite error: {e}") from e

    def get(self, key):
        rows = self._run("SELECT value FROM records WHERE key = ?", (check_key(key),), fetch=True)
        return bytes(rows[0][0]) if rows else None

    def put(self, key, value):
        self._run("INSERT INTO records (key, value, updated) VALUES (?, ?, ?) ON CONFLICT(key) "
                  "DO UPDATE SET value = excluded.value, updated = excluded.updated",
                  (check_key(key), bytes(value), time.time()))

    def delete(self, key):
        return self._run("DELETE FROM records WHERE key = ?", (check_key(key),)) > 0

    def keys(self, prefix=""):
        pattern = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self._run("SELECT key FROM records WHERE key LIKE ? ESCAPE '\\' ORDER BY key",
                         (pattern,), fetch=True)
        return iter([r[0] for r in rows])

    def close(self):
        with self._lock:
            self._conn.close()
