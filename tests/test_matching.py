import numpy as np
import pytest

from genidtals import ImageError, synthetic
from genidtals.calibration import DEFAULT_CALIBRATION
from genidtals.features import keypoints
from genidtals.matching import Probe, compare, match
from genidtals.preprocessing import load_image, normalize


def probe(img):
    return Probe.from_gray(load_image(img))


def test_normalization_is_pose_invariant(specimens, rng):
    sp = specimens[1]
    a = synthetic.capture(sp, rng, max_rotation=0, max_shift=0)
    b = synthetic.capture(sp, rng, max_rotation=25, max_shift=25)
    ca, _ = normalize(a)
    cb, _ = normalize(b)
    assert np.corrcoef(ca.ravel().astype(float), cb.ravel().astype(float))[0, 1] > 0.8


def test_keypoints_register_arbitrary_rotation(specimens, rng):
    sp = specimens[2]
    ref = probe(synthetic.capture(sp, rng)).features
    flipped = np.rot90(synthetic.capture(sp, rng), 2).copy()
    km = keypoints.match(probe(flipped).features["keypoints"], ref["keypoints"])
    assert km.registered
    assert km.similarity == 1.0


def test_registration_improves_grid_modalities(specimens, captures):
    sp = specimens[3]
    a, b = captures(sp, 2)
    pa, pb = probe(a), probe(b)
    parts, registered = compare(pa, pb.features)
    assert registered
    assert parts["vascular"] > 0.7


def test_impostor_keypoints_do_not_register(specimens, captures):
    pa = probe(captures(specimens[1], 1)[0])
    pb = probe(captures(specimens[2], 1)[0])
    km = keypoints.match(pa.features["keypoints"], pb.features["keypoints"])
    assert not km.registered
    assert km.similarity == 0.0


def test_genuine_and_impostor_scores_separate(specimens, captures):
    refs = {i: [probe(img).features for img in captures(sp, 2)] for i, sp in specimens.items()}
    genuine, impostor = [], []
    for i, sp in specimens.items():
        p = probe(captures(sp, 1)[0])
        for j, r in refs.items():
            (genuine if i == j else impostor).append(match(p, r, DEFAULT_CALIBRATION).score)
    assert min(genuine) > DEFAULT_CALIBRATION.threshold("maximum") + 10
    assert max(impostor) < DEFAULT_CALIBRATION.threshold("low") - 5


def test_modality_scores_are_bounded(specimens, captures):
    a, b = probe(captures(specimens[1], 1)[0]), probe(captures(specimens[3], 1)[0])
    parts, _ = compare(a, b.features)
    assert set(parts) == {"keypoints", "vascular", "texture", "follicular"}
    assert all(0.0 <= v <= 1.0 for v in parts.values())


def test_blank_and_tiny_images_rejected():
    with pytest.raises(ImageError):
        load_image(np.zeros((32, 32), np.uint8))
    with pytest.raises(ImageError):
        probe(np.zeros((200, 200), np.uint8))
