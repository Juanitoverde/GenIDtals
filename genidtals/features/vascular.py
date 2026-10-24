"""Vascular pattern mapping via multi-scale Hessian vesselness (Frangi-style)."""

import cv2
import numpy as np

GRID = 32
SCALES = (1.5, 2.5, 3.5)


def vesselness(gray: np.ndarray, scales=SCALES, beta: float = 0.5, c: float = 15.0) -> np.ndarray:
    """Response map for dark, tubular structures on a brighter background."""
    img = gray.astype(np.float32)
    response = np.zeros_like(img)
    for s in scales:
        smoothed = cv2.GaussianBlur(img, (0, 0), s)
        dxx = cv2.Sobel(smoothed, cv2.CV_32F, 2, 0, ksize=3) * s * s
        dyy = cv2.Sobel(smoothed, cv2.CV_32F, 0, 2, ksize=3) * s * s
        dxy = cv2.Sobel(smoothed, cv2.CV_32F, 1, 1, ksize=3) * s * s
        # Eigenvalues of the 2x2 Hessian, ordered so |l1| <= |l2|.
        tmp = np.sqrt((dxx - dyy) ** 2 + 4 * dxy ** 2)
        e1 = 0.5 * (dxx + dyy + tmp)
        e2 = 0.5 * (dxx + dyy - tmp)
        swap = np.abs(e1) > np.abs(e2)
        l1 = np.where(swap, e2, e1)
        l2 = np.where(swap, e1, e2)
        rb = (l1 / (l2 + 1e-6)) ** 2
        S2 = l1 ** 2 + l2 ** 2
        v = np.exp(-rb / (2 * beta ** 2)) * (1 - np.exp(-S2 / (2 * c ** 2)))
        # Dark vessels have a positive second derivative across the vessel.
        v[l2 < 0] = 0
        response = np.maximum(response, v)
    return response


def extract(canon: np.ndarray, mask: np.ndarray) -> np.ndarray:
    inner = cv2.erode(mask, np.ones((9, 9), np.uint8))
    v = vesselness(canon)
    v[inner == 0] = 0
    v = cv2.GaussianBlur(v, (0, 0), 3)
    grid = cv2.resize(v, (GRID, GRID), interpolation=cv2.INTER_AREA)
    # Center within the ROI only, so the ROI outline itself does not correlate.
    cells = cv2.resize(inner, (GRID, GRID), interpolation=cv2.INTER_AREA) > 127
    grid[cells] -= grid[cells].mean()
    grid[~cells] = 0
    norm = np.linalg.norm(grid)
    return (grid / norm if norm > 0 else grid).astype(np.float32).ravel()


def similarity(a: np.ndarray, b: np.ndarray, max_shift: int = 2) -> float:
    """Best normalized cross-correlation over small grid translations, in [0, 1]."""
    ga, gb = a.reshape(GRID, GRID), b.reshape(GRID, GRID)
    best = -1.0
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            shifted = np.roll(gb, (dy, dx), axis=(0, 1))
            best = max(best, float(np.sum(ga * shifted)))
    return max(0.0, best)
