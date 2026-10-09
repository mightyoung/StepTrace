from pathlib import Path

import pytest
from display_project.experiments import (
    FixedStreakConfig,
    PairedExperimentRunner,
    fixed_streak_adapter,
    UnavailableMethodAdapter,
)


def test_default_schedule_requires_all_seven_methods():
    with pytest.raises(ValueError):
        PairedExperimentRunner({"fixed_streak": fixed_streak_adapter()})


def test_fixed_streak_baseline_is_frozen():
    assert FixedStreakConfig() == FixedStreakConfig(4, 12)
    with pytest.raises(ValueError):
        fixed_streak_adapter(FixedStreakConfig(5, 12))


def test_explicit_schedule_is_allowed():
    runner = PairedExperimentRunner(
        {"fixed_streak": fixed_streak_adapter()}, frozen_schedule=["fixed_streak"]
    )
    assert runner.schedule == ("fixed_streak",)


def test_fixed_streak_without_processor_is_unavailable():
    result = fixed_streak_adapter()(Path("does-not-exist.mp4"))
    assert result["status"] == "unavailable"
    assert result["error_code"] == "FIXED_STREAK_PROCESSOR_UNAVAILABLE"


def test_unavailable_adapter_has_stable_error_fields():
    result = UnavailableMethodAdapter("yolo")(Path("does-not-exist.mp4"))
    assert result["status"] == "unavailable"
    assert result["error_code"] == "METHOD_UNAVAILABLE"
    assert result["error"]
