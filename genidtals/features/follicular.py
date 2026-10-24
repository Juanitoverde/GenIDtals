"""Follicular distribution metrics: blob detection + spatial statistics.

Follicles appear as small, dark, roughly circular spots. Their individual
appearance changes with grooming, but the layout of follicle sites does not,
so only positions are used.
"""

import cv2
import numpy as np
from scipy.spatial import cKDTree

GRID = 8
NN_BINS = 16


def detect(canon: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Return an (N, 2) array of follicle centers in canonical coordinates."""
    params = cv2.SimpleBlobDetector_Params()
    params.filterByColor = True
    params.blobColor = 0
    params.filterByArea = True
    params.minArea = 4
    params.maxArea = 80
    params.filterByCircularity = True
    params.minCircularity = 0.5
    params.filterByInertia = False
    params.filterByConvexity = False
    params.minThreshold = 10
    params.maxThreshold = 200
    params.thresholdStep = 10
    detector = cv2.SimpleBlobDetector_create(params)
    inner = cv2.erode(mask, np.ones((11, 11), np.uint8))
    img = canon.copy()
    img[inner == 0] = 255
    pts = np.array([kp.pt for kp in detector.detect(img)], dtype=np.float32)
    return pts.reshape(-1, 2)


def extract(canon: np.ndarray, mask: np.ndarray) -> np.ndarray:
    pts = detect(canon, mask)
    size = canon.shape[0]

    density = np.zeros((size, size), np.float32)
    for x, y in pts.astype(int):
        density[min(y, size - 1), min(x, size - 1)] += 1.0
    density = cv2.GaussianBlur(density, (0, 0), size / GRID / 2)
    grid = cv2.resize(density, (GRID, GRID), interpolation=cv2.INTER_AREA).ravel()
    cells = cv2.resize(mask, (GRID, GRID), interpolation=cv2.INTER_AREA).ravel() > 127
    grid[cells] -= grid[cells].mean()
    grid[~cells] = 0
    norm = np.linalg.norm(grid)
    grid = grid / norm if norm > 0 else grid

    nn_hist = np.zeros(NN_BINS, np.float32)
    if len(pts) >= 2:
        dists, _ = cKDTree(pts).query(pts, k=2)
        nn = dists[:, 1] / size
        nn_hist, _ = np.histogram(nn, bins=NN_BINS, range=(0, 0.15))
        nn_hist = nn_hist.astype(np.float32) / max(1, nn_hist.sum())

    count = np.array([len(pts)], np.float32)
    return np.concatenate([grid, nn_hist, count]).astype(np.float32)


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    g = GRID * GRID
    if a[-1] < 3 or b[-1] < 3:
        return 0.0
    layout = max(0.0, float(np.dot(a[:g], b[:g])))
    spacing = float(np.minimum(a[g:g + NN_BINS], b[g:g + NN_BINS]).sum())
    count = float(min(a[-1], b[-1]) / max(a[-1], b[-1]))
    return 0.5 * layout + 0.3 * spacing + 0.2 * count
