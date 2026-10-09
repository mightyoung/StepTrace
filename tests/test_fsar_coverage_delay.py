import math
import pytest
from display_project.metrics.paired import (
    compute_fsar,
    confirmation_delay,
    coverage,
    freeze_operating_point,
)


def test_fsar_explicit_zero_emitted_is_nan():
    assert math.isnan(compute_fsar(false_advances=0, emitted_advances=0))


def test_fsar_explicit_counts():
    assert compute_fsar(false_advances=1, emitted_advances=4) == 0.25


@pytest.mark.parametrize("false_advances", [float("nan"), float("inf"), True])
def test_fsar_rejects_nonfinite_or_boolean_counts(false_advances):
    with pytest.raises(ValueError, match="invalid advancement counts"):
        compute_fsar(false_advances=false_advances, emitted_advances=1)


def test_fsar_vector_denominator_is_all_emitted_advances():
    assert compute_fsar([True, True, False], [True, False, False]) == 0.5


def test_unknown_is_not_coverage():
    assert coverage(["normal", "unknown", None, "deviation"]) == 0.5


def test_confirmation_delay_preserves_right_censoring_exposure_seconds():
    events = [{"candidate_frame": 2}, {"candidate_frame": 4, "confirmed_frame": 9}]
    short = confirmation_delay(
        events, source_fps=2, video_end_frame=10, return_censoring=True
    )
    long = confirmation_delay(
        events, source_fps=2, video_end_frame=100, return_censoring=True
    )
    assert short == {
        "mean_delay": 2.5,
        "observed": 1,
        "right_censored": 1,
        "censoring_times": [4.0],
        "unit": "seconds",
    }
    assert long["censoring_times"] == [49.0]
    assert short != long


def test_confirmation_delay_rejects_censoring_without_video_end():
    with pytest.raises(ValueError, match="video_end_frame"):
        confirmation_delay([{"candidate_frame": 2}], return_censoring=True)


def test_single_cell_operating_point_is_ineligible():
    result = freeze_operating_point(
        [{"threshold": 0.5, "coverage": 0.5, "fsar": 0.1, "emitted_advances": 1}],
        target=0.5,
    )
    assert not result.eligible
