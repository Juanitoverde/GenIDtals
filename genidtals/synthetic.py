"""Synthetic specimen generator for testing and demos.

Each identity is a deterministic function of an integer seed: ROI shape,
vascular network, follicle layout and base texture. ``capture`` then applies
per-sample acquisition variation (pose, scale, lighting, sensor noise) so
that genuine and impostor comparisons can be exercised without real data.
"""

from dataclasses import dataclass

import cv2
import numpy as np

SPECIMEN_SIZE = 320
CAPTURE_SIZE = 400


@dataclass
class Specimen:
    seed: int
    image: np.ndarray  # float32, background = 0
    mask: np.ndarray   # uint8


def _shape_mask(rng: np.random.Generator) -> np.ndarray:
    s = SPECIMEN_SIZE
    mask = np.zeros((s, s), np.uint8)
    # Taller than wide, so the principal axis is well defined.
    lobe_r = int(rng.uniform(50, 58))
    spread = int(rng.uniform(30, 38))
    base_y = int(s * 0.66)
    cv2.circle(mask, (s // 2 - spread, base_y), lobe_r, 255, -1)
    cv2.circle(mask, (s // 2 + spread, base_y), lobe_r, 255, -1)
    neck = np.array([
        [s // 2 - spread - lobe_r // 2, base_y - lobe_r // 2],
        [s // 2 + spread + lobe_r // 2, base_y - lobe_r // 2],
        [s // 2 + 28, int(s * 0.06)],
        [s // 2 - 28, int(s * 0.06)],
    ], np.int32)
    cv2.fillPoly(mask, [neck], 255)
    return mask


def _texture(rng: np.random.Generator) -> np.ndarray:
    s = SPECIMEN_SIZE
    tex = np.zeros((s, s), np.float32)
    # Texture coarseness and strength vary between individuals.
    for sigma, amp in ((1.0, rng.uniform(2, 10)), (3.0, rng.uniform(3, 12)), (8.0, rng.uniform(4, 14))):
        noise = rng.standard_normal((s, s)).astype(np.float32)
        noise = cv2.GaussianBlur(noise, (0, 0), sigma)
        tex += amp * noise / (noise.std() + 1e-6)
    return tex


def _vessels(rng: np.random.Generator, mask: np.ndarray) -> np.ndarray:
    s = SPECIMEN_SIZE
    layer = np.zeros((s, s), np.float32)
    ys, xs = np.nonzero(mask)
    for _ in range(int(rng.integers(7, 12))):
        i = rng.integers(len(xs))
        p = np.array([xs[i], ys[i]], np.float64)
        heading = rng.uniform(0, 2 * np.pi)
        thickness = int(rng.integers(2, 5))
        pts = [p.copy()]
        for _ in range(int(rng.integers(40, 90))):
            heading += rng.normal(0, 0.25)
            p += 3.0 * np.array([np.cos(heading), np.sin(heading)])
            pts.append(p.copy())
        cv2.polylines(layer, [np.array(pts, np.int32)], False, 1.0, thickness, cv2.LINE_AA)
    return cv2.GaussianBlur(layer, (0, 0), 0.8)


def _follicles(rng: np.random.Generator, mask: np.ndarray) -> np.ndarray:
    s = SPECIMEN_SIZE
    layer = np.zeros((s, s), np.float32)
    inner = cv2.erode(mask, np.ones((25, 25), np.uint8))
    ys, xs = np.nonzero(inner)
    for i in rng.choice(len(xs), size=int(rng.integers(60, 110)), replace=False):
        cv2.circle(layer, (int(xs[i]), int(ys[i])), int(rng.integers(2, 4)), 1.0, -1, cv2.LINE_AA)
    return layer


def make_specimen(seed: int) -> Specimen:
    rng = np.random.default_rng(seed)
    mask = _shape_mask(rng)
    s = SPECIMEN_SIZE
    # Tissue is brighter toward the top, giving an anatomical up direction.
    gradient = np.linspace(185, 135, s, dtype=np.float32)[:, None].repeat(s, axis=1)
    img = gradient + _texture(rng)
    img -= 45.0 * _vessels(rng, mask)
    img -= 70.0 * _follicles(rng, mask)
    img[mask == 0] = 0
    return Specimen(seed, img, mask)


def capture(specimen: Specimen, rng: np.random.Generator | None = None,
            max_rotation: float = 20.0, max_shift: float = 20.0,
            noise_sigma: float = 3.0, blur_sigma: float = 0.0) -> np.ndarray:
    """Simulate acquiring one image of a specimen; returns uint8 grayscale."""
    rng = rng or np.random.default_rng()
    c = SPECIMEN_SIZE / 2
    angle = rng.uniform(-max_rotation, max_rotation)
    scale = rng.uniform(0.92, 1.08)
    mat = cv2.getRotationMatrix2D((c, c), angle, scale)
    mat[0, 2] += CAPTURE_SIZE / 2 - c + rng.uniform(-max_shift, max_shift)
    mat[1, 2] += CAPTURE_SIZE / 2 - c + rng.uniform(-max_shift, max_shift)
    out = cv2.warpAffine(specimen.image, mat, (CAPTURE_SIZE, CAPTURE_SIZE),
                         flags=cv2.INTER_LINEAR, borderValue=0)
    fg = cv2.warpAffine(specimen.mask, mat, (CAPTURE_SIZE, CAPTURE_SIZE),
                        flags=cv2.INTER_NEAREST) > 0
    gain, offset = rng.uniform(0.9, 1.1), rng.uniform(-12, 12)
    out[fg] = out[fg] * gain + offset
    if blur_sigma > 0:
        out = cv2.GaussianBlur(out, (0, 0), blur_sigma)
    out += rng.normal(0, noise_sigma, out.shape).astype(np.float32)
    return np.clip(out, 0, 255).astype(np.uint8)


def capture_sequence(specimen: Specimen, frames: int = 3,
                     rng: np.random.Generator | None = None) -> list[np.ndarray]:
    """A short burst with natural micro-motion between frames (for liveness)."""
    rng = rng or np.random.default_rng()
    base = rng.integers(0, 2 ** 31)
    seq = []
    for k in range(frames):
        frame_rng = np.random.default_rng(base)
        # Same pose each frame, then a small independent jitter on top.
        img = capture(specimen, frame_rng)
        jitter = np.float32([[1, 0, rng.uniform(-1.5, 1.5)], [0, 1, rng.uniform(-1.5, 1.5)]])
        img = cv2.warpAffine(img, jitter, img.shape[::-1], borderValue=0)
        seq.append(img)
    return seq
