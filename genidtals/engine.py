"""High-level enrollment and authentication API."""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from . import __version__
from .config import get_profile
from .features import extract_all
from .liveness import LivenessResult, check as check_liveness
from .matching import match
from .preprocessing import ImageInput, load_image, normalize, segment_roi
from .storage import TemplateStore

DEFAULT_STORE = Path.home() / ".genidtals"


@dataclass
class AuthResult:
    authenticated: bool
    confidence: float
    threshold: float
    modality_scores: dict = field(default_factory=dict)
    liveness: LivenessResult | None = None
    elapsed_ms: float = 0.0
    reason: str = ""


class EnrollmentError(Exception):
    pass


class BiometricEngine:
    def __init__(self, security_level: str = "standard",
                 store_dir: str | Path | None = None,
                 passphrase: str | None = None):
        self.profile = get_profile(security_level)
        self.store = TemplateStore(store_dir or DEFAULT_STORE, passphrase)

    @staticmethod
    def extract_template(image: ImageInput) -> dict:
        canon, mask = normalize(load_image(image))
        return extract_all(canon, mask)

    def enroll_user(self, user_id: str, images: Sequence[ImageInput],
                    overwrite: bool = False) -> bool:
        if not user_id:
            raise EnrollmentError("User ID must not be empty")
        if len(images) < self.profile.min_enroll_images:
            raise EnrollmentError(
                f"Security level {self.profile.name!r} requires at least "
                f"{self.profile.min_enroll_images} reference images, got {len(images)}")
        if not overwrite and self.store.load(user_id) is not None:
            raise EnrollmentError(f"User {user_id!r} is already enrolled")

        templates = [self.extract_template(img) for img in images]

        # Reject inconsistent reference sets (e.g. images of different subjects).
        if len(templates) > 1:
            for i, t in enumerate(templates):
                score, _ = match(t, templates[:i] + templates[i + 1:])
                if score < self.profile.match_threshold:
                    raise EnrollmentError(
                        f"Reference image {i + 1} is inconsistent with the others "
                        f"(score {score:.1f} < {self.profile.match_threshold})")

        self.store.save(user_id, {
            "version": __version__,
            "enrolled_at": time.time(),
            "templates": templates,
        })
        return True

    def authenticate(self, user_id: str, image: ImageInput,
                     extra_frames: Sequence[ImageInput] = ()) -> AuthResult:
        start = time.perf_counter()
        profile = self.profile

        def done(**kw) -> AuthResult:
            return AuthResult(threshold=profile.match_threshold,
                              elapsed_ms=(time.perf_counter() - start) * 1000, **kw)

        record = self.store.load(user_id)
        if record is None:
            return done(authenticated=False, confidence=0.0, reason="user not enrolled")

        gray = load_image(image)
        frames = [gray] + [load_image(f) for f in extra_frames]
        live = check_liveness(frames, segment_roi(gray))
        if profile.enforce_liveness and not live.live:
            return done(authenticated=False, confidence=0.0, liveness=live,
                        reason="liveness check failed: " + "; ".join(live.reasons))

        canon, mask = normalize(gray)
        score, parts = match(extract_all(canon, mask), record["templates"])
        ok = score >= profile.match_threshold
        return done(authenticated=ok, confidence=round(score, 2),
                    modality_scores={k: round(v, 4) for k, v in parts.items()},
                    liveness=live, reason="" if ok else "score below threshold")

    def is_enrolled(self, user_id: str) -> bool:
        return self.store.load(user_id) is not None

    def delete_user(self, user_id: str) -> bool:
        return self.store.delete(user_id)
