"""CLI for fail-closed, manifest-registered paired experiments."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from display_project.data.experiment_identity import (
    ExperimentIdentityError,
    resolve_experiment_identity,
)

from display_project.experiments import METHOD_NAMES, PairedExperimentRunner
from display_project.experiments.adapter_manifest import (
    canonical_sha256,
    load_adapter_manifest,
)
from display_project.gate_audit import verify_joint_gate_permission


def _write_report(path: str | Path, report: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        (
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    )


def _invalid_report(error: Exception) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "profile": "UNKNOWN",
        "status": "BLOCKED",
        "blocked": True,
        "error_code": "MANIFEST_INVALID",
        "error": f"{type(error).__name__}: {error}",
        "frozen_anchors": {},
        "methods": [],
    }


def _admission_metadata(path: str | Path) -> dict[str, Any]:
    """Expose only the verified batch result ledger identity to the runner."""
    try:
        receipt_payload = json.loads(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except (OSError, json.JSONDecodeError):
        return {}
    destination = (
        receipt_payload.get("result_destination")
        if isinstance(receipt_payload, Mapping)
        else None
    )
    if not isinstance(destination, Mapping):
        return {}
    return {"paired_ledger_id": destination.get("ledger_id")}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", nargs="?")
    parser.add_argument("--adapter-manifest")
    parser.add_argument("--ledger")
    parser.add_argument("--dataset-ledger")
    parser.add_argument("--split-manifest")
    parser.add_argument("--asset-id")
    parser.add_argument("--expected-dataset-ledger-sha256")
    parser.add_argument("--expected-split-manifest-sha256")
    parser.add_argument("--video-id")
    parser.add_argument("--task-id")
    parser.add_argument("--operator-id", default="UNKNOWN")
    parser.add_argument("--complete-job-id", default="UNKNOWN")
    parser.add_argument("--expected-frames", type=int)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--retries", type=int, default=0)
    parser.add_argument("--list-methods", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--preflight-report")
    parser.add_argument(
        "--joint-gate",
        default="research/audits/g1-g2-joint-gate.json",
    )
    parser.add_argument("--capability")
    parser.add_argument("--admission-receipt")
    parser.add_argument("--unit-token")
    parser.add_argument("--manual-intervention", action="store_true")
    args = parser.parse_args(argv)
    if args.list_methods:
        print("\n".join(METHOD_NAMES))
        return
    if not args.adapter_manifest:
        parser.error("--adapter-manifest is required unless --list-methods is used")
    report_path = args.preflight_report or f"{args.adapter_manifest}.preflight.json"
    try:
        compiled = load_adapter_manifest(args.adapter_manifest)
    except Exception as error:
        report = _invalid_report(error)
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        raise SystemExit(2) from error
    report = compiled.report()
    _write_report(report_path, report)
    if args.task_id is not None and args.task_id != compiled.task.task_id:
        report = {
            **report,
            "status": "BLOCKED",
            "blocked": True,
            "error_code": "TASK_ID_MISMATCH",
            "error": (
                f"--task-id={args.task_id} does not match "
                f"manifest task_id={compiled.task.task_id}"
            ),
        }
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        raise SystemExit(2)
    if compiled.blocked:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        raise SystemExit(2)
    if args.preflight_only:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return
    is_g3_evaluation = compiled.profile == "DEVELOPMENT_G3_EVALUATION"
    if compiled.profile != "SOFTWARE_FIXTURE" and not is_g3_evaluation:
        try:
            verify_joint_gate_permission(
                args.joint_gate,
                permission="execute_g3_method_panel_allowed",
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            report = {
                **report,
                "status": "BLOCKED",
                "blocked": True,
                "error_code": "JOINT_G1_G2_GATE_BLOCKED",
                "error": f"{type(error).__name__}: {error}",
            }
            _write_report(report_path, report)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            raise SystemExit(2) from error
    if not args.video or not args.ledger:
        parser.error(
            "video and --ledger are required unless --preflight-only or --list-methods is used"
        )
    identity = None
    capability = None
    admission_metadata: dict[str, Any] = {}
    if compiled.profile != "SOFTWARE_FIXTURE":
        if is_g3_evaluation:
            if not args.admission_receipt or not args.unit_token:
                parser.error(
                    "--admission-receipt and --unit-token are required for DEVELOPMENT_G3_EVALUATION"
                )
            admission_metadata = _admission_metadata(args.admission_receipt)
        elif not args.capability:
            parser.error("--capability is required for non-fixture profiles")
        capability = None if is_g3_evaluation else args.capability
        if not args.dataset_ledger or not args.split_manifest or not args.asset_id:
            parser.error(
                "--dataset-ledger, --split-manifest and --asset-id are required for non-fixture profiles"
            )
        try:
            identity = resolve_experiment_identity(
                args.video,
                asset_id=args.asset_id,
                ledger_path=args.dataset_ledger,
                manifest_path=args.split_manifest,
                profile=compiled.profile,
                video_id=args.video_id,
                expected_task_id=compiled.task.task_id,
                expected_ledger_sha256=args.expected_dataset_ledger_sha256,
                expected_manifest_sha256=args.expected_split_manifest_sha256,
            )
        except ExperimentIdentityError as error:
            report = {
                **report,
                "status": "BLOCKED",
                "blocked": True,
                "error_code": "DATA_IDENTITY_INVALID",
                "error": f"{type(error).__name__}: {error}",
            }
            _write_report(report_path, report)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            raise SystemExit(2) from error
    specs = {spec.name: spec for spec in compiled.method_specs}
    missing = set(compiled.schedule) - set(specs)
    if missing:
        report = {
            **report,
            "status": "BLOCKED",
            "blocked": True,
            "error_code": "SCHEDULE_ADAPTER_MISSING",
            "error": f"missing adapters: {sorted(missing)}",
        }
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        raise SystemExit(2)
    ordered_specs = tuple(specs[name] for name in compiled.schedule)
    run = PairedExperimentRunner(
        ordered_specs,
        timeout_s=args.timeout,
        ledger_path=args.ledger,
        retries=args.retries,
        frozen_schedule=compiled.schedule,
        schedule_id=canonical_sha256(compiled.schedule),
        metadata={
            **compiled.anchors,
            "manifest_hash": compiled.manifest_hash,
            "task_config_hash": compiled.task_config_hash,
            "task_source_hash": compiled.task_source_hash,
            "experiment_profile": compiled.profile,
            **admission_metadata,
            **(
                {
                    "asset_id": identity.asset_id,
                    "source_dataset": identity.source_dataset,
                    "canonical_root_id": identity.canonical_root_id,
                    "canonical_group_id": identity.canonical_group_id,
                    "registered_project_split": identity.project_split,
                    "formal_use_allowed": identity.formal_use_allowed,
                    "registered_video_hash": identity.ledger_hash,
                    "dataset_ledger_sha256": identity.ledger_file_hash,
                    "split_manifest_sha256": identity.manifest_file_hash,
                    "registered_source_split": identity.source_split,
                    "cluster_level": identity.cluster_level,
                }
                if identity
                else {}
            ),
        },
        capability=capability,
        admission_receipt=args.admission_receipt,
    ).run_video(
        args.video,
        video_id=identity.video_id if identity else args.video_id,
        expected_frames=args.expected_frames,
        task_id=args.task_id or compiled.task.task_id,
        operator_id=identity.operator_id if identity else args.operator_id,
        complete_job_id=identity.complete_job_id if identity else args.complete_job_id,
        source_dataset=identity.source_dataset if identity else "UNKNOWN",
        canonical_root_id=identity.canonical_root_id if identity else "UNKNOWN",
        canonical_group_id=identity.canonical_group_id if identity else "UNKNOWN",
        manual_intervention=args.manual_intervention,
        unit_token=args.unit_token,
    )
    print(
        f"registered={len(run.attempts)} video_hash={run.video_hash} expected_frames={run.expected_frames} manifest_hash={compiled.manifest_hash}"
    )


if __name__ == "__main__":
    main()
