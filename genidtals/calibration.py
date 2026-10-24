"""Score fusion weights and decision thresholds.

A calibration maps per-modality similarities to one fused 0-100 score and
fixes, for every security level, the score threshold that achieves that
level's target false-accept rate (FAR).

Thresholds are fitted from impostor scores. Very low FAR targets (1e-5,
1e-6) cannot be observed directly in any practical dataset, so the
threshold is the larger of the empirical quantile and a Gaussian tail
extrapolation of the impostor distribution, plus a fixed safety margin.
The built-in default was fitted on the synthetic benchmark; deployments
should run ``genidtals calibrate`` on real captures from their sensor.
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from statistics import NormalDist

import numpy as np

from .config import SECURITY_LEVELS
from .errors import ConfigurationError
from .features import MODALITIES

SAFETY_MARGIN = 3.0
# Cap on any single modality's discriminability when deriving weights, so
# one near-perfect modality on the calibration set cannot silence the rest.
MAX_DPRIME = 8.0


@dataclass(frozen=True)
class Calibration:
    weights: dict
    thresholds: dict
    source: str = "custom"
    stats: dict = field(default_factory=dict, compare=False)

    def __post_init__(self):
        missing = set(MODALITIES) - set(self.weights)
        if missing:
            raise ConfigurationError(f"Calibration lacks weights for: {', '.join(sorted(missing))}")
        missing = set(SECURITY_LEVELS) - set(self.thresholds)
        if missing:
            raise ConfigurationError(f"Calibration lacks thresholds for: {', '.join(sorted(missing))}")

    def fuse(self, scores: dict) -> float:
        total = sum(self.weights.values())
        return 100.0 * sum(self.weights[k] * scores[k] for k in MODALITIES) / total

    def threshold(self, level: str) -> float:
        return float(self.thresholds[level])

    def to_dict(self) -> dict:
        return {"format": 1, "source": self.source, "weights": self.weights,
                "thresholds": self.thresholds, "stats": self.stats}

    @classmethod
    def from_dict(cls, data: dict) -> "Calibration":
        if data.get("format") != 1:
            raise ConfigurationError("Unsupported calibration format")
        return cls({k: float(v) for k, v in data["weights"].items()},
                   {k: float(v) for k, v in data["thresholds"].items()},
                   data.get("source", "custom"), data.get("stats", {}))

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path) -> "Calibration":
        try:
            return cls.from_dict(json.loads(Path(path).read_text()))
        except (OSError, ValueError, KeyError) as e:
            raise ConfigurationError(f"Could not load calibration {path}: {e}") from e


def _dprime(gen: np.ndarray, imp: np.ndarray) -> float:
    spread = math.sqrt((gen.var() + imp.var()) / 2) + 1e-6
    return float((gen.mean() - imp.mean()) / spread)


def fit_threshold(impostor_scores: np.ndarray, target_far: float) -> float:
    imp = np.asarray(impostor_scores, np.float64)
    empirical = float(np.quantile(imp, 1 - target_far)) if len(imp) * target_far >= 1 else float(imp.max())
    z = NormalDist().inv_cdf(1 - target_far)
    gaussian = float(imp.mean() + z * imp.std())
    return min(99.0, max(empirical, gaussian) + SAFETY_MARGIN)


def fit(genuine: list[dict], impostor: list[dict], source: str = "custom") -> Calibration:
    """Fit weights and thresholds from per-modality genuine/impostor scores."""
    if len(genuine) < 10 or len(impostor) < 50:
        raise ConfigurationError("Need at least 10 genuine and 50 impostor comparisons to calibrate")
    stats, weights = {}, {}
    for m in MODALITIES:
        g = np.array([s[m] for s in genuine])
        i = np.array([s[m] for s in impostor])
        d = _dprime(g, i)
        stats[m] = {"dprime": round(d, 3), "genuine_mean": round(float(g.mean()), 4),
                    "impostor_mean": round(float(i.mean()), 4)}
        weights[m] = round(min(max(d, 0.0), MAX_DPRIME), 4)
    if sum(weights.values()) <= 0:
        raise ConfigurationError("No modality separates genuine from impostor scores")

    draft = Calibration(weights, {lvl: 0.0 for lvl in SECURITY_LEVELS}, source)
    imp_fused = np.array([draft.fuse(s) for s in impostor])
    gen_fused = np.array([draft.fuse(s) for s in genuine])
    thresholds = {lvl: round(fit_threshold(imp_fused, p.target_far), 2)
                  for lvl, p in SECURITY_LEVELS.items()}
    stats["fused"] = {
        "genuine_min": round(float(gen_fused.min()), 2),
        "impostor_max": round(float(imp_fused.max()), 2),
        "genuine_count": len(genuine), "impostor_count": len(impostor),
        "frr_at_threshold": {lvl: round(float((gen_fused < t).mean()), 5) for lvl, t in thresholds.items()},
    }
    return Calibration(weights, thresholds, source, stats)


# Weights fitted with ``genidtals calibrate --synthetic`` (50 subjects, hard
# capture conditions). On that set the fitted thresholds were 25-31 with
# genuine scores >= 83 and impostor scores <= 25. Synthetic data overstates
# separability, so the shipped thresholds are deliberately stricter than the
# fit: no level can be reached without keypoint agreement. Recalibrate on
# real captures before production use.
DEFAULT_CALIBRATION = Calibration(
    weights={"keypoints": 8.0, "vascular": 8.0, "texture": 0.8, "follicular": 1.4},
    thresholds={"low": 40.0, "standard": 50.0, "high": 55.0, "maximum": 60.0},
    source="builtin-synthetic",
)


def load_calibration(path=None) -> Calibration:
    return Calibration.load(path) if path else DEFAULT_CALIBRATION
