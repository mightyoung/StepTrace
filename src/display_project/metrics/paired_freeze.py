"""Public paired-freeze API with canonical runner-profile normalization."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from display_project.metrics import _paired_freeze_core as _core


FreezeArtifact = _core.FreezeArtifact
verify_freeze_artifact = _core.verify_freeze_artifact
write_freeze_artifact = _core.write_freeze_artifact


def _profile(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _normalized_records(
    records: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for source in records:
        row = dict(source)
        legacy_profile = (
            _profile(row["profile"], "profile") if "profile" in row else None
        )
        runner_profile = (
            _profile(row["experiment_profile"], "experiment_profile")
            if "experiment_profile" in row
            else None
        )
        if (
            legacy_profile is not None
            and runner_profile is not None
            and legacy_profile.upper() != runner_profile.upper()
        ):
            raise ValueError("profile conflicts with experiment_profile")
        if legacy_profile is None and runner_profile is not None:
            row["profile"] = runner_profile
        normalized.append(row)
    return normalized


def compute_paired_metrics(
    records: Iterable[Mapping[str, Any]],
    *,
    allow_methods: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Compute metrics while accepting the runner's canonical profile field."""

    return _core.compute_paired_metrics(
        _normalized_records(records),
        allow_methods=allow_methods,
    )


def freeze_paired_metrics(
    records: Iterable[Mapping[str, Any]],
    *,
    input_ledger_hash: str,
    operating_points: Mapping[str, Iterable[Mapping[str, Any]]],
    allow_methods: Sequence[str] | None = None,
    profile: str = "development",
    fixed_confirm_frames: int = 4,
    fixed_cooldown_frames: int = 12,
    expected_artifact_hash: str | None = None,
    branch: str = "A_FSAR_AT_MATCHED_COVERAGE",
    target: float = 0.5,
    delta_c: float = 0.0,
    delta_r: float = 0.0,
    min_support_width: float = 0.0,
    min_coverage: float = 0.5,
    min_units_per_task: int = 1,
) -> FreezeArtifact:
    """Freeze metrics from raw runner rows without mutating ledger evidence."""

    return _core.freeze_paired_metrics(
        _normalized_records(records),
        input_ledger_hash=input_ledger_hash,
        operating_points=operating_points,
        allow_methods=allow_methods,
        profile=profile,
        fixed_confirm_frames=fixed_confirm_frames,
        fixed_cooldown_frames=fixed_cooldown_frames,
        expected_artifact_hash=expected_artifact_hash,
        branch=branch,
        target=target,
        delta_c=delta_c,
        delta_r=delta_r,
        min_support_width=min_support_width,
        min_coverage=min_coverage,
        min_units_per_task=min_units_per_task,
    )


__all__ = [
    "FreezeArtifact",
    "compute_paired_metrics",
    "freeze_paired_metrics",
    "verify_freeze_artifact",
    "write_freeze_artifact",
]
