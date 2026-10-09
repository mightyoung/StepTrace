from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path
import pytest
import display_project.data.experiment_identity as ei
from display_project.data.experiment_identity import (
    ExperimentIdentityError,
    resolve_experiment_identity,
)


def _case(tmp_path: Path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"registered-video")
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    fields = [
        "asset_id",
        "source_dataset",
        "canonical_root_id",
        "canonical_group_id",
        "parent_asset_id",
        "operator_id",
        "complete_job_id",
        "task_id",
        "project_split",
        "source_split",
        "formal_use_allowed",
        "independent_unit_eligible",
        "sha256",
        "record_type",
        "derivation",
        "frame_start",
        "frame_end",
        "frame_count",
        "source_path",
    ]
    row = dict(
        asset_id="ASSET-1",
        source_dataset="IndustReal",
        canonical_root_id="ROOT-1",
        canonical_group_id="GROUP-1",
        parent_asset_id="NONE",
        operator_id="OP-1",
        complete_job_id="JOB-1",
        task_id="TASK-1",
        project_split="DEVELOPMENT",
        source_split="train",
        formal_use_allowed="NO",
        independent_unit_eligible="NO",
        sha256=digest,
        record_type="CANONICAL_ROOT_VIDEO",
        derivation="NONE",
        frame_start="0",
        frame_end="0",
        frame_count="1",
        source_path="video.mp4",
    )
    ledger = tmp_path / "ledger.csv"
    with ledger.open("w", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        w.writerow(row)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.1.0",
                "status": "READY",
                "rules": {
                    "no_cross_split_identity": True,
                    "adjacent_frames_inherit_root_split": True,
                    "unknown_identity_blocks_calibration_pilot_formal_test": True,
                },
                "records": [
                    dict(
                        asset_id="ASSET-1",
                        source_dataset="IndustReal",
                        canonical_root_id="ROOT-1",
                        operator_id="OP-1",
                        complete_job_id="JOB-1",
                        video_id="video",
                        project_split="DEVELOPMENT",
                        source_split="train",
                        formal_use_allowed="NO",
                        independent_unit_eligible="NO",
                        split_unit=["ROOT-1", "OP-1", "JOB-1", "video"],
                    )
                ],
            }
        ),
        encoding="utf-8",
    )
    return video, ledger, manifest


def _resolve(video, ledger, manifest):
    return resolve_experiment_identity(
        video,
        asset_id="ASSET-1",
        ledger_path=ledger,
        manifest_path=manifest,
        profile="DEVELOPMENT_ONLY",
        video_id="video",
        expected_task_id="TASK-1",
        expected_ledger_sha256=hashlib.sha256(ledger.read_bytes()).hexdigest(),
        expected_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
    )


def test_resolver_binds_hash_and_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(ei, "_decode_frames", lambda _: 1)
    video, ledger, manifest = _case(tmp_path)
    assert _resolve(video, ledger, manifest).canonical_group_id == "GROUP-1"


def test_resolver_accepts_only_registered_dataset_task_alias(tmp_path, monkeypatch):
    monkeypatch.setattr(ei, "_decode_frames", lambda _: 1)
    video, ledger, manifest = _case(tmp_path)
    ledger.write_text(
        ledger.read_text(encoding="utf-8").replace("TASK-1", "MECCANO_ASSEMBLY"),
        encoding="utf-8",
    )
    identity = resolve_experiment_identity(
        video,
        asset_id="ASSET-1",
        ledger_path=ledger,
        manifest_path=manifest,
        profile="DEVELOPMENT_ONLY",
        video_id="video",
        expected_task_id="meccano",
        expected_ledger_sha256=hashlib.sha256(ledger.read_bytes()).hexdigest(),
        expected_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
    )
    assert identity.task_id == "MECCANO_ASSEMBLY"


def test_g3_development_profile_accepts_frozen_meccano_development_split(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ei, "_decode_frames", lambda _: 1)
    video, ledger, manifest = _case(tmp_path)
    ledger.write_text(
        ledger.read_text(encoding="utf-8").replace(
            "DEVELOPMENT,train,NO,NO",
            "DEVELOPMENT,train,NO,YES",
        ),
        encoding="utf-8",
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '"independent_unit_eligible": "NO"',
            '"independent_unit_eligible": "YES"',
        ),
        encoding="utf-8",
    )
    identity = resolve_experiment_identity(
        video,
        asset_id="ASSET-1",
        ledger_path=ledger,
        manifest_path=manifest,
        profile="DEVELOPMENT_G3_EVALUATION",
        video_id="video",
        expected_task_id="TASK-1",
        expected_ledger_sha256=hashlib.sha256(ledger.read_bytes()).hexdigest(),
        expected_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
    )
    assert identity.project_split == "DEVELOPMENT"


@pytest.mark.parametrize(
    "mutator", ["hash", "duplicate", "mismatch", "unknown", "task"]
)
def test_resolver_rejects_hostile_registration(tmp_path, monkeypatch, mutator):
    monkeypatch.setattr(ei, "_decode_frames", lambda _: 1)
    video, ledger, manifest = _case(tmp_path)
    if mutator == "hash":
        ledger.write_text(
            ledger.read_text().replace(
                hashlib.sha256(video.read_bytes()).hexdigest(), "0" * 64
            )
        )
    elif mutator == "duplicate":
        ledger.write_text(
            ledger.read_text() + ledger.read_text().splitlines()[1] + "\n"
        )
    elif mutator == "mismatch":
        manifest.write_text(manifest.read_text().replace("ROOT-1", "ROOT-2"))
    elif mutator == "unknown":
        ledger.write_text(ledger.read_text().replace("OP-1", "UNKNOWN"))
    else:
        ledger.write_text(ledger.read_text().replace("TASK-1", "TASK-2"))
    with pytest.raises(ExperimentIdentityError):
        _resolve(video, ledger, manifest)


@pytest.mark.parametrize(
    "field",
    [
        "source_dataset",
        "canonical_root_id",
        "canonical_group_id",
        "operator_id",
        "complete_job_id",
        "task_id",
    ],
)
def test_resolver_rejects_explicit_identity_placeholders(tmp_path, monkeypatch, field):
    monkeypatch.setattr(ei, "_decode_frames", lambda _: 1)
    video, ledger, manifest = _case(tmp_path)
    replacements = {
        "source_dataset": "IndustReal",
        "canonical_root_id": "ROOT-1",
        "canonical_group_id": "GROUP-1",
        "operator_id": "OP-1",
        "complete_job_id": "JOB-1",
        "task_id": "TASK-1",
    }
    placeholder = (
        "SOFTWARE_FIXTURE"
        if field in {"source_dataset", "task_id"}
        else "UNASSIGNED_BLOCKED_IDENTITY"
    )
    ledger.write_text(
        ledger.read_text(encoding="utf-8").replace(replacements[field], placeholder),
        encoding="utf-8",
    )
    if field in {
        "source_dataset",
        "canonical_root_id",
        "operator_id",
        "complete_job_id",
    }:
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                replacements[field], placeholder
            ),
            encoding="utf-8",
        )
    with pytest.raises(ExperimentIdentityError):
        _resolve(video, ledger, manifest)


@pytest.mark.parametrize(
    "placeholder",
    [
        "SOFTWARE_FIXTURE",
        "UNASSIGNED_BLOCKED_IDENTITY",
        "UNKNOWN_EXTERNAL_IDENTITY",
        "FIXTURE",
        "SYNTHETIC",
        "N/A",
        "NA",
        "NULL",
        "-",
        "?",
    ],
)
def test_resolver_rejects_placeholder_video_id(tmp_path, monkeypatch, placeholder):
    monkeypatch.setattr(ei, "_decode_frames", lambda _: 1)
    video, ledger, manifest = _case(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    record = payload["records"][0]
    record["video_id"] = placeholder
    record["split_unit"] = ["ROOT-1", "OP-1", "JOB-1", placeholder]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ExperimentIdentityError):
        _resolve(video, ledger, manifest)


def test_fixture_requires_no_external_identity(tmp_path):
    video, ledger, manifest = _case(tmp_path)
    with pytest.raises(ExperimentIdentityError):
        resolve_experiment_identity(
            video,
            asset_id="ASSET-1",
            ledger_path=ledger,
            manifest_path=manifest,
            profile="SOFTWARE_FIXTURE",
        )


def test_g3_evaluation_profile_requires_exact_split_and_eligible_unit(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ei, "_decode_frames", lambda _: 1)
    video, ledger, manifest = _case(tmp_path)
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        .replace("DEVELOPMENT,train,NO,NO", "DEVELOPMENT_G3_EVALUATION,train,NO,YES"),
        encoding="utf-8",
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["records"][0]["project_split"] = "DEVELOPMENT_G3_EVALUATION"
    payload["records"][0]["independent_unit_eligible"] = "YES"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    resolved = resolve_experiment_identity(
        video,
        asset_id="ASSET-1",
        ledger_path=ledger,
        manifest_path=manifest,
        profile="DEVELOPMENT_G3_EVALUATION",
        video_id="video",
        expected_task_id="TASK-1",
        expected_ledger_sha256=hashlib.sha256(ledger.read_bytes()).hexdigest(),
        expected_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
    )
    assert resolved.project_split == "DEVELOPMENT_G3_EVALUATION"


def test_g3_evaluation_profile_rejects_unknown_operator(tmp_path, monkeypatch):
    monkeypatch.setattr(ei, "_decode_frames", lambda _: 1)
    video, ledger, manifest = _case(tmp_path)
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        .replace("OP-1", "UNKNOWN")
        .replace("DEVELOPMENT,train,NO,NO", "DEVELOPMENT_G3_EVALUATION,train,NO,YES"),
        encoding="utf-8",
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["records"][0].update(
        project_split="DEVELOPMENT_G3_EVALUATION",
        independent_unit_eligible="YES",
        operator_id="UNKNOWN",
    )
    payload["records"][0]["split_unit"][1] = "UNKNOWN"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ExperimentIdentityError):
        resolve_experiment_identity(
            video,
            asset_id="ASSET-1",
            ledger_path=ledger,
            manifest_path=manifest,
            profile="DEVELOPMENT_G3_EVALUATION",
            video_id="video",
            expected_task_id="TASK-1",
            expected_ledger_sha256=hashlib.sha256(ledger.read_bytes()).hexdigest(),
            expected_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        )


def test_ratified_industreal_complete_job_fallback_resolves_without_operator_inference(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ei, "_decode_frames", lambda _: 1)
    video, ledger, manifest = _case(tmp_path)
    with ledger.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    row = rows[0]
    row.update(
        operator_id="UNKNOWN",
        project_split="DEVELOPMENT_G3_EVALUATION",
        independent_unit_eligible="YES",
        cluster_level="complete_job",
        operator_identity_status="UNKNOWN_DISCLOSED",
        truth_capability="ACTION_STEP_STATE_ONLY",
    )
    fieldnames = list(row)
    with ledger.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["records"][0].update(
        operator_id="UNKNOWN",
        project_split="DEVELOPMENT_G3_EVALUATION",
        independent_unit_eligible="YES",
        cluster_level="complete_job",
        operator_identity_status="UNKNOWN_DISCLOSED",
        truth_capability="ACTION_STEP_STATE_ONLY",
    )
    payload["records"][0]["split_unit"][1] = "UNKNOWN"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    resolved = resolve_experiment_identity(
        video,
        asset_id="ASSET-1",
        ledger_path=ledger,
        manifest_path=manifest,
        profile="DEVELOPMENT_G3_EVALUATION",
        video_id="video",
        expected_task_id="TASK-1",
        expected_ledger_sha256=hashlib.sha256(ledger.read_bytes()).hexdigest(),
        expected_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
    )
    assert resolved.operator_id == "UNKNOWN"
    assert resolved.cluster_level == "complete_job"
    assert resolved.operator_identity_status == "UNKNOWN_DISCLOSED"


def test_resolver_rejects_populated_tombstoned_meccano_0009_alias(tmp_path, monkeypatch):
    monkeypatch.setattr(ei, "_decode_frames", lambda _: 1)
    video, ledger, manifest = _case(tmp_path)
    ledger.write_text(ledger.read_text(encoding="utf-8").replace("IndustReal", "MECCANO").replace("ASSET-1", "MECCANO-09"), encoding="utf-8")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["records"][0].update({"asset_id": "MECCANO-09", "source_dataset": "MECCANO", "video_id": "video09", "split_unit": ["ROOT-1", "OP-1", "JOB-1", "video09"]})
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ExperimentIdentityError, match="TOMBSTONE-MECCANO-0009"):
        resolve_experiment_identity(
            video, asset_id="MECCANO-09", ledger_path=ledger, manifest_path=manifest,
            profile="DEVELOPMENT_ONLY", video_id="video09", expected_task_id="TASK-1",
            expected_ledger_sha256=hashlib.sha256(ledger.read_bytes()).hexdigest(),
            expected_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        )
