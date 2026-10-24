"""Registration-aware matching and score fusion.

A probe is first matched to each reference by keypoints. When the geometric
verification succeeds, the fitted transform warps the probe into the
reference's coordinate frame and the position-dependent modalities
(vascular, texture, follicular) are re-extracted from the registered image,
so they compare like with like regardless of pose, flips or residual
normalization error. Without a trusted transform the canonical features are
compared directly.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from .config import CANONICAL_SIZE
from .features import extract_all, extract_grid_features, follicular, keypoints, texture, vascular
from .preprocessing import normalize, segment_roi


@dataclass
class Probe:
    """A processed capture: canonical view plus extracted features."""

    canon: np.ndarray
    mask: np.ndarray
    features: dict
    # The decoded capture and its ROI mask, in original image coordinates.
    gray: np.ndarray
    roi: np.ndarray

    @classmethod
    def from_gray(cls, gray: np.ndarray) -> "Probe":
        roi = segment_roi(gray)
        canon, mask = normalize(gray, roi)
        return cls(canon, mask, extract_all(canon, mask), gray, roi)


@dataclass
class MatchResult:
    score: float
    modalities: dict
    registered: bool
    reference_index: int


def _grid_scores(grid: dict, reference: dict) -> dict:
    return {
        "vascular": vascular.similarity(grid["vascular"], reference["vascular"]),
        "texture": texture.similarity(grid["texture"], reference["texture"]),
        "follicular": follicular.similarity(grid["follicular"], reference["follicular"]),
    }


def _register(probe: Probe, transform: np.ndarray) -> dict:
    size = (CANONICAL_SIZE, CANONICAL_SIZE)
    canon = cv2.warpAffine(probe.canon, transform, size, flags=cv2.INTER_LINEAR, borderValue=0)
    mask = cv2.warpAffine(probe.mask, transform, size, flags=cv2.INTER_NEAREST, borderValue=0)
    return extract_grid_features(canon, mask)


def compare(probe: Probe, reference: dict) -> tuple[dict, bool]:
    """Per-modality similarities in [0, 1] and whether registration succeeded."""
    km = keypoints.match(probe.features["keypoints"], reference["keypoints"])
    grid = _register(probe, km.transform) if km.registered else probe.features
    return {"keypoints": km.similarity, **_grid_scores(grid, reference)}, km.registered


def match(probe: Probe, references: list[dict], calibration) -> MatchResult:
    """Best fused score of a probe against a user's references."""
    kms = [keypoints.match(probe.features["keypoints"], r["keypoints"]) for r in references]
    best = None
    # Registration is the expensive step, so only the references with the
    # strongest geometric agreement are registered and fully scored.
    order = sorted(range(len(references)), key=lambda i: -kms[i].inliers)
    for rank, i in enumerate(order):
        km = kms[i]
        if rank >= 2 and km.registered:
            continue
        grid = _register(probe, km.transform) if km.registered else probe.features
        parts = {"keypoints": km.similarity, **_grid_scores(grid, references[i])}
        score = calibration.fuse(parts)
        if best is None or score > best.score:
            best = MatchResult(score, parts, km.registered, i)
    return best
