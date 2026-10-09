from __future__ import annotations

import json
import importlib.util
import csv
import hashlib
from pathlib import Path

from display_project.g3_admission_bundle import build_g3_admission_bundle
from display_project.g3_run_admission import validate_admission_input_bundle


ROOT = Path(__file__).parents[1]


def _spec(tmp_path: Path) -> Path:
    shared = tmp_path / "shared.bin"
    shared.write_bytes(b"frozen-source")
    split = tmp_path / "split.json"
    split_records = []
    ledger = tmp_path / "dataset-ledger.csv"
    ledger_rows = []
    result = tmp_path / "results" / "paired.jsonl"
    units = []
    for task, dataset in (("meccano", "MECCANO"), ("industreal", "INDUSTREAL")):
        video = tmp_path / f"{task}.mp4"
        video.write_bytes(f"video:{task}".encode())
        units.append(
            {
                "task_id": task,
                "dataset_id": dataset,
                "operator_id": f"OP-{dataset}-01",
                "complete_job_id": f"JOB-{dataset}-01",
                "canonical_root_id": f"ROOT-{dataset}-01",
                "canonical_group_id": f"GROUP-{dataset}-01",
                "video_id": f"{task}-001",
                "video": str(video),
                "split_manifest": str(split),
                "dataset_ledger": str(ledger),
            }
        )
        split_records.append(
            {
                "video_id": f"{task}-001",
                "source_dataset": dataset,
                "project_split": "DEVELOPMENT",
                "canonical_root_id": f"ROOT-{dataset}-01",
                "canonical_group_id": f"GROUP-{dataset}-01",
                "operator_id": f"OP-{dataset}-01",
                "complete_job_id": f"JOB-{dataset}-01",
                "independent_unit_eligible": "YES",
                "tombstone_id": "NONE",
            }
        )
        ledger_rows.append(
            {
                "record_type": "CANONICAL_ROOT_VIDEO",
                "source_dataset": dataset,
                "canonical_root_id": f"ROOT-{dataset}-01",
                "canonical_group_id": f"GROUP-{dataset}-01",
                "operator_id": f"OP-{dataset}-01",
                "complete_job_id": f"JOB-{dataset}-01",
                "split_locked": "YES",
                "independent_unit_eligible": "YES",
                "tombstone_id": "NONE",
                "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
            }
        )
    split.write_text(json.dumps({"records": split_records}), encoding="utf-8")
    with ledger.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(ledger_rows[0]))
        writer.writeheader()
        writer.writerows(ledger_rows)
    value = {
        "schema_version": "display_project.g3_admission_bundle_spec-1.0.0",
        "profile": "DEVELOPMENT_G3_EVALUATION",
        "randomization_seed": 20260820,
        "tasks": {
            "meccano": {
                "dataset_id": "MECCANO",
                "config": str(ROOT / "configs/tasks/meccano.yaml"),
            },
            "industreal": {
                "dataset_id": "INDUSTREAL",
                "config": str(ROOT / "configs/tasks/industreal.yaml"),
            },
        },
        "method_sources": {
            "fixed_streak": [str(shared)],
            "hmm_or_crf": [str(shared)],
            "proposed": [str(shared)],
        },
        "units": units,
        "statistical_producer": {
            "producer_id": "stats:g3-primary",
            "producer_version": "1.0.0",
            "runtime": "R",
            "real_input_enabled": True,
            "sources": [str(shared)],
            "runtime_assets": {"aft_dll": str(shared)},
            "environment_lock": str(shared),
            "bootstrap_count": 10000,
        },
        "result_destination": {
            "ledger_id": "ledger:g3-paired-results",
            "ledger": str(result),
        },
    }
    path = tmp_path / "bundle-spec.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_real_path_backed_spec_builds_five_valid_non_authorizing_inputs(tmp_path: Path):
    report = build_g3_admission_bundle(_spec(tmp_path))
    assert report["ready"] is True
    assert set(report["artifacts"]) == {
        "task_bindings",
        "method_manifest",
        "unit_inventory",
        "statistical_producer",
        "result_destination",
    }
    valid, reasons = validate_admission_input_bundle(**report["artifacts"])
    assert valid, reasons
    assert len(report["artifacts"]["unit_inventory"]) == 2


def test_missing_real_source_fails_closed_without_publishable_artifacts(tmp_path: Path):
    spec_path = _spec(tmp_path)
    spec = json.loads(spec_path.read_text())
    spec["units"][0]["video"] = str(tmp_path / "missing.mp4")
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    report = build_g3_admission_bundle(spec_path)
    assert report["ready"] is False
    assert report["artifacts"] == {}
    assert any(reason.startswith("UNIT_SOURCE_NOT_FILE:0") for reason in report["reason_codes"])


def test_meccano_0009_alias_is_rejected_by_bundle_validator(tmp_path: Path):
    spec_path = _spec(tmp_path)
    spec = json.loads(spec_path.read_text())
    spec["units"][0]["video_id"] = "MECCANO-0009"
    split_path = Path(spec["units"][0]["split_manifest"])
    split = json.loads(split_path.read_text())
    split["records"][0]["video_id"] = "MECCANO-0009"
    split_path.write_text(json.dumps(split), encoding="utf-8")
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    report = build_g3_admission_bundle(spec_path)
    assert report["ready"] is False
    assert "TOMBSTONE_MECCANO_0009_FORBIDDEN" in report["reason_codes"]


def test_cli_publishes_all_artifacts_only_after_ready(tmp_path: Path):
    module_path = ROOT / "scripts/build_g3_admission_bundle.py"
    module_spec = importlib.util.spec_from_file_location("build_g3_bundle", module_path)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)
    output = tmp_path / "bundle"
    assert module.main(["--spec", str(_spec(tmp_path)), "--output-dir", str(output)]) == 0
    assert {path.name for path in output.iterdir()} == {
        "bundle-report.json",
        "task-bindings.json",
        "method-manifest.json",
        "unit-inventory.json",
        "statistical-producer.json",
        "result-destination.json",
    }


def test_unassigned_split_and_unknown_operator_cannot_enter_bundle(tmp_path: Path):
    spec_path = _spec(tmp_path)
    spec = json.loads(spec_path.read_text())
    spec["units"][1]["operator_id"] = "UNKNOWN"
    split_path = Path(spec["units"][1]["split_manifest"])
    split = json.loads(split_path.read_text())
    split["records"][1].update(
        operator_id="UNKNOWN",
        project_split="UNASSIGNED_BLOCKED_IDENTITY",
        independent_unit_eligible="NO",
    )
    split_path.write_text(json.dumps(split), encoding="utf-8")
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    report = build_g3_admission_bundle(spec_path)
    assert report["ready"] is False
    assert "UNIT_SPLIT_NOT_ELIGIBLE:1" in report["reason_codes"]


def _spec_with_complete_job_fallback(tmp_path: Path) -> Path:
    spec_path = _spec(tmp_path)
    spec = json.loads(spec_path.read_text())
    first = spec["units"][1]
    first.update(
        operator_id="UNKNOWN",
        cluster_level="complete_job",
        operator_identity_status="UNKNOWN_DISCLOSED",
    )
    second_video = tmp_path / "industreal-second.mp4"
    second_video.write_bytes(b"video:industreal:second")
    second = dict(first)
    second.update(
        complete_job_id="JOB-INDUSTREAL-02",
        canonical_root_id="ROOT-INDUSTREAL-02",
        canonical_group_id="GROUP-INDUSTREAL-02",
        video_id="industreal-002",
        video=str(second_video),
    )
    spec["units"].append(second)

    split_path = Path(first["split_manifest"])
    split = json.loads(split_path.read_text())
    split["records"][1]["operator_id"] = "UNKNOWN"
    split["records"][1].update(
        cluster_level="complete_job",
        operator_identity_status="UNKNOWN_DISCLOSED",
        truth_capability="ACTION_STEP_STATE_ONLY",
    )
    second_split = dict(split["records"][1])
    second_split.update(
        complete_job_id=second["complete_job_id"],
        canonical_root_id=second["canonical_root_id"],
        canonical_group_id=second["canonical_group_id"],
        video_id=second["video_id"],
    )
    split["records"].append(second_split)
    split_path.write_text(json.dumps(split), encoding="utf-8")

    ledger_path = Path(first["dataset_ledger"])
    with ledger_path.open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    rows[1]["operator_id"] = "UNKNOWN"
    rows[1].update(
        cluster_level="complete_job",
        operator_identity_status="UNKNOWN_DISCLOSED",
        truth_capability="ACTION_STEP_STATE_ONLY",
    )
    second_ledger = dict(rows[1])
    second_ledger.update(
        complete_job_id=second["complete_job_id"],
        canonical_root_id=second["canonical_root_id"],
        canonical_group_id=second["canonical_group_id"],
        sha256=hashlib.sha256(second_video.read_bytes()).hexdigest(),
    )
    rows.append(second_ledger)
    with ledger_path.open("w", encoding="utf-8", newline="") as stream:
        fieldnames = list(rows[0])
        for field in ("cluster_level", "operator_identity_status", "truth_capability"):
            if field not in fieldnames:
                fieldnames.append(field)
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return spec_path


def test_bundle_builds_explicit_two_job_industreal_fallback(tmp_path: Path):
    report = build_g3_admission_bundle(_spec_with_complete_job_fallback(tmp_path))
    assert report["ready"] is True, report["reason_codes"]
    industreal = [
        row for row in report["artifacts"]["unit_inventory"]
        if row["task_id"] == "industreal"
    ]
    assert len(industreal) == 2
    assert {row["cluster_level"] for row in industreal} == {"complete_job"}
    assert {row["operator_identity_status"] for row in industreal} == {
        "UNKNOWN_DISCLOSED"
    }


def test_video_must_match_registered_dataset_ledger_hash(tmp_path: Path):
    spec_path = _spec(tmp_path)
    spec = json.loads(spec_path.read_text())
    ledger_path = Path(spec["units"][0]["dataset_ledger"])
    with ledger_path.open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    rows[0]["sha256"] = "0" * 64
    with ledger_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    report = build_g3_admission_bundle(spec_path)
    assert report["ready"] is False
    assert "UNIT_LEDGER_VIDEO_HASH_MISMATCH:0" in report["reason_codes"]


def test_fixture_only_statistical_producer_cannot_be_declared_real(tmp_path: Path):
    spec_path = _spec(tmp_path)
    spec = json.loads(spec_path.read_text())
    spec["statistical_producer"]["real_input_enabled"] = False
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    report = build_g3_admission_bundle(spec_path)
    assert report["ready"] is False
    assert "STATISTICAL_PRODUCER_REAL_INPUT_DISABLED" in report["reason_codes"]
