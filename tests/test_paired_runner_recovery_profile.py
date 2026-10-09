from __future__ import annotations

import json

import pytest

from display_project.experiments.paired_runner import (
    append_jsonl_atomic,
    reconcile_host_interrupted,
)
from tests.test_paired_runner_failures import (
    _registration_candidate,
    _rehash_registration_candidate,
)


def _write_registration(tmp_path, *, scorer_metadata: dict[str, str]):
    ledger = tmp_path / "recovery.jsonl"
    sidecar = ledger.with_suffix(ledger.suffix + ".registration.jsonl")
    registration = _registration_candidate()
    registration.update(
        source_video_path=str(tmp_path / "missing.mp4"),
        video_hash="a" * 64,
        model_hash="b" * 64,
        config_hash="c" * 64,
        scorer_metadata=scorer_metadata,
    )
    _rehash_registration_candidate(registration)
    append_jsonl_atomic(sidecar, [registration])
    return ledger, registration


def test_non_fixture_recovery_requires_explicit_profile(tmp_path) -> None:
    ledger, _registration = _write_registration(tmp_path, scorer_metadata={})

    with pytest.raises(ValueError, match="requires scorer_metadata.experiment_profile"):
        reconcile_host_interrupted(ledger)

    assert not ledger.exists()


def test_recovery_preserves_profile_and_registration_identity(tmp_path) -> None:
    ledger, registration = _write_registration(
        tmp_path,
        scorer_metadata={"experiment_profile": "DEVELOPMENT"},
    )

    assert reconcile_host_interrupted(ledger) == 1
    recovered = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert recovered["experiment_profile"] == "DEVELOPMENT"
    assert recovered["registration_id"] == registration["registration_id"]
