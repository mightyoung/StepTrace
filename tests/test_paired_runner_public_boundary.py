from __future__ import annotations

from dataclasses import asdict
import inspect

import pytest

from display_project.experiments import _paired_runner_core as core
from display_project.experiments import paired_runner


def test_public_attempt_record_remains_a_dataclass_type() -> None:
    assert inspect.isclass(paired_runner.AttemptRecord)
    record = paired_runner.AttemptRecord("video", "fixed_streak", "success")
    assert isinstance(record, core.AttemptRecord)
    assert asdict(record)["experiment_profile"] == "SOFTWARE_FIXTURE"


def test_runner_binds_profile_into_registration_metadata() -> None:
    runner = paired_runner.PairedExperimentRunner(
        {"fixed_streak": lambda *_args, **_kwargs: {}},
        frozen_schedule=["fixed_streak"],
    )
    assert runner.metadata["experiment_profile"] == "SOFTWARE_FIXTURE"
    assert (
        runner.metadata["scorer_metadata"]["experiment_profile"] == "SOFTWARE_FIXTURE"
    )


def test_runner_rejects_conflicting_registration_profile() -> None:
    with pytest.raises(ValueError, match="conflicts"):
        paired_runner.PairedExperimentRunner(
            {"fixed_streak": lambda *_args, **_kwargs: {}},
            frozen_schedule=["fixed_streak"],
            metadata={
                "experiment_profile": "SOFTWARE_FIXTURE",
                "scorer_metadata": {"experiment_profile": "DEVELOPMENT"},
            },
        )


@pytest.mark.parametrize("invalid_profile", [None, 17, True])
def test_runner_rejects_non_string_top_level_profile(invalid_profile: object) -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        paired_runner.PairedExperimentRunner(
            {"fixed_streak": lambda *_args, **_kwargs: {}},
            frozen_schedule=["fixed_streak"],
            metadata={"experiment_profile": invalid_profile},
        )


@pytest.mark.parametrize("invalid_profile", [None, 17, True, "  "])
def test_runner_rejects_non_string_scorer_profile(invalid_profile: object) -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        paired_runner.PairedExperimentRunner(
            {"fixed_streak": lambda *_args, **_kwargs: {}},
            frozen_schedule=["fixed_streak"],
            metadata={
                "experiment_profile": "SOFTWARE_FIXTURE",
                "scorer_metadata": {"experiment_profile": invalid_profile},
            },
        )


def test_public_exports_do_not_leak_core_import_modules() -> None:
    assert "hashlib" not in paired_runner.__all__
    assert "inspect" not in paired_runner.__all__
    assert "tempfile" not in paired_runner.__all__
