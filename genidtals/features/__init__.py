"""Feature extractors for each biometric modality."""

import numpy as np

from . import follicular, keypoints, texture, vascular

MODALITIES = ("keypoints", "vascular", "texture", "follicular")


def extract_grid_features(canon: np.ndarray, mask: np.ndarray) -> dict:
    """Position-dependent features, recomputed after registration at match time."""
    return {
        "vascular": vascular.extract(canon, mask),
        "texture": texture.extract(canon, mask),
        "follicular": follicular.extract(canon, mask),
    }


def extract_all(canon: np.ndarray, mask: np.ndarray) -> dict:
    return {"keypoints": keypoints.extract(canon, mask), **extract_grid_features(canon, mask)}


__all__ = ["MODALITIES", "extract_all", "extract_grid_features",
           "follicular", "keypoints", "texture", "vascular"]
