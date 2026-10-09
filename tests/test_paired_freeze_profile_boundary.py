from __future__ import annotations

import pytest

from display_project.metrics.paired_freeze import compute_paired_metrics
from tests.test_paired_metrics_freeze import METHODS, rows


def test_runner_profile_alias_rebuilds_identical_metrics() -> None:
    legacy_rows = rows()
    runner_rows = []
    for source in legacy_rows:
        row = dict(source)
        row["experiment_profile"] = row.pop("profile")
        runner_rows.append(row)

    assert compute_paired_metrics(
        runner_rows, allow_methods=METHODS
    ) == compute_paired_metrics(legacy_rows, allow_methods=METHODS)


def test_conflicting_profile_fields_fail_closed() -> None:
    record_rows = rows()
    record_rows[0]["experiment_profile"] = "SOFTWARE_FIXTURE"

    with pytest.raises(ValueError, match="conflicts"):
        compute_paired_metrics(record_rows, allow_methods=METHODS)


def test_invalid_runner_profile_fails_closed_even_with_legacy_profile() -> None:
    record_rows = rows()
    record_rows[0]["experiment_profile"] = None

    with pytest.raises(ValueError, match="experiment_profile"):
        compute_paired_metrics(record_rows, allow_methods=METHODS)
