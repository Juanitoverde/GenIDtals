"""Tunable parameters and security level presets."""

from dataclasses import dataclass

# Side length (pixels) of the canonical, normalized region of interest.
CANONICAL_SIZE = 256

# Relative weight of each modality in the fused match score.
MODALITY_WEIGHTS = {
    "vascular": 0.45,
    "texture": 0.35,
    "follicular": 0.20,
}


@dataclass(frozen=True)
class SecurityProfile:
    name: str
    # Minimum fused score (0-100) required to accept a match.
    match_threshold: float
    # Minimum number of reference images required at enrollment.
    min_enroll_images: int
    # Whether a failed liveness check rejects the attempt outright.
    enforce_liveness: bool


SECURITY_LEVELS = {
    "low": SecurityProfile("low", 58.0, 1, False),
    "standard": SecurityProfile("standard", 62.0, 2, True),
    "high": SecurityProfile("high", 68.0, 3, True),
    "maximum": SecurityProfile("maximum", 74.0, 3, True),
}


def get_profile(level: str) -> SecurityProfile:
    try:
        return SECURITY_LEVELS[level]
    except KeyError:
        raise ValueError(
            f"Unknown security level {level!r}; choose from {', '.join(SECURITY_LEVELS)}"
        ) from None
