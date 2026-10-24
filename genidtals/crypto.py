"""Key management and authenticated encryption of templates.

Sealed blob layout (all integers unsigned)::

    "GIDT" | format (1) | key-id length (1) | key-id | salt (16) | nonce (12) | AES-256-GCM ciphertext+tag

* Every blob is encrypted under a fresh key derived with HKDF-SHA256 from
  the master key and a random salt, so no AES-GCM key ever approaches its
  nonce-reuse or data-volume limits.
* The header and a caller-supplied *context* (record type + subject ID) are
  authenticated as associated data. A blob moved to another user, or with
  any byte altered, fails to open.
* Blobs name the key they were sealed with, which is what makes key
  rotation possible: old keys stay available for opening while new data is
  sealed under the current key.

Store indexes (where a user's record lives) are HMACs of the user ID under
a per-key index subkey, so storage never contains user IDs in the clear.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import stat
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .errors import KeyManagementError, TemplateError

log = logging.getLogger("genidtals")

MAGIC = b"GIDT"
FORMAT = 2
KEY_BYTES = 32
SALT_BYTES = 16
NONCE_BYTES = 12
MAX_BLOB_BYTES = 8 * 1024 * 1024


def generate_key() -> bytes:
    """A new random 256-bit master key."""
    return os.urandom(KEY_BYTES)


def encode_key(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode().rstrip("=")


def decode_key(text: str) -> bytes:
    text = text.strip()
    try:
        key = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError as e:
        raise KeyManagementError("Key is not valid base64") from e
    if len(key) != KEY_BYTES:
        raise KeyManagementError(f"Key must be {KEY_BYTES} bytes, got {len(key)}")
    return key


def key_id_for(key: bytes) -> str:
    """Public, non-reversible identifier of a key (safe to log and store)."""
    return hashlib.sha256(b"genidtals/key-id\0" + key).hexdigest()[:16]


class KeyProvider(ABC):
    """Supplies master keys. Implement this to integrate a KMS or HSM."""

    @property
    @abstractmethod
    def current_key_id(self) -> str:
        """ID of the key new data is sealed under."""

    @abstractmethod
    def get_key(self, key_id: str) -> bytes:
        """Return the 32-byte key for ``key_id`` or raise KeyManagementError."""

    @abstractmethod
    def key_ids(self) -> list[str]:
        """All available key IDs, current first."""


class StaticKeyProvider(KeyProvider):
    """Keys held in memory. The first key is current; the rest are for opening old data."""

    def __init__(self, keys: Iterable[bytes]):
        keys = list(keys)
        if not keys:
            raise KeyManagementError("At least one key is required")
        self._keys = {}
        for k in keys:
            if not isinstance(k, (bytes, bytearray)) or len(k) != KEY_BYTES:
                raise KeyManagementError(f"Keys must be {KEY_BYTES} bytes")
            self._keys[key_id_for(bytes(k))] = bytes(k)
        self._order = list(self._keys)

    @property
    def current_key_id(self) -> str:
        return self._order[0]

    def get_key(self, key_id: str) -> bytes:
        try:
            return self._keys[key_id]
        except KeyError:
            raise KeyManagementError(f"Key {key_id} is not available") from None

    def key_ids(self) -> list[str]:
        return list(self._order)

    @classmethod
    def from_env(cls, var: str = "GENIDTALS_KEYS") -> "StaticKeyProvider":
        """Comma-separated base64 keys from an environment variable, current first."""
        value = os.environ.get(var, "")
        if not value.strip():
            raise KeyManagementError(f"Environment variable {var} is not set")
        return cls(decode_key(part) for part in value.split(",") if part.strip())

    @classmethod
    def from_passphrase(cls, passphrase: str, salt: bytes) -> "StaticKeyProvider":
        """Derive the key with Argon2id. ``salt`` (>= 16 random bytes) must be stored and reused."""
        if len(passphrase) < 12:
            raise KeyManagementError("Passphrase must be at least 12 characters")
        if len(salt) < 16:
            raise KeyManagementError("Salt must be at least 16 bytes")
        kdf = Argon2id(salt=salt, length=KEY_BYTES, iterations=3, lanes=4, memory_cost=64 * 1024)
        return cls([kdf.derive(passphrase.encode())])


def _write_private(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class KeyringFile(KeyProvider):
    """Keys persisted in a JSON file readable only by its owner.

    Created with a fresh key on first use. Keep it outside the template
    store (ideally on a different volume or secret mount) so that a copy of
    the store alone is useless.
    """

    def __init__(self, path, create: bool = True):
        self.path = Path(path).expanduser()
        if not self.path.exists():
            if not create:
                raise KeyManagementError(f"Keyring not found: {self.path}")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            key = generate_key()
            self._data = {"format": 1, "current": key_id_for(key), "keys": {key_id_for(key): encode_key(key)}}
            self._save()
            log.info("Created keyring %s", self.path)
        else:
            self._check_permissions()
            try:
                self._data = json.loads(self.path.read_text())
                keys = {kid: decode_key(v) for kid, v in self._data["keys"].items()}
            except (OSError, ValueError, KeyError, TypeError) as e:
                raise KeyManagementError(f"Corrupt keyring {self.path}: {e}") from e
            if self._data.get("current") not in keys:
                raise KeyManagementError(f"Keyring {self.path} has no valid current key")
            for kid, key in keys.items():
                if key_id_for(key) != kid:
                    raise KeyManagementError(f"Keyring {self.path} entry {kid} does not match its key")
        self._keys = {kid: decode_key(v) for kid, v in self._data["keys"].items()}

    def _check_permissions(self) -> None:
        if os.name != "posix":
            return
        mode = self.path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise KeyManagementError(
                f"Keyring {self.path} is accessible by other users; run: chmod 600 {self.path}")

    def _save(self) -> None:
        _write_private(self.path, json.dumps(self._data, indent=2).encode())

    @property
    def current_key_id(self) -> str:
        return self._data["current"]

    def get_key(self, key_id: str) -> bytes:
        try:
            return self._keys[key_id]
        except KeyError:
            raise KeyManagementError(f"Key {key_id} is not in keyring {self.path}") from None

    def key_ids(self) -> list[str]:
        cur = self.current_key_id
        return [cur] + [k for k in self._keys if k != cur]

    def rotate(self) -> str:
        """Add a new current key; older keys remain for opening existing data."""
        key = generate_key()
        kid = key_id_for(key)
        self._keys[kid] = key
        self._data["keys"][kid] = encode_key(key)
        self._data["current"] = kid
        self._save()
        return kid

    def retire(self, key_id: str) -> None:
        """Remove an old key (after all data has been re-sealed)."""
        if key_id == self.current_key_id:
            raise KeyManagementError("Cannot retire the current key")
        self._keys.pop(key_id, None)
        self._data["keys"].pop(key_id, None)
        self._save()


class Sealer:
    """Authenticated encryption and pseudonymous indexing over a KeyProvider."""

    def __init__(self, keys: KeyProvider):
        self.keys = keys
        self._index_keys: dict[str, bytes] = {}

    @staticmethod
    def _derive(key: bytes, salt: Optional[bytes], info: bytes) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=salt, info=info).derive(key)

    def seal(self, plaintext: bytes, context: bytes) -> bytes:
        kid = self.keys.current_key_id
        kid_b = kid.encode()
        salt, nonce = os.urandom(SALT_BYTES), os.urandom(NONCE_BYTES)
        header = MAGIC + bytes([FORMAT, len(kid_b)]) + kid_b + salt + nonce
        key = self._derive(self.keys.get_key(kid), salt, b"genidtals/seal/v2")
        return header + AESGCM(key).encrypt(nonce, plaintext, header + b"\0" + context)

    @staticmethod
    def key_id_of(blob: bytes) -> str:
        if len(blob) < 6 or blob[:4] != MAGIC:
            raise TemplateError("Not a GenIDtals sealed blob")
        if blob[4] != FORMAT:
            raise TemplateError(f"Unsupported sealed blob format {blob[4]}")
        n = blob[5]
        if len(blob) < 6 + n + SALT_BYTES + NONCE_BYTES + 16:
            raise TemplateError("Sealed blob is truncated")
        return blob[6:6 + n].decode("ascii", errors="replace")

    def open(self, blob: bytes, context: bytes) -> tuple[bytes, str]:
        """Return (plaintext, key_id) or raise TemplateError."""
        blob = bytes(blob)
        if len(blob) > MAX_BLOB_BYTES:
            raise TemplateError("Sealed blob is too large")
        kid = self.key_id_of(blob)
        off = 6 + len(kid.encode())
        salt = blob[off:off + SALT_BYTES]
        nonce = blob[off + SALT_BYTES:off + SALT_BYTES + NONCE_BYTES]
        header = blob[:off + SALT_BYTES + NONCE_BYTES]
        try:
            master = self.keys.get_key(kid)
        except KeyManagementError as e:
            raise TemplateError(f"Template was sealed with unavailable key {kid}") from e
        key = self._derive(master, salt, b"genidtals/seal/v2")
        try:
            return AESGCM(key).decrypt(nonce, blob[len(header):], header + b"\0" + context), kid
        except InvalidTag:
            raise TemplateError(
                "Template failed authentication (tampered, or belongs to another subject)") from None

    def index(self, label: str, key_id: Optional[str] = None) -> str:
        """Keyed, non-reversible 256-bit identifier for ``label``."""
        kid = key_id or self.keys.current_key_id
        if kid not in self._index_keys:
            self._index_keys[kid] = self._derive(self.keys.get_key(kid), None, b"genidtals/index/v2")
        return hmac.new(self._index_keys[kid], label.encode(), hashlib.sha256).hexdigest()
