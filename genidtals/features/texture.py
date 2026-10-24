"""Dermatoglyphic micro-texture analysis: Haar wavelet energies + LBP histograms."""

import cv2
import numpy as np

CELLS = 4
LEVELS = 3
LBP_BINS = 10  # rotation-invariant uniform LBP, P=8


def _haar_level(img: np.ndarray):
    h, w = img.shape[0] // 2 * 2, img.shape[1] // 2 * 2
    img = img[:h, :w]
    a, b = img[0::2, 0::2], img[0::2, 1::2]
    c, d = img[1::2, 0::2], img[1::2, 1::2]
    ll = (a + b + c + d) / 4
    lh = (a - b + c - d) / 4
    hl = (a + b - c - d) / 4
    hh = (a - b - c + d) / 4
    return ll, (lh, hl, hh)


def _cell_means(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = np.zeros(CELLS * CELLS, np.float64)
    hs, ws = img.shape[0] // CELLS, img.shape[1] // CELLS
    for i in range(CELLS):
        for j in range(CELLS):
            m = mask[i * hs:(i + 1) * hs, j * ws:(j + 1) * ws]
            v = img[i * hs:(i + 1) * hs, j * ws:(j + 1) * ws]
            out[i * CELLS + j] = v[m].mean() if m.any() else 0.0
    return out


def wavelet_features(canon: np.ndarray, mask: np.ndarray) -> np.ndarray:
    ll = canon.astype(np.float64)
    m = mask > 0
    feats = []
    for _ in range(LEVELS):
        ll, bands = _haar_level(ll)
        m = cv2.resize(m.astype(np.uint8), (ll.shape[1], ll.shape[0]),
                       interpolation=cv2.INTER_NEAREST) > 0
        for band in bands:
            feats.append(np.log1p(_cell_means(band ** 2, m)))
    return np.concatenate(feats).astype(np.float32)


def _lbp_riu2(gray: np.ndarray) -> np.ndarray:
    img = gray.astype(np.int16)
    center = img[1:-1, 1:-1]
    offsets = [(-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1)]
    bits = [(img[1 + dy:img.shape[0] - 1 + dy, 1 + dx:img.shape[1] - 1 + dx] >= center)
            for dy, dx in offsets]
    bits = np.stack(bits).astype(np.uint8)
    transitions = np.sum(bits != np.roll(bits, 1, axis=0), axis=0)
    ones = bits.sum(axis=0)
    # Uniform patterns map to their count of set bits, the rest to bin P+1.
    codes = np.where(transitions <= 2, ones, 9)
    return np.pad(codes, 1, constant_values=9)


def lbp_features(canon: np.ndarray, mask: np.ndarray) -> np.ndarray:
    codes = _lbp_riu2(canon)
    inner = cv2.erode(mask, np.ones((5, 5), np.uint8)) > 0
    hs, ws = canon.shape[0] // CELLS, canon.shape[1] // CELLS
    hists = []
    for i in range(CELLS):
        for j in range(CELLS):
            c = codes[i * hs:(i + 1) * hs, j * ws:(j + 1) * ws]
            m = inner[i * hs:(i + 1) * hs, j * ws:(j + 1) * ws]
            h = np.bincount(c[m], minlength=LBP_BINS).astype(np.float64)
            hists.append(h / h.sum() if h.sum() else h)
    return np.concatenate(hists).astype(np.float32)


def extract(canon: np.ndarray, mask: np.ndarray) -> dict:
    return {
        "wavelet": wavelet_features(canon, mask),
        "lbp": lbp_features(canon, mask),
    }


def similarity(a: dict, b: dict) -> float:
    # Mean absolute difference of log band energies; 0.5 nats ~ no similarity.
    diff = float(np.mean(np.abs(a["wavelet"] - b["wavelet"])))
    wav = float(np.clip(1.0 - diff / 0.5, 0.0, 1.0))
    ha, hb = a["lbp"], b["lbp"]
    chi2 = 0.5 * np.sum((ha - hb) ** 2 / (ha + hb + 1e-9)) / (CELLS * CELLS)
    lbp = float(np.clip(1.0 - chi2 * 4.0, 0.0, 1.0))
    return 0.5 * wav + 0.5 * lbp
