"""Local keypoint matching with geometric verification.

SIFT keypoints are detected on the canonical image (vessel junctions,
follicles and texture corners all produce stable keypoints). Matching uses
RootSIFT descriptors, Lowe's ratio test, and a RANSAC-fitted similarity
transform: only correspondences consistent with a single rigid placement
count. Random coincidences between different people almost never agree
geometrically, which makes the inlier count highly discriminative, and the
fitted transform registers the probe onto the reference for the other
modalities.
"""

import threading
from dataclasses import dataclass

import cv2
import numpy as np

MAX_KEYPOINTS = 300
RATIO = 0.85
RANSAC_THRESHOLD = 5.0
# Canonical images are scale-normalized, so a genuine transform is close to 1.
SCALE_RANGE = (0.75, 1.33)
# Inliers needed before the transform is trusted for registration.
MIN_INLIERS_FOR_REGISTRATION = 12
# Inlier counts are mapped linearly onto [0, 1] between these bounds.
INLIER_FLOOR = 8
INLIER_SATURATION = 48

_local = threading.local()


def _sift():
    # SIFT objects are not documented as thread-safe; keep one per thread.
    if not hasattr(_local, "sift"):
        _local.sift = cv2.SIFT_create(nfeatures=MAX_KEYPOINTS, contrastThreshold=0.02)
    return _local.sift


def extract(canon: np.ndarray, mask: np.ndarray) -> dict:
    inner = cv2.erode(mask, np.ones((9, 9), np.uint8))
    kps, desc = _sift().detectAndCompute(canon, inner)
    if not kps:
        return {"pts": np.zeros((0, 2), np.float32), "desc": np.zeros((0, 128), np.uint8)}
    pts = np.array([k.pt for k in kps], np.float32)
    return {"pts": pts, "desc": np.clip(desc, 0, 255).astype(np.uint8)}


def _root_sift(desc: np.ndarray) -> np.ndarray:
    d = desc.astype(np.float32)
    d /= d.sum(axis=1, keepdims=True) + 1e-7
    return np.sqrt(d)


@dataclass
class KeypointMatch:
    inliers: int
    # 2x3 similarity transform mapping probe -> reference canonical coordinates.
    transform: np.ndarray | None

    @property
    def similarity(self) -> float:
        return float(np.clip((self.inliers - INLIER_FLOOR) / (INLIER_SATURATION - INLIER_FLOOR), 0, 1))

    @property
    def registered(self) -> bool:
        return self.transform is not None and self.inliers >= MIN_INLIERS_FOR_REGISTRATION


def match(probe: dict, reference: dict) -> KeypointMatch:
    pa, pb = probe["pts"], reference["pts"]
    if len(pa) < 4 or len(pb) < 4:
        return KeypointMatch(0, None)
    knn = cv2.BFMatcher(cv2.NORM_L2).knnMatch(_root_sift(probe["desc"]), _root_sift(reference["desc"]), k=2)
    good = [m[0] for m in knn if len(m) == 2 and m[0].distance < RATIO * m[1].distance]
    if len(good) < 4:
        return KeypointMatch(0, None)
    src = pa[[g.queryIdx for g in good]]
    dst = pb[[g.trainIdx for g in good]]
    transform, inliers = cv2.estimateAffinePartial2D(
        src, dst, method=cv2.RANSAC, ransacReprojThreshold=RANSAC_THRESHOLD,
        maxIters=2000, confidence=0.995)
    if transform is None:
        return KeypointMatch(0, None)
    scale = float(np.hypot(transform[0, 0], transform[1, 0]))
    if not SCALE_RANGE[0] < scale < SCALE_RANGE[1]:
        return KeypointMatch(0, None)
    # Count each reference keypoint once, so repeated matches cannot inflate the score.
    idx = np.flatnonzero(inliers.ravel())
    unique = len({good[i].trainIdx for i in idx})
    return KeypointMatch(unique, transform)


def similarity(probe: dict, reference: dict) -> float:
    return match(probe, reference).similarity
