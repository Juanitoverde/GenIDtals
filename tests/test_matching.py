import numpy as np
import pytest

from genidtals import BiometricEngine, synthetic
from genidtals.config import SECURITY_LEVELS
from genidtals.matching import compare, fuse
from genidtals.preprocessing import ImageError, load_image, normalize


def template(img):
    return BiometricEngine.extract_template(img)


def test_normalization_is_pose_invariant(specimens, rng):
    sp = specimens[1]
    a = synthetic.capture(sp, rng, max_rotation=0, max_shift=0)
    b = synthetic.capture(sp, rng, max_rotation=25, max_shift=25)
    ca, _ = normalize(a)
    cb, _ = normalize(b)
    corr = np.corrcoef(ca.ravel().astype(float), cb.ravel().astype(float))[0, 1]
    assert corr > 0.8


def test_upside_down_capture_still_matches(specimens, rng):
    sp = specimens[2]
    ref = template(synthetic.capture(sp, rng))
    flipped = np.rot90(synthetic.capture(sp, rng), 2).copy()
    assert fuse(compare(template(flipped), ref)) > SECURITY_LEVELS["maximum"].match_threshold


def test_genuine_and_impostor_scores_separate(specimens, rng):
    refs = {i: template(synthetic.capture(sp, rng)) for i, sp in specimens.items()}
    genuine, impostor = [], []
    for i, sp in specimens.items():
        probe = template(synthetic.capture(sp, rng))
        for j, ref in refs.items():
            (genuine if i == j else impostor).append(fuse(compare(probe, ref)))
    assert min(genuine) > SECURITY_LEVELS["maximum"].match_threshold
    assert max(impostor) < SECURITY_LEVELS["low"].match_threshold


def test_modality_scores_are_bounded(specimens, rng):
    a = template(synthetic.capture(specimens[1], rng))
    b = template(synthetic.capture(specimens[3], rng))
    for score in compare(a, b).values():
        assert 0.0 <= score <= 1.0


def test_load_image_rejects_tiny_and_blank():
    with pytest.raises(ImageError):
        load_image(np.zeros((32, 32), np.uint8))
    with pytest.raises(ImageError):
        normalize(np.zeros((200, 200), np.uint8))


def test_load_image_accepts_color(specimens, rng):
    gray = synthetic.capture(specimens[1], rng)
    color = np.dstack([gray] * 3)
    assert load_image(color).shape == gray.shape
