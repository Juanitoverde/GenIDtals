# Integration Guide

GenIDtals can be integrated three ways. Pick the one that fits your stack:

| You are building in... | Use | Install |
|---|---|---|
| Python | The embedded SDK (`BiometricEngine`) | `pip install genidtals` |
| Any other language, or a separate biometrics service | The REST API (`genidtals serve` or the Docker image) | `pip install "genidtals[server]"` or `docker build` |
| Node.js, Deno, Bun, browsers (via your backend) | The TypeScript client for the REST API | `clients/typescript` |
| Python talking to a remote GenIDtals service | `genidtals.client.GenIDtalsClient` (standard library only) | included |

For anything else, generate a client from [`openapi.json`](openapi.json) with
[OpenAPI Generator](https://openapi-generator.tech) (Java, Go, C#, Rust, Swift,
Kotlin, PHP, Ruby...), or call the endpoints directly. They only need HTTP,
JSON and base64.

---

## 1. Embedded Python SDK

### Managed storage

The engine stores encrypted templates for you.

```python
from genidtals import BiometricEngine, Reason

engine = BiometricEngine(security_level="high")        # create once, share across threads

engine.enroll_user("user_12345", ["ref1.jpg", "ref2.jpg", "ref3.jpg"])

result = engine.authenticate("user_12345", "attempt.jpg")
if result.authenticated:
    ...
elif result.reason == Reason.LOW_QUALITY:
    show(result.quality.issues)                         # e.g. "image is out of focus"
elif result.reason == Reason.LOCKED_OUT:
    show(f"Try again after {result.locked_until}")
```

Images can be file paths, encoded bytes (PNG, JPEG, TIFF, BMP, WebP), binary
file objects, or NumPy arrays (grayscale, BGR or BGRA).

### Stateless: keep templates in your own database

```python
template = engine.create_template("u_123", images)      # bytes: encrypted and bound to "u_123"
db.save(user_id="u_123", biometric_template=template)

result = engine.verify_template("u_123", db.load("u_123"), capture)
```

A template only verifies for the subject it was created for. Copying Alice's
blob onto Mallory's row makes verification fail with `TemplateError`. Lockout
counters are still kept in the engine's store, keyed by subject.

### Storage backends

| Store | Use for |
|---|---|
| `FileStore(dir)` | Single host. One `0600` file per record, written atomically. Default: `~/.genidtals/templates`. |
| `SQLiteStore(path)` | Single host with several processes. WAL mode, `0600` file. |
| `MemoryStore()` | Tests, ephemeral workers. |
| Your own `TemplateStore` subclass | Redis, Postgres, DynamoDB, S3... Implement `get`, `put`, `delete` and `keys` on opaque string keys and `bytes` values. See [`examples/custom_store.py`](../examples/custom_store.py). |

Stores only ever see HMAC-derived keys and AES-GCM ciphertext.

### Keys

| Provider | When |
|---|---|
| `StaticKeyProvider.from_env()` | Production. Reads `GENIDTALS_KEYS` (comma-separated base64 keys, current key first) from your secret manager. Generate keys with `genidtals keygen`. |
| `KeyringFile(path)` | Single host. A `0600` JSON keyring, created on first use. It refuses to load if other users can read it. |
| `StaticKeyProvider.from_passphrase(passphrase, salt)` | Argon2id-derived key; you must store the salt. |
| Your own `KeyProvider` | KMS/HSM. Implement `current_key_id`, `get_key(key_id)` and `key_ids()`. |

With no `keys=` argument, the engine uses `GENIDTALS_KEYS` if it is set.
Otherwise it uses the keyring at `GENIDTALS_KEYRING`, or `~/.genidtals/keyring.json`.

**Rotation:** make a new key current while keeping the old ones (a new key
first in `GENIDTALS_KEYS`, or `KeyringFile.rotate()`), then call
`engine.rotate_keys()` (CLI: `genidtals rotate-keys`). Records stay readable
throughout. Once re-sealed, retire the old key. For stateless templates, call
`engine.reseal_template(subject_id, blob)` when you next load each one.

### Configuration

```python
from genidtals import BiometricEngine, EngineConfig, SQLiteStore, StaticKeyProvider

engine = BiometricEngine(
    config=EngineConfig(
        security_level="high",          # low | standard | high | maximum
        calibration="calibration.json", # from `genidtals calibrate` on your sensor's data
        expose_scores=False,            # keep numeric scores out of results (default)
        max_failed_attempts=5,          # lockout after 5 failures (0 disables)
        failure_window_seconds=900,
        lockout_seconds=300,            # doubles with each lockout, capped at 24 h
        max_references=8,
        audit_hook=send_to_siem,        # receives AuditEvent; no biometrics, no raw IDs
    ),
    store=SQLiteStore("/var/lib/myapp/biometrics.db"),
    keys=StaticKeyProvider.from_env(),
)
```

### Errors

Every SDK exception derives from `GenIDtalsError`:

| Exception | Meaning |
|---|---|
| `ImageError` | Input could not be decoded, is too large or small, or has no region of interest |
| `QualityError` | An enrollment image is too poor (`.report.issues` says why) |
| `LivenessError` | An enrollment image looks like a presentation attack |
| `EnrollmentError` / `AlreadyEnrolledError` | Too few images, inconsistent references, duplicate user |
| `TemplateError` | A sealed template was tampered with, belongs to another subject, or needs re-enrollment |
| `KeyManagementError` | Missing, malformed or unsafe key material |
| `StorageError` | The backend failed |
| `ConfigurationError` | Invalid settings or user ID |

Verification outcomes are **not** exceptions. They come back as
`AuthResult.reason`: `ok`, `no_match`, `not_enrolled`, `low_quality`,
`liveness_failed` or `locked_out`.

---

## 2. REST API

```bash
pip install "genidtals[server]"
export GENIDTALS_KEYS="$(genidtals keygen)"            # master key: from your secret manager
export GENIDTALS_API_KEYS="$(genidtals keygen --api-key)"
genidtals serve --host 127.0.0.1 --port 8080           # add --docs for /docs + /openapi.json
```

Or with Docker:

```bash
docker build -t genidtals .
docker run -p 8080:8080 -e GENIDTALS_KEYS -e GENIDTALS_API_KEYS -v genidtals-data:/data genidtals
```

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/v1/health` | none (no auth) | `{status, version}` |
| GET | `/v1/info` | none | version, security level, enrolled count |
| POST | `/v1/users/{id}/enroll` | `{images: [b64...], overwrite?}` | `{user_id, references, quality}` |
| POST | `/v1/users/{id}/verify` | `{image: b64, frames?: [b64...]}` | AuthResult |
| GET | `/v1/users/{id}` | none | `{enrolled, locked, locked_until}` |
| DELETE | `/v1/users/{id}` | none | `{deleted}` |
| POST | `/v1/users/{id}/unlock` | none | `{unlocked}` |
| POST | `/v1/templates` | `{subject_id, images}` | `{template: b64}` |
| POST | `/v1/templates/verify` | `{subject_id, template, image, frames?}` | AuthResult |
| POST | `/v1/templates/reseal` | `{subject_id, template}` | `{template}` |
| POST | `/v1/quality` | `{image}` | QualityReport |

Authenticate with `Authorization: Bearer <api key>` or `X-API-Key`. URL-encode
user IDs. A failed verification is still HTTP 200 with
`"authenticated": false`. Errors use `{error, message}` with these status codes:

- 401: bad or missing API key
- 409: user already enrolled
- 413: request body too large
- 422: unusable input or enrollment refused
- 400: invalid template
- 500: internal error (details are logged on the server, never returned)

The server is meant to run **behind your backend**, which owns user sessions
and decides who may verify as whom. Don't expose it to end users. Put TLS in
front of it, or pass `--ssl-certfile/--ssl-keyfile`.

### Clients

```python
from genidtals.client import GenIDtalsClient
c = GenIDtalsClient("https://biometrics.internal", api_key)
c.enroll("alice", ["a1.png", "a2.png"])
c.verify("alice", "probe.png")["authenticated"]
```

```ts
import { GenIDtalsClient } from "@genidtals/client";
const c = new GenIDtalsClient({ baseUrl: "https://biometrics.internal", apiKey });
await c.enroll("alice", [img1, img2]);          // Uint8Array | ArrayBuffer | Blob | base64
const { authenticated } = await c.verify("alice", probe);
```

Both clients refuse plain `http://` to non-local hosts unless you explicitly
allow it. See also [`examples/rest_curl.sh`](../examples/rest_curl.sh).

---

## 3. Production checklist

- [ ] Run `genidtals calibrate --dataset <real captures>` and deploy the resulting `calibration.json`. The built-in calibration was fitted on synthetic data.
- [ ] Load master keys from a secret manager (`GENIDTALS_KEYS`) or a custom `KeyProvider`. Never store them next to the template store.
- [ ] Use a shared store (SQLite or your own backend) when running more than one process, so lockouts apply everywhere.
- [ ] Wire `audit_hook` into your logging/SIEM.
- [ ] Keep `expose_scores` off in anything an end user can see.
- [ ] Put the REST server behind TLS and your backend; rotate API keys.
- [ ] Capture burst frames (`frames=`) where your sensor allows, to enable the motion liveness check.
- [ ] Plan for key rotation (`rotate-keys`) and erasure requests (`delete_user`).
