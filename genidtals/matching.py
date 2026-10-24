"""Pattern matching and score fusion."""

from .config import MODALITY_WEIGHTS
from .features import follicular, texture, vascular

_SIMILARITY = {
    "vascular": vascular.similarity,
    "texture": texture.similarity,
    "follicular": follicular.similarity,
}


def compare(probe: dict, reference: dict) -> dict:
    """Per-modality similarities in [0, 1]."""
    return {name: fn(probe[name], reference[name]) for name, fn in _SIMILARITY.items()}


def fuse(scores: dict) -> float:
    """Weighted fusion of modality similarities into a 0-100 score."""
    total = sum(MODALITY_WEIGHTS.values())
    return 100.0 * sum(MODALITY_WEIGHTS[k] * scores[k] for k in MODALITY_WEIGHTS) / total


def match(probe: dict, references: list[dict]) -> tuple[float, dict]:
    """Best fused score of a probe against all enrolled references."""
    best_score, best_parts = -1.0, {}
    for ref in references:
        parts = compare(probe, ref)
        score = fuse(parts)
        if score > best_score:
            best_score, best_parts = score, parts
    return best_score, best_parts
