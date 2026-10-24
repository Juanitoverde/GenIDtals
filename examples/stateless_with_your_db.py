"""Stateless integration: your application stores the sealed template itself.

The template is an encrypted blob bound to the user's ID, so it can live in
an ordinary column of your users table. Run:  python examples/stateless_with_your_db.py
"""

import sqlite3

import numpy as np

from genidtals import BiometricEngine, MemoryStore, StaticKeyProvider, generate_key, synthetic

# Your application's database.
db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT, biometric_template BLOB)")
db.execute("INSERT INTO users VALUES ('u_123', 'alice@example.com', NULL)")

# In production load the key from your secret manager, e.g. StaticKeyProvider.from_env().
# The MemoryStore here only holds lockout counters; use a shared store
# (SQLiteStore, or your own TemplateStore) when running several workers.
engine = BiometricEngine("standard", store=MemoryStore(), keys=StaticKeyProvider([generate_key()]))

rng = np.random.default_rng(1)
alice = synthetic.make_specimen(10)

# Enrollment
template = engine.create_template("u_123", [synthetic.capture(alice, rng, hard=True) for _ in range(2)])
db.execute("UPDATE users SET biometric_template = ? WHERE id = ?", (template, "u_123"))
print(f"Stored a {len(template)}-byte sealed template")

# Verification
(stored,) = db.execute("SELECT biometric_template FROM users WHERE id = ?", ("u_123",)).fetchone()
result = engine.verify_template("u_123", stored, synthetic.capture(alice, rng, hard=True))
print("authenticated:", result.authenticated, result.reason)
