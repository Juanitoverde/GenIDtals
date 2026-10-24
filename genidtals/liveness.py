"""Presentation-attack (liveness) detection.

Three independent signals:

* **Sharpness** - variance of the Laplacian. Re-captured prints and screen
  replays are typically softer than a live capture.
* **Moire** - isolated high-frequency peaks in the spectrum, characteristic of
  photographing a display or a halftone print.
* **Micro-motion** - dense Farneback optical flow across a short burst of
  frames. Live tissue moves slightly; a static photo held still does not, and
  large motion means the capture is unusable.

Single images can only be checked for sharpness and moire; a burst of frames
is needed for the motion check.
"""

from dataclasses import dataclass, field

import cv2
import numpy as np

MIN_LAPLACIAN_VAR = 40.0
MAX_MOIRE_PROMINENCE = 2.5  # natural-log units above the local spectrum
MIN_FLOW = 0.15  # mean pixel displacement inside the ROI
MAX_FLOW = 6.0


@dataclass
class LivenessResult:
    live: bool
    checks: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)


def laplacian_variance(gray: np.ndarray, mask: np.ndarray | None = None) -> float:
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    if mask is not None:
        inner = cv2.erode(mask, np.ones((9, 9), np.uint8)) > 0
        lap = lap[inner]
    return float(lap.var())


def moire_prominence(gray: np.ndarray) -> float:
    """Height of the strongest high-frequency spectral peak above its neighborhood.

    Measured on the log-magnitude spectrum, so natural texture (a smooth,
    broadband spectrum) scores low while a periodic interference pattern
    produces an isolated spike.
    """
    img = gray.astype(np.float32)
    img -= img.mean()
    h, w = img.shape
    win = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    spec = np.log1p(np.abs(np.fft.fftshift(np.fft.fft2(img * win)))).astype(np.float32)
    background = cv2.blur(spec, (15, 15))
    yy, xx = np.ogrid[:h, :w]
    r = np.hypot(yy - h / 2, xx - w / 2) / (min(h, w) / 2)
    band = (r > 0.25) & (r < 0.95)
    return float((spec - background)[band].max())


def mean_flow(frames: list[np.ndarray], mask: np.ndarray | None = None) -> float:
    mags = []
    for prev, nxt in zip(frames, frames[1:], strict=False):
        flow = cv2.calcOpticalFlowFarneback(prev, nxt, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        mag = np.linalg.norm(flow, axis=2)
        mags.append(float(mag[mask > 0].mean() if mask is not None else mag.mean()))
    return float(np.mean(mags))


def check(frames: list[np.ndarray], mask: np.ndarray | None = None) -> LivenessResult:
    """Run all applicable checks on one image or a burst of frames (uint8 gray)."""
    result = LivenessResult(live=True)
    first = frames[0]

    lv = laplacian_variance(first, mask)
    result.checks["laplacian_variance"] = round(lv, 2)
    if lv < MIN_LAPLACIAN_VAR:
        result.reasons.append(f"image too smooth (Laplacian variance {lv:.1f} < {MIN_LAPLACIAN_VAR})")

    mp = moire_prominence(first)
    result.checks["moire_prominence"] = round(mp, 2)
    if mp > MAX_MOIRE_PROMINENCE:
        result.reasons.append(f"periodic moire pattern detected (prominence {mp:.2f})")

    if len(frames) > 1:
        if any(f.shape != first.shape for f in frames):
            result.reasons.append("frames have mismatched dimensions")
        else:
            flow = mean_flow(frames, mask)
            result.checks["mean_flow"] = round(flow, 3)
            if flow < MIN_FLOW:
                result.reasons.append(f"no micro-motion between frames (flow {flow:.3f})")
            elif flow > MAX_FLOW:
                result.reasons.append(f"excessive motion between frames (flow {flow:.2f})")

    result.live = not result.reasons
    return result
