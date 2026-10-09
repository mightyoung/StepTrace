import pytest

from display_project.risk.calibrator import RiskCalibrator


def test_fit_is_calibration_split_only_and_calibrates_probability():
    cal = RiskCalibrator(bins=2)
    with pytest.raises(ValueError, match="calibration-split-only"):
        cal.fit([0.2], [0], split="test")
    assert cal.fit([0.2, 0.8], [0, 1]).calibrate(0.2) == pytest.approx(0.2)


def test_fit_rejects_empty_misaligned_or_non_binary_calibration_data():
    cal = RiskCalibrator()
    for p, y in [([], []), ([0.2], []), ([0.2], [0.5])]:
        with pytest.raises(ValueError):
            cal.fit(p, y)


def test_fit_accepts_single_use_generators():
    cal = RiskCalibrator()
    cal.fit((x for x in [0.1, 0.9]), (x for x in [0, 1]))
    assert cal.fitted


def test_calibrate_requires_fit_and_probability_bounds():
    cal = RiskCalibrator()
    with pytest.raises(RuntimeError):
        cal.calibrate(0.5)
    cal.fit([0, 1], [0, 1])
    with pytest.raises(ValueError):
        cal.calibrate(1.1)


def test_brier_and_ece_report_are_deterministic():
    cal = RiskCalibrator(bins=2)
    p, y = [0.1, 0.9, 0.6, 0.4], [0, 1, 1, 0]
    assert cal.brier_score(p, y) == pytest.approx(0.085)
    assert cal.ece(p, y) == pytest.approx(0.25)
    report = cal.report(p, y)
    assert report.n == 4 and report.brier == pytest.approx(0.085)


def test_metrics_reject_out_of_range_probabilities_and_invalid_labels():
    cal = RiskCalibrator()
    with pytest.raises(ValueError):
        cal.brier_score([1.2], [1])
    with pytest.raises(ValueError):
        cal.ece([0.2], [2])


def test_bin_boundaries_include_probability_one():
    cal = RiskCalibrator(bins=10)
    assert cal.ece([0.0, 1.0], [0, 1]) == pytest.approx(0.0)


@pytest.mark.parametrize(
    "operation",
    [
        lambda: RiskCalibrator().fit(["0.2", "0.8"], [0, 1]),
        lambda: RiskCalibrator.brier_score(["0.2", "0.8"], [0, 1]),
        lambda: RiskCalibrator().ece(["0.2", "0.8"], [0, 1]),
        lambda: RiskCalibrator().fit([False, 0.8], [0, 1]),
        lambda: RiskCalibrator().fit([0.2, 0.8], [False, 1]),
    ],
)
def test_calibrator_rejects_string_and_boolean_numeric_inputs(operation):
    with pytest.raises(ValueError):
        operation()


def test_calibrator_rejects_boolean_or_fractional_bins():
    with pytest.raises(ValueError):
        RiskCalibrator(bins=True)
    with pytest.raises(ValueError):
        RiskCalibrator(bins=2.5)
