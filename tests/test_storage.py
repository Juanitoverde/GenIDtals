import json

import numpy as np
import pytest

from genidtals.storage import StorageError, TemplateStore


def record():
    return {"templates": [{"v": np.arange(6, dtype=np.float32).reshape(2, 3)}], "n": 1}


def test_roundtrip(tmp_path):
    store = TemplateStore(tmp_path)
    store.save("alice", record())
    loaded = store.load("alice")
    np.testing.assert_array_equal(loaded["templates"][0]["v"], record()["templates"][0]["v"])
    assert loaded["n"] == 1


def test_files_are_encrypted_and_anonymous(tmp_path):
    store = TemplateStore(tmp_path)
    store.save("alice", record())
    (path,) = tmp_path.glob("*.tpl")
    assert "alice" not in path.name
    assert b"alice" not in path.read_bytes()
    assert path.stat().st_mode & 0o077 == 0
    assert (tmp_path / "store.key").stat().st_mode & 0o077 == 0


def test_missing_user_returns_none(tmp_path):
    assert TemplateStore(tmp_path).load("nobody") is None


def test_wrong_key_fails(tmp_path):
    TemplateStore(tmp_path, passphrase="correct horse").save("alice", record())
    with pytest.raises(StorageError):
        TemplateStore(tmp_path, passphrase="battery staple").load("alice")


def test_passphrase_store_reopens(tmp_path):
    TemplateStore(tmp_path, passphrase="correct horse").save("alice", record())
    assert TemplateStore(tmp_path, passphrase="correct horse").load("alice")["n"] == 1


def test_swapped_template_fails_integrity(tmp_path):
    store = TemplateStore(tmp_path)
    store.save("alice", record())
    store.save("mallory", record())
    store._path("alice").write_bytes(store._path("mallory").read_bytes())
    with pytest.raises(StorageError, match="Integrity"):
        store.load("alice")


def test_tampered_token_fails(tmp_path):
    store = TemplateStore(tmp_path)
    store.save("alice", record())
    path = store._path("alice")
    blob = json.loads(path.read_text())
    blob["token"] = blob["token"][:-4] + "AAAA"
    path.write_text(json.dumps(blob))
    with pytest.raises(StorageError):
        store.load("alice")


def test_delete(tmp_path):
    store = TemplateStore(tmp_path)
    store.save("alice", record())
    assert store.count() == 1
    assert store.delete("alice")
    assert not store.delete("alice")
    assert store.count() == 0
