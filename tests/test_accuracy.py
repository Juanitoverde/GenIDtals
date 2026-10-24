"""Accuracy regression gate on the hard synthetic benchmark."""

import pytest

from genidtals.calibration import DEFAULT_CALIBRATION, fit
from genidtals.config import SECURITY_LEVELS
from genidtals.evaluation import collect_scores, report, synthetic_subjects


@pytest.fixture(scope="module")
def scores():
    subjects = synthetic_subjects(count=12, images_per_subject=5, hard=True, seed=3)
    return collect_scores(subjects, enroll_count=3)


def test_zero_error_rate_with_margin(scores):
    rep = report(scores, DEFAULT_CALIBRATION)
    assert rep.failed_to_enroll == 0 and rep.failed_to_acquire == 0
    assert rep.eer == 0.0
    for level in SECURITY_LEVELS:
        assert rep.far(DEFAULT_CALIBRATION.threshold(level)) == 0.0
        assert rep.frr(DEFAULT_CALIBRATION.threshold(level)) == 0.0
    # Margins: genuine well above the strictest threshold, impostors well below the loosest.
    assert rep.genuine.min() > DEFAULT_CALIBRATION.threshold("maximum") + 15
    assert rep.impostor.max() < DEFAULT_CALIBRATION.threshold("low") - 5


def test_fitted_calibration_is_ordered_and_separating(scores):
    cal = fit(scores.genuine, scores.impostor, "test")
    levels = [cal.threshold(lvl) for lvl in ("low", "standard", "high", "maximum")]
    assert levels == sorted(levels)
    rep = report(scores, cal)
    assert rep.frr(cal.threshold("maximum")) == 0.0
    assert rep.far(cal.threshold("low")) == 0.0
