# Security Model

## Assets and threats

| Asset | Threat | Control |
|---|---|---|
| Biometric templates at rest | Theft of the store or a backup | AES-256-GCM per record, with a fresh HKDF-derived key per record. Master keys live outside the store. |
| Template integrity | Swapping, replaying or editing records or stateless blobs | The GCM tag covers the header plus a context binding the record to its user (managed store) or subject ID (stateless). Any change fails to open. |
| User identities in storage | Learning who is enrolled from the store | Record keys are HMAC-SHA256 of the user ID under a key-derived index key. Files and rows contain no IDs. |
| Verification decision | Brute force with many forged or other people's samples | Per-user lockout (5 failures in 15 min → 5 min, doubling to 24 h). State is sealed and shared through the store. |
| Verification decision | Hill-climbing a forged sample using returned scores | Scores, liveness values and raw quality metrics are withheld unless `expose_scores` is on. Only the decision and human-readable issues are returned. |
| Verification decision | Presentation attacks (prints, screens, replays) | Liveness checks: sharpness, moiré spectral peaks, optical-flow micro-motion on bursts. Spoofs count as failed attempts. |
| Enrollment | Template poisoning (mixed or mislabeled captures) | Every reference must match the others at the operating threshold. Quality and liveness gates apply at enrollment too. |
| Service | Malicious inputs: decompression bombs, oversized bodies, path tricks | Byte and pixel limits enforced before and by the decoder. 64 MB body cap including chunked uploads. Store keys validated against `^[a-z]/[0-9a-f]{64}$`. JSON-only template format (no pickle). |
| REST API | Unauthorized callers | Mandatory API keys (≥ 24 chars), compared in constant time across all keys. The server refuses to start without keys unless `--insecure-no-auth` is given. Docs and OpenAPI are off by default. Responses are `Cache-Control: no-store`. |
| Keys | Key compromise, cryptoperiod limits | Keys are identified by fingerprint. Rotation re-seals records and lockout state without downtime. Keyring files must be `0600` or they are refused. Keys can come from a KMS via `KeyProvider`. |
| Audit trail | Logs leaking personal data | `AuditEvent`s carry a keyed pseudonym, never the user ID or any biometric data. A failing audit sink cannot break authentication. |

## Cryptography

- **Sealing:** `GIDT | v2 | key-id | salt(16) | nonce(12) | AES-256-GCM(ciphertext ‖ tag)`.
  The per-record key is `HKDF-SHA256(master, salt, "genidtals/seal/v2")`.
  Associated data is the header plus `"\0"` plus the context.
- **Indexing:** `HMAC-SHA256(HKDF(master, info="genidtals/index/v2"), "user:" ‖ id)`.
- **Passphrases:** Argon2id (t=3, m=64 MiB, p=4).
- Library: [`cryptography`](https://cryptography.io) (OpenSSL). No custom primitives.

## What is *not* protected

Be clear about these limits when deploying:

- **A host running the engine sees plaintext templates in memory.** Python
  cannot reliably wipe memory. Protect the process like any credential store.
- **Templates are not cancelable biometrics.** Anyone holding both the
  ciphertext and the master key can recover the feature data. That includes
  SIFT descriptors, from which partial images can in principle be
  reconstructed. Revoking a key revokes access, but it does not change the
  underlying biometric.
- **Lockout is per user, so it can be abused for denial of service.** An
  attacker who knows a user ID can lock that user out. Rate-limit per client
  in your application or gateway, and use `unlock_user` for support flows.
  Concurrent processes can race on the counter by an attempt or two.
- **Response differences reveal enrollment status.** `not_enrolled` vs
  `no_match` tells the API caller whether a user is enrolled. The REST API
  assumes a trusted backend caller. Do not forward reasons verbatim to
  anonymous end users.
- **Liveness is software-only.** It raises the cost of simple replays. It is
  not certified presentation-attack detection (ISO/IEC 30107-3), and it does
  not stop a determined attacker with a high-quality artifact. For high-assurance
  use, combine it with sensor-level PAD and a second factor.
- **Default thresholds were calibrated on synthetic data.** Real-world FAR/FRR
  are unknown until you run `genidtals calibrate` on captures from your sensor
  and population. See [accuracy.md](accuracy.md).
- **Not independently audited or certified.** No third-party penetration test,
  FIDO certification or NIST evaluation has been performed.

## Reporting vulnerabilities

Email dev@rbl.llc with details and a reproduction. Please do not open public
issues for security problems.
