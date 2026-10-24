import numpy as np

from genidtals import liveness, synthetic
from genidtals.preprocessing import segment_roi


def test_live_capture_passes(specimens, rng):
    img = synthetic.capture(specimens[1], rng)
    assert liveness.check([img], segment_roi(img)).live


def test_blurry_replay_is_rejected(specimens, rng):
    img = synthetic.capture(specimens[1], rng, blur_sigma=3.0, noise_sigma=0.5)
    result = liveness.check([img], segment_roi(img))
    assert not result.live
    assert "smooth" in result.reasons[0]


def test_moire_pattern_is_rejected(specimens, rng):
    img = synthetic.capture(specimens[1], rng).astype(np.float32)
    yy, xx = np.mgrid[:img.shape[0], :img.shape[1]]
    grating = 8 * np.sin(2 * np.pi * 0.4 * (xx * 0.8 + yy * 0.6))
    attack = np.clip(img + grating * (img > 0), 0, 255).astype(np.uint8)
    result = liveness.check([attack])
    assert not result.live
    assert any("moire" in r for r in result.reasons)


def test_static_photo_burst_is_rejected(specimens, rng):
    img = synthetic.capture(specimens[1], rng)
    result = liveness.check([img, img.copy(), img.copy()], segment_roi(img))
    assert not result.live
    assert any("micro-motion" in r for r in result.reasons)


def test_burst_with_micro_motion_passes(specimens, rng):
    frames = synthetic.capture_sequence(specimens[1], frames=3, rng=rng)
    result = liveness.check(frames, segment_roi(frames[0]))
    assert result.live, result.reasons
    assert result.checks["mean_flow"] > liveness.MIN_FLOW
