"""Capture quality assessment.

Poor captures are the largest single source of recognition errors, so both
enrollment and verification gate on a 0-100 quality score. The score is
``100 * sqrt(min(parts) * mean(parts))`` over five sub-scores in [0, 1], so
one very poor factor pulls the total down even when the rest are fine.
"""

import math
from dataclasses import dataclass, field

import cv2
import numpy as np


def _ramp(x: float, lo: float, hi: float) -> float:
    return float(np.clip((x - lo) / (hi - lo), 0.0, 1.0))


@dataclass
class QualityReport:
    score: float
    metrics: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"score": self.score, "metrics": self.metrics, "issues": self.issues}


def assess(gray: np.ndarray, mask: np.ndarray, keypoint_count: int) -> QualityReport:
    inner = cv2.erode(mask, np.ones((9, 9), np.uint8)) > 0
    if not inner.any():
        return QualityReport(0.0, {}, ["no usable region of interest"])
    pixels = gray[inner]
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F)[inner].var())
    clipped = float(((pixels <= 3) | (pixels >= 252)).mean())
    contrast = float(pixels.std())
    coverage = float((mask > 0).mean())

    parts = {
        "sharpness": _ramp(math.log10(lap_var + 1e-9), math.log10(15), math.log10(120)),
        "exposure": 1.0 - _ramp(clipped, 0.05, 0.30),
        "contrast": _ramp(contrast, 4.0, 15.0),
        "coverage": min(_ramp(coverage, 0.0, 0.08), 1.0 - _ramp(coverage, 0.90, 0.98)),
        "detail": _ramp(keypoint_count, 25, 120),
    }
    messages = {
        "sharpness": "image is out of focus or motion-blurred",
        "exposure": "image is over- or under-exposed",
        "contrast": "image has too little contrast",
        "coverage": "region of interest is too small or not fully in frame",
        "detail": "too few distinctive features are visible",
    }
    values = list(parts.values())
    score = 100.0 * math.sqrt(min(values) * (sum(values) / len(values)))
    metrics = {
        "laplacian_variance": round(lap_var, 2),
        "clipped_fraction": round(clipped, 4),
        "contrast": round(contrast, 2),
        "coverage": round(coverage, 4),
        "keypoints": int(keypoint_count),
        **{f"{k}_score": round(v, 3) for k, v in parts.items()},
    }
    return QualityReport(round(score, 1), metrics, [messages[k] for k, v in parts.items() if v < 0.5])
