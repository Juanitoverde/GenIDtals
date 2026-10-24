import os

import pytest

from genidtals import (
    FileStore,
    KeyManagementError,
    KeyringFile,
    MemoryStore,
    SQLiteStore,
    StaticKeyProvider,
    StorageError,
    TemplateError,
    encode_key,
    generate_key,
)
from genidtals.crypto import Sealer, key_id_for

# ------------------------------------------------------------------ sealing


def test_seal_roundtrip(keys):
    s = Sealer(keys)
    blob = s.seal(b"secret", b"ctx")
    assert b"secret" not in blob
    assert s.open(blob, b"ctx") == (b"secret", keys.current_key_id)


def test_wrong_context_fails(keys):
    s = Sealer(keys)
    with pytest.raises(TemplateError):
        s.open(s.seal(b"secret", b"template\0alice"), b"template\0mallory")


@pytest.mark.parametrize("pos", [0, 5, 10, 30, -1])
def test_any_tampering_fails(keys, pos):
    s = Sealer(keys)
    blob = bytearray(s.seal(b"secret payload", b"ctx"))
    blob[pos] ^= 0x01
    with pytest.raises(TemplateError):
        s.open(bytes(blob), b"ctx")


def test_truncated_and_garbage_fail(keys):
    s = Sealer(keys)
    for bad in (b"", b"GIDT", b"not a template at all", s.seal(b"x", b"c")[:20]):
        with pytest.raises(TemplateError):
            s.open(bad, b"c")


def test_seals_are_randomized(keys):
    s = Sealer(keys)
    assert s.seal(b"same", b"c") != s.seal(b"same", b"c")


def test_rotation_opens_old_and_seals_new():
    old, new = generate_key(), generate_key()
    blob = Sealer(StaticKeyProvider([old])).seal(b"data", b"c")
    rotated = Sealer(StaticKeyProvider([new, old]))
    assert rotated.open(blob, b"c")[1] == key_id_for(old)
    assert Sealer.key_id_of(rotated.seal(b"data", b"c")) == key_id_for(new)
    with pytest.raises(TemplateError, match="unavailable key"):
        Sealer(StaticKeyProvider([new])).open(blob, b"c")


def test_index_is_keyed_and_stable(keys):
    a, b = Sealer(keys), Sealer(StaticKeyProvider([generate_key()]))
    assert a.index("user:alice") == a.index("user:alice")
    assert a.index("user:alice") != b.index("user:alice")
    assert "alice" not in a.index("user:alice")


# --------------------------------------------------------------- providers


def test_static_provider_validation():
    with pytest.raises(KeyManagementError):
        StaticKeyProvider([])
    with pytest.raises(KeyManagementError):
        StaticKeyProvider([b"short"])


def test_from_env(monkeypatch):
    k1, k2 = generate_key(), generate_key()
    monkeypatch.setenv("GENIDTALS_KEYS", f"{encode_key(k1)}, {encode_key(k2)}")
    p = StaticKeyProvider.from_env()
    assert p.current_key_id == key_id_for(k1)
    assert p.key_ids() == [key_id_for(k1), key_id_for(k2)]
    monkeypatch.setenv("GENIDTALS_KEYS", "not-base64!")
    with pytest.raises(KeyManagementError):
        StaticKeyProvider.from_env()


def test_from_passphrase_is_deterministic():
    salt = os.urandom(16)
    a = StaticKeyProvider.from_passphrase("correct horse battery", salt)
    b = StaticKeyProvider.from_passphrase("correct horse battery", salt)
    assert a.current_key_id == b.current_key_id
    with pytest.raises(KeyManagementError):
        StaticKeyProvider.from_passphrase("short", salt)


def test_keyring_file_lifecycle(tmp_path):
    path = tmp_path / "keyring.json"
    ring = KeyringFile(path)
    assert path.stat().st_mode & 0o077 == 0
    first = ring.current_key_id
    second = ring.rotate()
    reopened = KeyringFile(path)
    assert reopened.key_ids() == [second, first]
    reopened.retire(first)
    assert KeyringFile(path).key_ids() == [second]
    with pytest.raises(KeyManagementError):
        reopened.retire(second)


def test_keyring_refuses_loose_permissions(tmp_path):
    path = tmp_path / "keyring.json"
    KeyringFile(path)
    path.chmod(0o644)
    with pytest.raises(KeyManagementError, match="chmod 600"):
        KeyringFile(path)


def test_keyring_missing_without_create(tmp_path):
    with pytest.raises(KeyManagementError):
        KeyringFile(tmp_path / "nope.json", create=False)


# ------------------------------------------------------------------ stores

KEY = "t/" + "ab" * 32
KEY2 = "a/" + "cd" * 32


@pytest.fixture(params=["memory", "file", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryStore()
    if request.param == "file":
        return FileStore(tmp_path / "files")
    return SQLiteStore(tmp_path / "db.sqlite")


def test_store_crud(store):
    assert store.get(KEY) is None
    store.put(KEY, b"one")
    store.put(KEY, b"two")
    store.put(KEY2, b"x")
    assert store.get(KEY) == b"two"
    assert list(store.keys("t/")) == [KEY]
    assert sorted(store.keys()) == sorted([KEY, KEY2])
    assert store.count("t/") == 1
    assert store.delete(KEY) and not store.delete(KEY)
    assert store.get(KEY) is None


UNSAFE_KEYS = ["../etc/passwd", "t/../../x", "t/ABC", "x", "t/" + "g" * 64, "tt/" + "a" * 64]


@pytest.mark.parametrize("bad", UNSAFE_KEYS)
def test_store_rejects_unsafe_keys(store, bad):
    with pytest.raises(StorageError):
        store.put(bad, b"x")


def test_file_store_permissions(tmp_path):
    store = FileStore(tmp_path / "files")
    store.put(KEY, b"x")
    (path,) = (tmp_path / "files" / "t").glob("*.bin")
    assert path.stat().st_mode & 0o077 == 0


def test_sqlite_store_persists(tmp_path):
    SQLiteStore(tmp_path / "db.sqlite").put(KEY, b"persisted")
    assert SQLiteStore(tmp_path / "db.sqlite").get(KEY) == b"persisted"
    assert (tmp_path / "db.sqlite").stat().st_mode & 0o077 == 0
