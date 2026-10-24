"""Feature extractors for each biometric modality."""

import numpy as np

from . import follicular, texture, vascular


def extract_all(canon: np.ndarray, mask: np.ndarray) -> dict:
    return {
        "vascular": vascular.extract(canon, mask),
        "texture": texture.extract(canon, mask),
        "follicular": follicular.extract(canon, mask),
    }


__all__ = ["extract_all", "follicular", "texture", "vascular"]
