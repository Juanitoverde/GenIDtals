"""Encrypted template storage.

Templates (feature vectors only - raw images are never written) are
serialized to JSON and encrypted with Fernet (AES-128-CBC + HMAC-SHA256).
Each record additionally carries an HMAC binding it to its user ID, so a
template file copied onto another user's slot fails verification. File
names are keyed hashes of the user ID, so the store directory does not
reveal who is enrolled.

Keys come from either a passphrase (``GENIDTALS_PASSPHRASE``, stretched with
PBKDF2-SHA256 and a per-store salt) or a randomly generated key file kept
in the store directory with 0600 permissions.
"""

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path

import numpy as np
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS = 600_000
KEY_FILE = "store.key"
SALT_FILE = "store.salt"
CHECK_FILE = "store.check"
SUFFIX = ".tpl"


class StorageError(Exception):
    pass


def _encode(obj):
    if isinstance(obj, np.ndarray):
        return {"__nd__": obj.dtype.str, "shape": obj.shape,
                "data": base64.b64encode(obj.tobytes()).decode()}
    if isinstance(obj, dict):
        return {k: _encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_encode(v) for v in obj]
    return obj


def _decode(obj):
    if isinstance(obj, dict):
        if "__nd__" in obj:
            arr = np.frombuffer(base64.b64decode(obj["data"]), dtype=np.dtype(obj["__nd__"]))
            return arr.reshape(obj["shape"]).copy()
        return {k: _decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode(v) for v in obj]
    return obj


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


class TemplateStore:
    def __init__(self, root: str | Path, passphrase: str | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        passphrase = passphrase if passphrase is not None else os.environ.get("GENIDTALS_PASSPHRASE")
        master = self._derive(passphrase) if passphrase else self._load_keyfile()
        # Separate subkeys for encryption, integrity tags and file naming.
        self._fernet = Fernet(base64.urlsafe_b64encode(self._subkey(master, b"enc")))
        self._mac_key = self._subkey(master, b"mac")
        self._name_key = self._subkey(master, b"name")
        self._verify_key(self._subkey(master, b"check"))

    @staticmethod
    def _subkey(master: bytes, label: bytes) -> bytes:
        return hmac.new(master, b"genidtals/" + label, hashlib.sha256).digest()

    def _verify_key(self, check: bytes) -> None:
        """Fail loudly on a wrong key instead of making every user look unenrolled."""
        path = self.root / CHECK_FILE
        if not path.exists():
            _write_private(path, check)
        elif not hmac.compare_digest(path.read_bytes(), check):
            raise StorageError(f"Wrong passphrase or key for template store {self.root}")

    def _derive(self, passphrase: str) -> bytes:
        salt_path = self.root / SALT_FILE
        if salt_path.exists():
            salt = salt_path.read_bytes()
        else:
            salt = os.urandom(16)
            _write_private(salt_path, salt)
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                         iterations=PBKDF2_ITERATIONS)
        return kdf.derive(passphrase.encode())

    def _load_keyfile(self) -> bytes:
        key_path = self.root / KEY_FILE
        if key_path.exists():
            key = key_path.read_bytes()
            if len(key) != 32:
                raise StorageError(f"Corrupt key file: {key_path}")
            return key
        key = os.urandom(32)
        _write_private(key_path, key)
        return key

    def _path(self, user_id: str) -> Path:
        name = hmac.new(self._name_key, user_id.encode(), hashlib.sha256).hexdigest()[:32]
        return self.root / (name + SUFFIX)

    def _tag(self, user_id: str, token: bytes) -> str:
        return hmac.new(self._mac_key, user_id.encode() + b"\0" + token, hashlib.sha256).hexdigest()

    def save(self, user_id: str, record: dict) -> None:
        payload = json.dumps(_encode({"user_id": user_id, **record})).encode()
        token = self._fernet.encrypt(payload)
        blob = json.dumps({"v": 1, "token": token.decode(), "hmac": self._tag(user_id, token)})
        tmp = self._path(user_id).with_suffix(".tmp")
        _write_private(tmp, blob.encode())
        os.replace(tmp, self._path(user_id))

    def load(self, user_id: str) -> dict | None:
        path = self._path(user_id)
        if not path.exists():
            return None
        try:
            blob = json.loads(path.read_text())
            token = blob["token"].encode()
        except (ValueError, KeyError) as e:
            raise StorageError(f"Malformed template file for {user_id!r}") from e
        if not hmac.compare_digest(blob.get("hmac", ""), self._tag(user_id, token)):
            raise StorageError(f"Integrity check failed for {user_id!r}")
        try:
            record = _decode(json.loads(self._fernet.decrypt(token)))
        except InvalidToken as e:
            raise StorageError(f"Could not decrypt template for {user_id!r} (wrong key?)") from e
        if record.pop("user_id", None) != user_id:
            raise StorageError(f"Template user mismatch for {user_id!r}")
        return record

    def delete(self, user_id: str) -> bool:
        path = self._path(user_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def count(self) -> int:
        return sum(1 for _ in self.root.glob("*" + SUFFIX))
