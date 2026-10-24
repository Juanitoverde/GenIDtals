"""Input processing and multi-angle feature normalization.

Every image is reduced to a canonical view before feature extraction:
the region of interest is segmented, its principal axis is rotated to
vertical, the 180-degree ambiguity is resolved from the intensity skew, and
the result is cropped and resized to ``CANONICAL_SIZE``.
"""

from pathlib import Path
from typing import Union

import cv2
import numpy as np

from .config import CANONICAL_SIZE

ImageInput = Union[str, Path, np.ndarray]


class ImageError(ValueError):
    pass


def load_image(source: ImageInput) -> np.ndarray:
    """Return a single-channel uint8 image from a path or array."""
    if isinstance(source, np.ndarray):
        img = source
    else:
        img = cv2.imread(str(source), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ImageError(f"Could not read image: {source}")
    if img.ndim == 3:
        code = cv2.COLOR_BGRA2GRAY if img.shape[2] == 4 else cv2.COLOR_BGR2GRAY
        img = cv2.cvtColor(img, code)
    if img.dtype != np.uint8:
        img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    if min(img.shape) < 64:
        raise ImageError(f"Image too small ({img.shape[1]}x{img.shape[0]}); need at least 64x64")
    return img


def segment_roi(gray: np.ndarray) -> np.ndarray:
    """Binary mask of the largest foreground region."""
    blurred = cv2.GaussianBlur(gray, (0, 0), 3)
    _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count <= 1:
        raise ImageError("No region of interest found")
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    roi = (labels == largest).astype(np.uint8) * 255
    if stats[largest, cv2.CC_STAT_AREA] < 0.02 * gray.size:
        raise ImageError("Region of interest too small")
    return roi


def _orientation(mask: np.ndarray, gray: np.ndarray):
    m = cv2.moments(mask, binaryImage=True)
    cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
    # Angle of the principal axis relative to the x-axis.
    theta = 0.5 * np.degrees(np.arctan2(2 * m["mu11"], m["mu20"] - m["mu02"]))
    return cx, cy, theta


def _rotate(img: np.ndarray, center, angle: float, interp=cv2.INTER_LINEAR) -> np.ndarray:
    h, w = img.shape[:2]
    diag = int(np.ceil(np.hypot(h, w)))
    mat = cv2.getRotationMatrix2D(center, angle, 1.0)
    # Translate so the rotation center lands in the middle of a square canvas.
    mat[0, 2] += diag / 2 - center[0]
    mat[1, 2] += diag / 2 - center[1]
    return cv2.warpAffine(img, mat, (diag, diag), flags=interp, borderValue=0)


def normalize(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (canonical image, canonical mask), both CANONICAL_SIZE square."""
    mask = segment_roi(gray)
    cx, cy, theta = _orientation(mask, gray)
    # Rotate so the principal axis is vertical.
    angle = theta - 90.0
    rot_img = _rotate(gray, (cx, cy), angle)
    rot_mask = _rotate(mask, (cx, cy), angle, cv2.INTER_NEAREST)

    # Resolve the 180-degree ambiguity: put the brighter half on top.
    ys, xs = np.nonzero(rot_mask)
    mid = rot_mask.shape[0] / 2
    vals = rot_img[ys, xs].astype(np.float64)
    if vals[ys > mid].mean() > vals[ys <= mid].mean():
        rot_img = cv2.rotate(rot_img, cv2.ROTATE_180)
        rot_mask = cv2.rotate(rot_mask, cv2.ROTATE_180)

    x, y, w, h = cv2.boundingRect(rot_mask)
    side = max(w, h)
    pad_x, pad_y = (side - w) // 2, (side - h) // 2
    crop = np.zeros((side, side), np.uint8)
    crop_mask = np.zeros((side, side), np.uint8)
    crop[pad_y:pad_y + h, pad_x:pad_x + w] = rot_img[y:y + h, x:x + w]
    crop_mask[pad_y:pad_y + h, pad_x:pad_x + w] = rot_mask[y:y + h, x:x + w]

    size = (CANONICAL_SIZE, CANONICAL_SIZE)
    canon = cv2.resize(crop, size, interpolation=cv2.INTER_AREA)
    canon_mask = cv2.resize(crop_mask, size, interpolation=cv2.INTER_NEAREST)

    # Photometric normalization: local contrast equalization inside the ROI.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    canon = clahe.apply(canon)
    canon[canon_mask == 0] = 0
    return canon, canon_mask
