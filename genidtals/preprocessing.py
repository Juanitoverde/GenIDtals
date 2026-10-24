"""Input processing and multi-angle feature normalization.

Every image is reduced to a canonical view before feature extraction:
the region of interest is segmented, its principal axis is rotated to
vertical, the 180-degree ambiguity is resolved from the intensity skew, and
the result is cropped and resized to ``CANONICAL_SIZE``.
"""

from pathlib import Path
from typing import BinaryIO, Union

import cv2
import numpy as np

from .config import CANONICAL_SIZE
from .errors import ImageError

ImageInput = Union[str, Path, bytes, bytearray, memoryview, BinaryIO, np.ndarray]

MAX_INPUT_BYTES = 25 * 1024 * 1024
MAX_INPUT_PIXELS = 40_000_000
MIN_SIDE = 64
# Larger inputs are downscaled before processing; detail beyond this is
# discarded by normalization anyway.
WORKING_SIDE = 1024


def _decode(data: bytes) -> np.ndarray:
    if len(data) > MAX_INPUT_BYTES:
        raise ImageError(f"Image is {len(data)} bytes; the limit is {MAX_INPUT_BYTES}")
    if not data:
        raise ImageError("Image data is empty")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ImageError("Could not decode image (unsupported format or corrupt data)")
    return img


def load_image(source: ImageInput) -> np.ndarray:
    """Return a single-channel uint8 image.

    Accepts a file path, encoded image bytes (PNG, JPEG, TIFF, BMP, WebP...),
    a binary file object, or a NumPy array (grayscale, BGR or BGRA).
    """
    if isinstance(source, np.ndarray):
        img = source
    elif isinstance(source, (bytes, bytearray, memoryview)):
        img = _decode(bytes(source))
    elif isinstance(source, (str, Path)):
        path = Path(source)
        try:
            if path.stat().st_size > MAX_INPUT_BYTES:
                raise ImageError(f"Image file exceeds {MAX_INPUT_BYTES} bytes: {path}")
            data = path.read_bytes()
        except OSError as e:
            raise ImageError(f"Could not read image: {path} ({e.strerror})") from e
        img = _decode(data)
    elif hasattr(source, "read"):
        img = _decode(source.read(MAX_INPUT_BYTES + 1))
    else:
        raise ImageError(f"Unsupported image input type: {type(source).__name__}")

    if img.ndim == 3:
        if img.shape[2] == 1:
            img = img[:, :, 0]
        else:
            code = cv2.COLOR_BGRA2GRAY if img.shape[2] == 4 else cv2.COLOR_BGR2GRAY
            img = cv2.cvtColor(img, code)
    elif img.ndim != 2:
        raise ImageError(f"Unsupported image shape {img.shape}")
    if img.dtype != np.uint8:
        img = cv2.normalize(img.astype(np.float32), None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    h, w = img.shape
    if min(h, w) < MIN_SIDE:
        raise ImageError(f"Image too small ({w}x{h}); need at least {MIN_SIDE}x{MIN_SIDE}")
    if h * w > MAX_INPUT_PIXELS:
        raise ImageError(f"Image too large ({w}x{h}); the limit is {MAX_INPUT_PIXELS} pixels")
    if max(h, w) > WORKING_SIDE:
        f = WORKING_SIDE / max(h, w)
        img = cv2.resize(img, (round(w * f), round(h * f)), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(img)


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


def normalize(gray: np.ndarray, mask: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Return (canonical image, canonical mask), both CANONICAL_SIZE square."""
    if mask is None:
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
