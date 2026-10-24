"""Plug in your own storage backend (Redis, DynamoDB, Postgres, S3, ...).

A store only ever sees opaque keys and encrypted bytes. Implement four
methods; this example wraps a dict to stand in for a key/value service.
Run:  python examples/custom_store.py
"""

import threading

import numpy as np

from genidtals import BiometricEngine, StaticKeyProvider, TemplateStore, generate_key, synthetic
from genidtals.stores import check_key


class KeyValueServiceStore(TemplateStore):
    def __init__(self, client: dict):
        self.client = client  # e.g. redis.Redis(...)
        self.lock = threading.Lock()

    def get(self, key):
        return self.client.get(check_key(key))           # redis: self.client.get(key)

    def put(self, key, value):
        with self.lock:
            self.client[check_key(key)] = value           # redis: self.client.set(key, value)

    def delete(self, key):
        with self.lock:
            return self.client.pop(check_key(key), None) is not None   # redis: self.client.delete(key) > 0

    def keys(self, prefix=""):
        return iter([k for k in list(self.client) if k.startswith(prefix)])  # redis: scan_iter(prefix + "*")


backend = {}
engine = BiometricEngine("standard", store=KeyValueServiceStore(backend),
                         keys=StaticKeyProvider([generate_key()]))

rng = np.random.default_rng(2)
bob = synthetic.make_specimen(20)
engine.enroll_user("bob", [synthetic.capture(bob, rng, hard=True) for _ in range(2)])
print("backend keys:", list(backend))
print("authenticated:", engine.authenticate("bob", synthetic.capture(bob, rng, hard=True)).authenticated)
