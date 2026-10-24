"""Engine configuration and security level presets."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .errors import ConfigurationError

# Side length (pixels) of the canonical, normalized region of interest.
CANONICAL_SIZE = 256


@dataclass(frozen=True)
class SecurityProfile:
    name: str
    # Target false-accept rate; the matching threshold comes from calibration.
    target_far: float
    # Minimum number of reference images required at enrollment.
    min_enroll_images: int
    # Whether a failed liveness check rejects the attempt outright.
    enforce_liveness: bool
    # Minimum capture quality (0-100) accepted for enrollment and verification.
    min_quality: float


SECURITY_LEVELS = {
    "low": SecurityProfile("low", 1e-2, 1, False, 30.0),
    "standard": SecurityProfile("standard", 1e-4, 2, True, 40.0),
    "high": SecurityProfile("high", 1e-5, 3, True, 50.0),
    "maximum": SecurityProfile("maximum", 1e-6, 4, True, 60.0),
}


def get_profile(level: str) -> SecurityProfile:
    try:
        return SECURITY_LEVELS[level]
    except KeyError:
        raise ConfigurationError(
            f"Unknown security level {level!r}; choose from {', '.join(SECURITY_LEVELS)}"
        ) from None


@dataclass
class EngineConfig:
    """Behavioral settings for :class:`~genidtals.BiometricEngine`.

    The defaults are the secure choice; loosen them deliberately.
    """

    security_level: str = "standard"
    # Path to a calibration JSON produced by ``genidtals calibrate``. When
    # unset, the built-in calibration (fitted on synthetic data) is used.
    calibration: Optional[Path] = None
    # Include numeric scores in results. Off by default: fine-grained scores
    # let an attacker hill-climb a forged sample toward acceptance.
    expose_scores: bool = False
    # Consecutive failed attempts before a user is locked out (0 disables).
    max_failed_attempts: int = 5
    # Failures older than this many seconds are forgotten.
    failure_window_seconds: float = 900.0
    # First lockout duration; doubles with each further lockout (capped at a day).
    lockout_seconds: float = 300.0
    # Maximum reference images stored per user.
    max_references: int = 8
    # Called with an AuditEvent for every enrollment, verification and
    # lockout. Events never contain biometric data or raw user IDs.
    audit_hook: Optional[Callable] = field(default=None, repr=False)

    def __post_init__(self):
        self.profile = get_profile(self.security_level)
        if self.max_failed_attempts < 0:
            raise ConfigurationError("max_failed_attempts must be >= 0")
        if self.max_references < self.profile.min_enroll_images:
            raise ConfigurationError("max_references is below the level's minimum enrollment count")
        if self.calibration is not None:
            self.calibration = Path(self.calibration)
