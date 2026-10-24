import cv2
import pytest

from genidtals import BiometricEngine, EnrollmentError, synthetic
from genidtals.cli import main


def captures(specimen, rng, n):
    return [synthetic.capture(specimen, rng) for _ in range(n)]


def test_enroll_and_authenticate(engine, specimens, rng):
    engine.enroll_user("alice", captures(specimens[1], rng, 3))
    result = engine.authenticate("alice", synthetic.capture(specimens[1], rng))
    assert result.authenticated, result
    assert result.confidence >= result.threshold
    assert set(result.modality_scores) == {"vascular", "texture", "follicular"}
    assert result.elapsed_ms < 250


def test_impostor_rejected(engine, specimens, rng):
    engine.enroll_user("alice", captures(specimens[1], rng, 3))
    result = engine.authenticate("alice", synthetic.capture(specimens[2], rng))
    assert not result.authenticated
    assert result.reason == "score below threshold"


def test_unknown_user(engine, specimens, rng):
    result = engine.authenticate("bob", synthetic.capture(specimens[1], rng))
    assert not result.authenticated
    assert result.reason == "user not enrolled"


def test_spoof_rejected_before_matching(engine, specimens, rng):
    engine.enroll_user("alice", captures(specimens[1], rng, 3))
    replay = synthetic.capture(specimens[1], rng, blur_sigma=3.0, noise_sigma=0.5)
    result = engine.authenticate("alice", replay)
    assert not result.authenticated
    assert result.reason.startswith("liveness check failed")


def test_enrollment_requires_enough_images(tmp_path, specimens, rng):
    engine = BiometricEngine("high", store_dir=tmp_path)
    with pytest.raises(EnrollmentError, match="at least 3"):
        engine.enroll_user("alice", captures(specimens[1], rng, 2))


def test_enrollment_rejects_mixed_subjects(engine, specimens, rng):
    images = captures(specimens[1], rng, 2) + [synthetic.capture(specimens[2], rng)]
    with pytest.raises(EnrollmentError, match="inconsistent"):
        engine.enroll_user("alice", images)


def test_duplicate_enrollment(engine, specimens, rng):
    engine.enroll_user("alice", captures(specimens[1], rng, 2))
    with pytest.raises(EnrollmentError, match="already enrolled"):
        engine.enroll_user("alice", captures(specimens[1], rng, 2))
    assert engine.enroll_user("alice", captures(specimens[1], rng, 2), overwrite=True)


def test_delete_user(engine, specimens, rng):
    engine.enroll_user("alice", captures(specimens[1], rng, 2))
    assert engine.is_enrolled("alice")
    assert engine.delete_user("alice")
    assert not engine.is_enrolled("alice")


def test_invalid_security_level(tmp_path):
    with pytest.raises(ValueError):
        BiometricEngine("ludicrous", store_dir=tmp_path)


def test_cli_flow(tmp_path, specimens, rng, capsys):
    paths = []
    for i, img in enumerate(captures(specimens[3], rng, 4)):
        p = tmp_path / f"img{i}.png"
        cv2.imwrite(str(p), img)
        paths.append(str(p))
    other = tmp_path / "other.png"
    cv2.imwrite(str(other), synthetic.capture(specimens[4], rng))
    store = ["--store", str(tmp_path / "store")]

    assert main(store + ["enroll", "--user", "carol", "--images", *paths[:3]]) == 0
    assert main(store + ["authenticate", "--user", "carol", "--image", paths[3]]) == 0
    assert main(store + ["authenticate", "--user", "carol", "--image", str(other)]) == 1
    assert main(store + ["authenticate", "--user", "carol", "--image", "missing.png"]) == 2
    assert main(store + ["delete", "--user", "carol"]) == 0
    assert main(store + ["diagnostics"]) == 0
    assert "PASS" in capsys.readouterr().out
