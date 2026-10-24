"""High-level SDK entry point.

Two integration styles are supported:

* **Managed storage** - :meth:`BiometricEngine.enroll_user` /
  :meth:`~BiometricEngine.authenticate` keep sealed templates in a
  :class:`~genidtals.stores.TemplateStore` (files, SQLite, memory, or your
  own backend).
* **Stateless** - :meth:`~BiometricEngine.create_template` returns an
  encrypted, subject-bound blob that your application stores wherever it
  likes (e.g. a column on its users table), and
  :meth:`~BiometricEngine.verify_template` checks a capture against it.
"""

import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import liveness as liveness_mod
from ._version import ALGORITHM_VERSION, __version__
from .calibration import load_calibration
from .config import EngineConfig
from .crypto import KeyProvider, KeyringFile, Sealer, StaticKeyProvider, generate_key
from .errors import (
    AlreadyEnrolledError,
    ConfigurationError,
    EnrollmentError,
    LivenessError,
    QualityError,
    TemplateError,
)
from .matching import Probe, match
from .preprocessing import ImageInput, load_image
from .quality import QualityReport, assess
from .security import AttemptLimiter, Auditor, LockoutStatus
from .stores import FileStore, MemoryStore, TemplateStore
from .templates import deserialize, serialize

MAX_USER_ID_LENGTH = 256


def default_home() -> Path:
    return Path(os.environ.get("GENIDTALS_HOME", "~/.genidtals")).expanduser()


def default_keys() -> KeyProvider:
    """``$GENIDTALS_KEYS`` if set, else the keyring at ``$GENIDTALS_KEYRING`` or ``<home>/keyring.json``."""
    if os.environ.get("GENIDTALS_KEYS", "").strip():
        return StaticKeyProvider.from_env()
    return KeyringFile(os.environ.get("GENIDTALS_KEYRING") or default_home() / "keyring.json")


class Reason:
    """Machine-readable outcome codes used in :class:`AuthResult`."""

    OK = "ok"
    NOT_ENROLLED = "not_enrolled"
    LOCKED_OUT = "locked_out"
    LOW_QUALITY = "low_quality"
    LIVENESS_FAILED = "liveness_failed"
    NO_MATCH = "no_match"


@dataclass
class AuthResult:
    authenticated: bool
    reason: str
    message: str
    # The next four are populated only when EngineConfig.expose_scores is on.
    confidence: Optional[float] = None
    threshold: Optional[float] = None
    modality_scores: Optional[dict] = None
    liveness: Optional[dict] = None
    quality: Optional[QualityReport] = None
    locked_until: Optional[float] = None
    remaining_attempts: Optional[int] = None
    elapsed_ms: float = 0.0

    def __bool__(self) -> bool:
        return self.authenticated

    def to_dict(self) -> dict:
        d = asdict(self)
        d["quality"] = self.quality.to_dict() if self.quality else None
        return d


@dataclass
class EnrollmentResult:
    user_id: str
    references: int
    quality: list = field(default_factory=list)


def _check_id(user_id: str) -> str:
    if not isinstance(user_id, str) or not user_id:
        raise ConfigurationError("User ID must be a non-empty string")
    if len(user_id) > MAX_USER_ID_LENGTH:
        raise ConfigurationError(f"User ID exceeds {MAX_USER_ID_LENGTH} characters")
    if any(ord(c) < 32 or ord(c) == 127 for c in user_id):
        raise ConfigurationError("User ID must not contain control characters")
    return user_id


class BiometricEngine:
    """Enrollment and verification. Thread-safe; create one and share it.

    Args:
        security_level: ``low``, ``standard``, ``high`` or ``maximum``
            (shorthand for ``config=EngineConfig(security_level=...)``).
        store: Where sealed templates and lockout state live. Defaults to
            a :class:`FileStore` at ``store_dir`` or ``<home>/templates``.
        keys: Master key source. Defaults to :func:`default_keys`.
        config: Full :class:`EngineConfig`.
        store_dir: Shorthand for ``store=FileStore(store_dir)``.
    """

    def __init__(self, security_level: Optional[str] = None, *,
                 store: Optional[TemplateStore] = None,
                 keys: Optional[KeyProvider] = None,
                 config: Optional[EngineConfig] = None,
                 store_dir=None):
        if config is None:
            config = EngineConfig(security_level=security_level or "standard")
        elif security_level is not None and security_level != config.security_level:
            raise ConfigurationError("security_level conflicts with config.security_level")
        if store is not None and store_dir is not None:
            raise ConfigurationError("Pass either store or store_dir, not both")

        self.config = config
        self.profile = config.profile
        self.calibration = load_calibration(config.calibration)
        self.threshold = self.calibration.threshold(self.profile.name)
        self.store = store if store is not None else FileStore(store_dir or default_home() / "templates")
        self.keys = keys if keys is not None else default_keys()
        self.sealer = Sealer(self.keys)
        self.auditor = Auditor(self.sealer, config.audit_hook)
        self.limiter = AttemptLimiter(self.store, self.sealer, config.max_failed_attempts,
                                      config.failure_window_seconds, config.lockout_seconds)
        self._write_lock = threading.Lock()

    @classmethod
    def ephemeral(cls, security_level: str = "standard", **kwargs) -> "BiometricEngine":
        """In-memory store and a random key: nothing touches disk. For tests and demos."""
        return cls(security_level, store=MemoryStore(), keys=StaticKeyProvider([generate_key()]), **kwargs)

    # ------------------------------------------------------------------ analysis

    def analyze(self, image: ImageInput) -> tuple[Probe, QualityReport]:
        """Decode, normalize and extract features from one capture."""
        probe = Probe.from_gray(load_image(image))
        return probe, assess(probe.gray, probe.roi, len(probe.features["keypoints"]["pts"]))

    def assess_quality(self, image: ImageInput) -> QualityReport:
        return self.analyze(image)[1]

    def check_liveness(self, frames: Sequence[ImageInput]) -> liveness_mod.LivenessResult:
        """Presentation-attack checks on one capture or a burst of frames."""
        if not frames:
            raise ConfigurationError("At least one frame is required")
        grays = [load_image(f) for f in frames]
        return liveness_mod.check(grays, Probe.from_gray(grays[0]).roi)

    # --------------------------------------------------------------- templates

    def _build_record(self, user_id: str, images: Sequence[ImageInput]) -> tuple[dict, list]:
        profile = self.profile
        if isinstance(images, (str, bytes, Path)):
            raise ConfigurationError("images must be a sequence of images")
        images = list(images)
        if len(images) < profile.min_enroll_images:
            raise EnrollmentError(
                f"Security level {profile.name!r} requires at least {profile.min_enroll_images} "
                f"reference images, got {len(images)}")
        if len(images) > self.config.max_references:
            raise EnrollmentError(f"At most {self.config.max_references} reference images are allowed")

        probes, qualities = [], []
        for n, img in enumerate(images, 1):
            probe, quality = self.analyze(img)
            if quality.score < profile.min_quality:
                raise QualityError(
                    f"Reference image {n} quality {quality.score:.0f} is below {profile.min_quality:.0f}: "
                    + ("; ".join(quality.issues) or "insufficient quality"), quality)
            live = liveness_mod.check([probe.gray], probe.roi)
            if profile.enforce_liveness and not live.live:
                raise LivenessError(f"Reference image {n} failed liveness: " + "; ".join(live.reasons), live)
            probes.append(probe)
            qualities.append(quality.score)

        # Every reference must match the others: rejects mixed subjects and
        # mislabelled captures, which would otherwise poison the template.
        if len(probes) > 1:
            for i, probe in enumerate(probes):
                others = [p.features for j, p in enumerate(probes) if j != i]
                if match(probe, others, self.calibration).score < self.threshold:
                    raise EnrollmentError(f"Reference image {i + 1} does not match the other references")

        record = {
            "algorithm": ALGORITHM_VERSION,
            "sdk": __version__,
            "subject": user_id,
            "created_at": time.time(),
            "references": [p.features for p in probes],
        }
        return record, qualities

    def _load_record(self, plaintext: bytes, user_id: str) -> dict:
        record = deserialize(plaintext)
        if record.get("algorithm") != ALGORITHM_VERSION:
            raise TemplateError(
                f"Template was created with algorithm v{record.get('algorithm')}; "
                f"this SDK uses v{ALGORITHM_VERSION}. Re-enroll the user.")
        if record.get("subject") != user_id:
            raise TemplateError("Template subject mismatch")
        return record

    @staticmethod
    def _template_context(subject_id: str) -> bytes:
        return b"template\0" + subject_id.encode()

    def create_template(self, subject_id: str, images: Sequence[ImageInput]) -> bytes:
        """Enroll without managed storage; returns a sealed template for the caller to store.

        The blob is encrypted and bound to ``subject_id``: it only verifies
        for that subject and cannot be read without the engine's keys.
        """
        _check_id(subject_id)
        try:
            record, _ = self._build_record(subject_id, images)
        except Exception as e:
            self.auditor.emit("enroll", "failure", subject_id, type(e).__name__)
            raise
        blob = self.sealer.seal(serialize(record), self._template_context(subject_id))
        self.auditor.emit("enroll", "success", subject_id, "stateless")
        return blob

    def open_template(self, subject_id: str, template: bytes) -> dict:
        """Decrypt and validate a sealed template (raises TemplateError)."""
        plaintext, _ = self.sealer.open(template, self._template_context(_check_id(subject_id)))
        return self._load_record(plaintext, subject_id)

    def verify_template(self, subject_id: str, template: bytes, image: ImageInput,
                        extra_frames: Sequence[ImageInput] = ()) -> AuthResult:
        """Verify a capture against a sealed template from :meth:`create_template`.

        Lockout state is still tracked in the engine's store, keyed by subject.
        """
        _check_id(subject_id)
        return self._verify(subject_id, lambda: self.open_template(subject_id, template), image, extra_frames)

    def reseal_template(self, subject_id: str, template: bytes) -> bytes:
        """Re-encrypt a template under the current key (after key rotation)."""
        context = self._template_context(_check_id(subject_id))
        plaintext, _ = self.sealer.open(template, context)
        return self.sealer.seal(plaintext, context)

    # ------------------------------------------------------- managed storage

    def _index(self, user_id: str, key_id: Optional[str] = None) -> str:
        return "t/" + self.sealer.index("user:" + user_id, key_id)

    @staticmethod
    def _record_context(store_key: str) -> bytes:
        return b"record\0" + store_key.encode()

    def _find(self, user_id: str) -> Optional[tuple[str, bytes]]:
        # Records written before a key rotation live under the old key's index.
        for kid in self.keys.key_ids():
            key = self._index(user_id, kid)
            blob = self.store.get(key)
            if blob is not None:
                return key, blob
        return None

    def _read(self, user_id: str) -> Optional[dict]:
        found = self._find(user_id)
        if found is None:
            return None
        key, blob = found
        plaintext, _ = self.sealer.open(blob, self._record_context(key))
        return self._load_record(plaintext, user_id)

    def enroll_user(self, user_id: str, images: Sequence[ImageInput],
                    overwrite: bool = False) -> EnrollmentResult:
        """Enroll ``user_id`` from reference captures into the managed store."""
        _check_id(user_id)
        if not overwrite and self._find(user_id) is not None:
            self.auditor.emit("enroll", "failure", user_id, "already_enrolled")
            raise AlreadyEnrolledError(f"User {user_id!r} is already enrolled")
        try:
            record, qualities = self._build_record(user_id, images)
        except Exception as e:
            self.auditor.emit("enroll", "failure", user_id, type(e).__name__)
            raise
        with self._write_lock:
            found = self._find(user_id)
            if found is not None and not overwrite:
                self.auditor.emit("enroll", "failure", user_id, "already_enrolled")
                raise AlreadyEnrolledError(f"User {user_id!r} is already enrolled")
            key = self._index(user_id)
            self.store.put(key, self.sealer.seal(serialize(record), self._record_context(key)))
            if found is not None and found[0] != key:
                self.store.delete(found[0])
        self.limiter.reset(user_id)
        self.auditor.emit("enroll", "success", user_id)
        return EnrollmentResult(user_id, len(record["references"]), qualities)

    def authenticate(self, user_id: str, image: ImageInput,
                     extra_frames: Sequence[ImageInput] = ()) -> AuthResult:
        """Verify a capture (optionally with burst frames for motion liveness)."""
        _check_id(user_id)
        return self._verify(user_id, lambda: self._read(user_id), image, extra_frames)

    def is_enrolled(self, user_id: str) -> bool:
        return self._find(_check_id(user_id)) is not None

    def delete_user(self, user_id: str) -> bool:
        """Remove a user's templates and lockout state (right to erasure)."""
        _check_id(user_id)
        with self._write_lock:
            deleted = [self.store.delete(self._index(user_id, kid)) for kid in self.keys.key_ids()]
        self.limiter.reset(user_id)
        self.auditor.emit("delete", "success" if any(deleted) else "failure", user_id)
        return any(deleted)

    def lockout_status(self, user_id: str) -> LockoutStatus:
        return self.limiter.status(_check_id(user_id))

    def unlock_user(self, user_id: str) -> None:
        """Administrative reset of a user's failed-attempt counter and lockout."""
        self.limiter.reset(_check_id(user_id))
        self.auditor.emit("unlock", "success", user_id)

    def enrolled_count(self) -> int:
        return self.store.count("t/")

    def rotate_keys(self) -> int:
        """Re-seal every stored record under the current key. Returns the number migrated.

        Run after making a new key current (e.g. ``KeyringFile.rotate()``);
        once it returns, older keys can be retired.
        """
        current = self.keys.current_key_id
        migrated = 0
        with self._write_lock:
            for key in list(self.store.keys("t/")):
                blob = self.store.get(key)
                if blob is None:
                    continue
                old_kid = Sealer.key_id_of(blob)
                if old_kid == current:
                    continue
                plaintext, _ = self.sealer.open(blob, self._record_context(key))
                user_id = deserialize(plaintext)["subject"]
                new_key = self._index(user_id)
                self.store.put(new_key, self.sealer.seal(plaintext, self._record_context(new_key)))
                if new_key != key:
                    self.store.delete(key)
                self._migrate_attempts(user_id, old_kid)
                migrated += 1
        self.auditor.emit("rotate", "success", "*", f"{migrated} records")
        return migrated

    def _migrate_attempts(self, user_id: str, old_kid: str) -> None:
        old_key = "a/" + self.sealer.index("attempts:" + user_id, old_kid)
        blob = self.store.get(old_key)
        if blob is None:
            return
        context = b"attempts\0" + user_id.encode()
        plaintext, _ = self.sealer.open(blob, context)
        self.store.put("a/" + self.sealer.index("attempts:" + user_id), self.sealer.seal(plaintext, context))
        self.store.delete(old_key)

    # ------------------------------------------------------------ verification

    def _verify(self, user_id: str, load: Callable[[], Optional[dict]], image: ImageInput,
                extra_frames: Sequence[ImageInput]) -> AuthResult:
        start = time.perf_counter()
        expose = self.config.expose_scores

        def done(ok: bool, reason: str, message: str, **kw) -> AuthResult:
            self.auditor.emit("verify", "success" if ok else "failure", user_id, reason)
            return AuthResult(ok, reason, message, elapsed_ms=(time.perf_counter() - start) * 1000, **kw)

        status = self.limiter.status(user_id)
        if status.locked:
            return done(False, Reason.LOCKED_OUT, "Too many failed attempts; try again later",
                        locked_until=status.locked_until)

        record = load()
        if record is None:
            return done(False, Reason.NOT_ENROLLED, "User is not enrolled")

        probe, full_quality = self.analyze(image)
        # Raw metrics (e.g. sharpness) double as liveness signals; without
        # expose_scores only the score and human-readable issues are returned.
        quality = full_quality if expose else QualityReport(full_quality.score, {}, full_quality.issues)
        if quality.score < self.profile.min_quality:
            return done(False, Reason.LOW_QUALITY,
                        "Capture quality too low: " + ("; ".join(quality.issues) or "retake the image"),
                        quality=quality)

        frames = [probe.gray] + [load_image(f) for f in extra_frames]
        live = liveness_mod.check(frames, probe.roi)
        live_info = {"live": live.live, "checks": live.checks, "reasons": live.reasons} if expose else None

        def failed(reason: str, message: str, **kw) -> AuthResult:
            st = self.limiter.record_failure(user_id)
            remaining = None
            if self.limiter.enabled and not st.locked:
                remaining = self.limiter.max_failures - st.failures
            if st.locked:
                self.auditor.emit("lockout", "success", user_id)
            return done(False, reason, message, quality=quality, liveness=live_info,
                        locked_until=st.locked_until, remaining_attempts=remaining, **kw)

        if self.profile.enforce_liveness and not live.live:
            return failed(Reason.LIVENESS_FAILED, "Liveness check failed")

        result = match(probe, record["references"], self.calibration)
        scores = {}
        if expose:
            scores = {"confidence": round(result.score, 2), "threshold": self.threshold,
                      "modality_scores": {k: round(v, 4) for k, v in result.modalities.items()}}
        if result.score >= self.threshold:
            self.limiter.record_success(user_id)
            return done(True, Reason.OK, "Authenticated", quality=quality, liveness=live_info, **scores)
        return failed(Reason.NO_MATCH, "Biometric does not match", **scores)
