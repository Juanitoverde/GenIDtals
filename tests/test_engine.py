import io
import threading

import cv2
import pytest

from genidtals import (
    AlreadyEnrolledError,
    BiometricEngine,
    ConfigurationError,
    EnrollmentError,
    FileStore,
    KeyringFile,
    LivenessError,
    MemoryStore,
    QualityError,
    Reason,
    StaticKeyProvider,
    TemplateError,
    generate_key,
    synthetic,
)


def test_enroll_and_authenticate(engine, specimens, captures):
    r = engine.enroll_user("alice", captures(specimens[1], 2))
    assert r.references == 2 and all(q >= 90 for q in r.quality)
    result = engine.authenticate("alice", captures(specimens[1], 1)[0])
    assert result.authenticated and result.reason == Reason.OK
    assert result.elapsed_ms < 250


def test_impostor_rejected(engine, specimens, captures):
    engine.enroll_user("alice", captures(specimens[1], 2))
    result = engine.authenticate("alice", captures(specimens[2], 1)[0])
    assert not result and result.reason == Reason.NO_MATCH
    assert result.remaining_attempts == 4


def test_scores_hidden_by_default_and_exposed_on_request(make_engine, specimens, captures):
    hidden = make_engine()
    hidden.enroll_user("alice", captures(specimens[1], 2))
    r = hidden.authenticate("alice", captures(specimens[1], 1)[0])
    assert r.confidence is None and r.modality_scores is None and r.liveness is None
    assert r.quality.metrics == {} and r.quality.score >= 90

    shown = make_engine(expose_scores=True)
    shown.enroll_user("alice", captures(specimens[1], 2))
    r = shown.authenticate("alice", captures(specimens[1], 1)[0])
    assert r.confidence > r.threshold
    assert set(r.modality_scores) == {"keypoints", "vascular", "texture", "follicular"}


def test_unknown_user(engine, specimens, captures):
    result = engine.authenticate("bob", captures(specimens[1], 1)[0])
    assert result.reason == Reason.NOT_ENROLLED


def test_spoof_rejected_and_counted(engine, specimens, captures, rng):
    engine.enroll_user("alice", captures(specimens[1], 2))
    attack = synthetic.capture(specimens[1], rng).astype("float32")
    import numpy as np
    yy, xx = np.mgrid[:attack.shape[0], :attack.shape[1]]
    attack = np.clip(attack + 8 * np.sin(2 * np.pi * 0.4 * (xx * 0.8 + yy * 0.6)) * (attack > 0), 0, 255)
    result = engine.authenticate("alice", attack.astype("uint8"))
    assert result.reason == Reason.LIVENESS_FAILED
    assert result.remaining_attempts == 4


def test_low_quality_rejected_without_counting(engine, specimens, captures, rng):
    engine.enroll_user("alice", captures(specimens[1], 2))
    blurry = synthetic.capture(specimens[1], rng, blur_sigma=4.0, noise_sigma=0.3)
    result = engine.authenticate("alice", blurry)
    assert result.reason == Reason.LOW_QUALITY
    assert result.quality.issues
    assert engine.lockout_status("alice").failures == 0


def test_lockout_after_repeated_failures(make_engine, specimens, captures):
    engine = make_engine(max_failed_attempts=3)
    engine.enroll_user("alice", captures(specimens[1], 2))
    impostor = captures(specimens[2], 1)[0]
    reasons = [engine.authenticate("alice", impostor).reason for _ in range(3)]
    assert reasons == [Reason.NO_MATCH] * 3
    locked = engine.authenticate("alice", captures(specimens[1], 1)[0])
    assert locked.reason == Reason.LOCKED_OUT and locked.locked_until
    engine.unlock_user("alice")
    assert engine.authenticate("alice", captures(specimens[1], 1)[0])


def test_success_resets_failure_count(make_engine, specimens, captures):
    engine = make_engine(max_failed_attempts=3)
    engine.enroll_user("alice", captures(specimens[1], 2))
    engine.authenticate("alice", captures(specimens[2], 1)[0])
    assert engine.authenticate("alice", captures(specimens[1], 1)[0])
    assert engine.lockout_status("alice").failures == 0


def test_lockout_persists_across_engine_instances(keys, specimens, captures):
    from genidtals import EngineConfig
    store = MemoryStore()
    cfg = EngineConfig(max_failed_attempts=2)
    def fresh():
        return BiometricEngine(config=cfg, store=store, keys=keys)

    fresh().enroll_user("alice", captures(specimens[1], 2))
    for _ in range(2):
        fresh().authenticate("alice", captures(specimens[2], 1)[0])
    assert fresh().lockout_status("alice").locked


def test_enrollment_validation(make_engine, specimens, captures, rng):
    engine = make_engine("high")
    with pytest.raises(EnrollmentError, match="at least 3"):
        engine.enroll_user("alice", captures(specimens[1], 2))
    with pytest.raises(EnrollmentError, match="does not match"):
        engine.enroll_user("alice", captures(specimens[1], 2) + captures(specimens[2], 1))
    with pytest.raises(QualityError):
        engine.enroll_user("alice", captures(specimens[1], 2) + [
            synthetic.capture(specimens[1], rng, blur_sigma=4.0, noise_sigma=0.3)])
    assert not engine.is_enrolled("alice")


def test_enrollment_rejects_spoofed_reference(engine, specimens, rng):
    replay = synthetic.capture(specimens[1], rng, blur_sigma=2.2, noise_sigma=0.3)
    live = synthetic.capture(specimens[1], rng)
    with pytest.raises((LivenessError, QualityError)):
        engine.enroll_user("alice", [live, replay])


def test_duplicate_enrollment(engine, specimens, captures):
    engine.enroll_user("alice", captures(specimens[1], 2))
    with pytest.raises(AlreadyEnrolledError):
        engine.enroll_user("alice", captures(specimens[1], 2))
    engine.enroll_user("alice", captures(specimens[1], 2), overwrite=True)
    assert engine.enrolled_count() == 1


def test_delete_user(engine, specimens, captures):
    engine.enroll_user("alice", captures(specimens[1], 2))
    assert engine.delete_user("alice")
    assert not engine.is_enrolled("alice")
    assert not engine.delete_user("alice")


def test_audit_events_never_contain_user_id(make_engine, specimens, captures):
    events = []
    engine = make_engine(audit_hook=events.append)
    engine.enroll_user("alice@example.com", captures(specimens[1], 2))
    engine.authenticate("alice@example.com", captures(specimens[1], 1)[0])
    assert [(e.event, e.outcome) for e in events] == [("enroll", "success"), ("verify", "success")]
    assert all("alice" not in str(e.to_dict()) for e in events)
    assert events[0].subject == events[1].subject


def test_failing_audit_hook_does_not_break_auth(make_engine, specimens, captures):
    def boom(event):
        raise RuntimeError("sink down")
    engine = make_engine(audit_hook=boom)
    engine.enroll_user("alice", captures(specimens[1], 2))
    assert engine.authenticate("alice", captures(specimens[1], 1)[0])


def test_store_contents_reveal_nothing(tmp_path, keys, specimens, captures):
    engine = BiometricEngine(store=FileStore(tmp_path), keys=keys)
    engine.enroll_user("alice", captures(specimens[1], 2))
    (path,) = (tmp_path / "t").glob("*.bin")
    data = path.read_bytes()
    assert b"alice" not in data and b"references" not in data and "alice" not in path.name


def test_image_input_types(engine, specimens, captures, tmp_path):
    imgs = captures(specimens[1], 3)
    path = tmp_path / "a.png"
    cv2.imwrite(str(path), imgs[0])
    png = cv2.imencode(".png", imgs[1])[1].tobytes()
    engine.enroll_user("alice", [str(path), png])
    assert engine.authenticate("alice", io.BytesIO(cv2.imencode(".jpg", imgs[2])[1].tobytes()))


@pytest.mark.parametrize("bad", ["", "x" * 257, "a\nb", "a\x00b", None])
def test_user_id_validation(engine, bad):
    with pytest.raises(ConfigurationError):
        engine.is_enrolled(bad)


# ----------------------------------------------------------- stateless API


def test_stateless_template_roundtrip(engine, specimens, captures):
    blob = engine.create_template("user-1", captures(specimens[1], 2))
    assert isinstance(blob, bytes) and blob.startswith(b"GIDT")
    assert engine.verify_template("user-1", blob, captures(specimens[1], 1)[0])
    assert not engine.verify_template("user-1", blob, captures(specimens[2], 1)[0])
    assert engine.enrolled_count() == 0


def test_template_is_bound_to_subject(engine, specimens, captures):
    blob = engine.create_template("user-1", captures(specimens[1], 2))
    with pytest.raises(TemplateError):
        engine.verify_template("user-2", blob, captures(specimens[1], 1)[0])


def test_template_unusable_with_other_keys(engine, specimens, captures):
    blob = engine.create_template("user-1", captures(specimens[1], 2))
    other = BiometricEngine.ephemeral()
    with pytest.raises(TemplateError):
        other.verify_template("user-1", blob, captures(specimens[1], 1)[0])


def test_reseal_after_rotation(specimens, captures):
    old = StaticKeyProvider([generate_key()])
    blob = BiometricEngine(store=MemoryStore(), keys=old).create_template("u", captures(specimens[1], 2))
    new_key = generate_key()
    both = StaticKeyProvider([new_key, old.get_key(old.current_key_id)])
    rotated = BiometricEngine(store=MemoryStore(), keys=both)
    fresh = rotated.reseal_template("u", blob)
    only_new = BiometricEngine(store=MemoryStore(), keys=StaticKeyProvider([new_key]))
    assert only_new.verify_template("u", fresh, captures(specimens[1], 1)[0])


# ------------------------------------------------------------ key rotation


def test_managed_store_key_rotation(tmp_path, specimens, captures):
    ring = KeyringFile(tmp_path / "keyring.json")
    store = FileStore(tmp_path / "store")
    engine = BiometricEngine(store=store, keys=ring)
    engine.enroll_user("alice", captures(specimens[1], 2))
    engine.enroll_user("bob", captures(specimens[2], 2))
    old = ring.current_key_id

    ring.rotate()
    engine = BiometricEngine(store=store, keys=ring)
    assert engine.authenticate("alice", captures(specimens[1], 1)[0])  # still readable before re-seal
    assert engine.rotate_keys() == 2
    assert engine.rotate_keys() == 0
    ring.retire(old)

    engine = BiometricEngine(store=store, keys=KeyringFile(tmp_path / "keyring.json"))
    assert engine.authenticate("alice", captures(specimens[1], 1)[0])
    assert engine.authenticate("bob", captures(specimens[2], 1)[0])
    assert engine.enrolled_count() == 2


def test_concurrent_authentication(engine, specimens, captures):
    engine.enroll_user("alice", captures(specimens[1], 2))
    probes = captures(specimens[1], 6)
    results = []

    def run(img):
        results.append(engine.authenticate("alice", img).authenticated)

    threads = [threading.Thread(target=run, args=(p,)) for p in probes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [True] * 6


def test_config_conflicts(keys):
    from genidtals import EngineConfig
    with pytest.raises(ConfigurationError):
        BiometricEngine("high", config=EngineConfig(security_level="low"), store=MemoryStore(), keys=keys)
    with pytest.raises(ConfigurationError):
        EngineConfig(security_level="ludicrous")
