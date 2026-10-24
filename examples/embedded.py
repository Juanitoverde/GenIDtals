"""Embedded SDK with managed storage.

Run from the repository root:  python examples/embedded.py
"""

import tempfile
from pathlib import Path

import numpy as np

from genidtals import (
    BiometricEngine,
    EngineConfig,
    GenIDtalsError,
    KeyringFile,
    QualityError,
    SQLiteStore,
    synthetic,
)

workdir = Path(tempfile.mkdtemp())

# In production: keep the keyring (or GENIDTALS_KEYS) away from the template store.
engine = BiometricEngine(
    config=EngineConfig(security_level="high", audit_hook=lambda e: print("audit:", e.event, e.outcome)),
    store=SQLiteStore(workdir / "templates.db"),
    keys=KeyringFile(workdir / "secrets" / "keyring.json"),
)

# Demo captures; replace with images from your sensor (paths, bytes or arrays).
rng = np.random.default_rng(0)
alice, mallory = synthetic.make_specimen(1), synthetic.make_specimen(2)
references = [synthetic.capture(alice, rng, hard=True) for _ in range(3)]

try:
    engine.enroll_user("alice@example.com", references)
except QualityError as e:
    print("Retake needed:", e.report.issues)
    raise

for name, specimen in (("alice", alice), ("mallory", mallory)):
    result = engine.authenticate("alice@example.com", synthetic.capture(specimen, rng, hard=True))
    print(f"{name}: authenticated={result.authenticated} reason={result.reason} ({result.elapsed_ms:.0f} ms)")

try:
    engine.authenticate("alice@example.com", b"not an image")
except GenIDtalsError as e:
    print("Rejected input:", e)
