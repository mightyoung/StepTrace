from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from display_project.evidence import verify_certificate
from display_project.fusion.event_schema import validate_event
from display_project.provenance import load_sidecar_record


RUNS = (
    (
        "full-meccano-0016-yolo-fixed-v1",
        "fixed_streak",
        "/srv/project/datasets/MECCANO/_codex_candidate_rgb_videos/MECCANO_RGB_Videos/0016.mp4",
    ),
    (
        "full-meccano-0016-yolo-proposed-v1",
        "proposed",
        "/srv/project/datasets/MECCANO/_codex_candidate_rgb_videos/MECCANO_RGB_Videos/0016.mp4",
    ),
    (
        "full-meccano-0016-yolo-uncalibrated-v1",
        "uncalibrated_certificate",
        "/srv/project/datasets/MECCANO/_codex_candidate_rgb_videos/MECCANO_RGB_Videos/0016.mp4",
    ),
    (
        "full-meccano-0016-dino-fixed-v1",
        "fixed_streak",
        "/srv/project/datasets/MECCANO/_codex_candidate_rgb_videos/MECCANO_RGB_Videos/0016.mp4",
    ),
    (
        "full-meccano-0016-dino-proposed-v1",
        "proposed",
        "/srv/project/datasets/MECCANO/_codex_candidate_rgb_videos/MECCANO_RGB_Videos/0016.mp4",
    ),
    (
        "full-industreal-01-dino-fixed-v1",
        "fixed_streak",
        "/srv/project/datasets/IndustReal/official/rgb_videos/01_assy_0_1.mp4",
    ),
    (
        "full-industreal-01-dino-proposed-v1",
        "proposed",
        "/srv/project/datasets/IndustReal/official/rgb_videos/01_assy_0_1.mp4",
    ),
)


def load_design(path: Path) -> dict[str, Any]:
    design = json.loads(path.read_text(encoding="utf-8"))
    required_false = (
        "four_outputs_are_independent_replicates",
        "inferential_comparison_allowed",
        "formal_evidence",
        "g3_evidence",
        "pilot_evidence",
        "formal_test_evidence",
        "submission_evidence",
        "annotation_used_required",
    )
    if design.get("scope") != "DEVELOPMENT_ONLY_REAL_DATA":
        raise ValueError("design scope must be DEVELOPMENT_ONLY_REAL_DATA")
    if any(design.get(field) is not False for field in required_false):
        raise ValueError("four-video design must preserve all development-only boundaries")
    runs = design.get("runs")
    if not isinstance(runs, list) or len(runs) != 4:
        raise ValueError("four-video design must contain exactly four runs")
    run_ids = [run.get("experiment_id") for run in runs]
    if any(not isinstance(run_id, str) or not run_id for run_id in run_ids):
        raise ValueError("every design run must have an experiment_id")
    if len(set(run_ids)) != 4:
        raise ValueError("four-video design run ids must be unique")
    sources = {run.get("source") for run in runs}
    if None in sources:
        raise ValueError("every design run must have a source")
    if design.get("output_artifact_count") != 4:
        raise ValueError("output_artifact_count must be four")
    if design.get("distinct_source_recording_count") != len(sources):
        raise ValueError("distinct_source_recording_count does not match runs")
    if design.get("independent_unit") != "complete_source_recording":
        raise ValueError("independent_unit must be complete_source_recording")
    if design.get("independent_unit_count") != len(sources):
        raise ValueError("independent_unit_count does not match distinct sources")
    return design


def validate_selected_lifecycles(
    ledger: list[dict[str, Any]], expected_ids: set[str]
) -> None:
    lifecycle: dict[str, list[str]] = {run_id: [] for run_id in expected_ids}
    exits: dict[str, int] = {}
    for row in ledger:
        experiment_id = row.get("experiment_id")
        if experiment_id not in expected_ids:
            continue
        if row.get("scope") != "DEVELOPMENT_ONLY_REAL_DATA":
            raise RuntimeError(f"invalid ledger scope for {experiment_id}")
        event = row.get("event")
        if event not in {"START", "EXIT"}:
            raise RuntimeError(f"unexpected ledger event for {experiment_id}: {event}")
        lifecycle[experiment_id].append(event)
        if event == "EXIT":
            exits[experiment_id] = row.get("exit_code")
    incomplete = {
        run_id: events
        for run_id, events in lifecycle.items()
        if events != ["START", "EXIT"]
    }
    if incomplete:
        raise RuntimeError(f"invalid selected lifecycle: {incomplete}")
    if set(exits) != expected_ids or any(code != 0 for code in exits.values()):
        raise RuntimeError(f"selected exits are incomplete or failed: {exits}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def video_info(path: Path) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {path}")
    result = {
        "frames": int(capture.get(cv2.CAP_PROP_FRAME_COUNT)),
        "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": float(capture.get(cv2.CAP_PROP_FPS)),
    }
    capture.release()
    return result


def sample_frame(path: Path, index: int) -> np.ndarray:
    capture = cv2.VideoCapture(str(path))
    capture.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, frame = capture.read()
    capture.release()
    if not ok:
        raise RuntimeError(f"cannot decode frame {index} from {path}")
    return frame


def parse_summary(log_path: Path) -> dict[str, Any]:
    for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError(f"missing JSON summary in {log_path}")


def validate_run(
    root: Path,
    run_id: str,
    policy: str,
    source_path: Path,
    source_info: dict[str, Any],
) -> dict[str, Any]:
    output = root / "outputs" / f"{run_id}.mp4"
    events_path = root / "outputs" / f"{run_id}.events.jsonl"
    certificate_path = (
        root
        / "outputs"
        / f"{run_id}.events.certificates"
        / "records.jsonl"
    )
    log_path = root / "logs" / f"{run_id}.container.log"
    for path in (output, events_path, certificate_path, log_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    output_info = video_info(output)
    if output_info != source_info:
        raise ValueError(
            f"video metadata mismatch for {run_id}: {output_info} != {source_info}"
        )
    frame_indices = (0, output_info["frames"] // 2, output_info["frames"] - 1)
    source_samples = [sample_frame(source_path, index) for index in frame_indices]
    output_samples = [sample_frame(output, index) for index in frame_indices]
    strong_delta_pixels = [
        int((cv2.absdiff(source, rendered).max(axis=2) > 24).sum())
        for source, rendered in zip(source_samples, output_samples)
    ]
    if not all(value > 1_000 for value in strong_delta_pixels):
        raise ValueError(f"visual overlay not proven for {run_id}")

    events: list[dict[str, Any]] = []
    with events_path.open("r", encoding="utf-8") as stream:
        for expected_frame, line in enumerate(stream):
            event = json.loads(line)
            validate_event(event)
            if event.get("frame") != expected_frame:
                raise ValueError(f"non-sequential event frame in {run_id}")
            events.append(event)
    if len(events) != source_info["frames"]:
        raise ValueError(f"event count mismatch for {run_id}")

    certificates = []
    with certificate_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            certificate, graph = load_sidecar_record(record)
            result = verify_certificate(
                certificate,
                graph,
                context=certificate.verification_context,
                task_id=certificate.task_id,
                expected_step=certificate.expected_step,
                current_step=certificate.current_step,
            )
            if not result.valid:
                raise ValueError(
                    f"invalid certificate in {run_id}: {result.reason}"
                )
            certificates.append(certificate)
    event_certificate_hashes = {
        event["certificate_hash"]
        for event in events
        if event.get("certificate_hash") is not None
    }
    sidecar_certificate_hashes = {
        certificate.certificate_hash for certificate in certificates
    }
    if event_certificate_hashes != sidecar_certificate_hashes:
        raise ValueError(f"event/sidecar certificate mismatch for {run_id}")
    if policy == "fixed_streak" and certificates:
        raise ValueError(f"fixed_streak emitted certificates in {run_id}")

    summary = parse_summary(log_path)
    if summary.get("annotation_used") is not False:
        raise ValueError(f"annotation_used is not false for {run_id}")
    if summary.get("frames") != source_info["frames"]:
        raise ValueError(f"summary frame count mismatch for {run_id}")
    owned_by_runner = all(
        path.stat().st_uid == os.getuid()
        for path in (output, events_path, certificate_path, log_path)
    )
    if not owned_by_runner:
        raise ValueError(f"unexpected file ownership for {run_id}")

    return {
        "experiment_id": run_id,
        "decision_policy": policy,
        "source": str(source_path),
        "video": str(output),
        "events": str(events_path),
        "certificates": str(certificate_path),
        "video_info": output_info,
        "video_bytes": output.stat().st_size,
        "video_sha256": sha256_file(output),
        "events_sha256": sha256_file(events_path),
        "certificates_sha256": sha256_file(certificate_path),
        "event_count": len(events),
        "certificate_count": len(certificates),
        "strong_delta_pixels": strong_delta_pixels,
        "annotation_used": False,
        "owned_by_runner": owned_by_runner,
        "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--run-id",
        choices=tuple(run_id for run_id, _, _ in RUNS),
        help="validate one completed run without requiring the batch completion marker",
    )
    parser.add_argument(
        "--design",
        type=Path,
        help="validate the completed runs selected by a development-only design",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if args.run_id and args.design:
        parser.error("--run-id and --design are mutually exclusive")
    if args.run_id:
        run_id, policy, source = next(
            run for run in RUNS if run[0] == args.run_id
        )
        source_path = Path(source)
        report = {
            "schema_version": 1,
            "scope": "DEVELOPMENT_ONLY_REAL_DATA",
            "formal_evidence": False,
            "g3_evidence": False,
            "independent_unit": "complete_recording",
            "run": validate_run(
                root, run_id, policy, source_path, video_info(source_path)
            ),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False))
        return 0

    if args.design:
        design = load_design(args.design.resolve())
        ledger_path = root / "logs" / "development-real-data-full-v1.ledger.jsonl"
        ledger = [
            json.loads(line)
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        selected_runs = design["runs"]
        expected_ids = {run["experiment_id"] for run in selected_runs}
        validate_selected_lifecycles(ledger, expected_ids)
        source_info_cache: dict[str, dict[str, Any]] = {}
        results = []
        for run in selected_runs:
            source = run["source"]
            source_path = Path(source)
            source_info = source_info_cache.setdefault(
                source, video_info(source_path)
            )
            results.append(
                validate_run(
                    root,
                    run["experiment_id"],
                    run["decision_policy"],
                    source_path,
                    source_info,
                )
            )
        report = {
            "schema_version": 1,
            "design_id": design["design_id"],
            "scope": "DEVELOPMENT_ONLY_REAL_DATA",
            "purpose": design["purpose"],
            "formal_evidence": False,
            "g3_evidence": False,
            "pilot_evidence": False,
            "formal_test_evidence": False,
            "submission_evidence": False,
            "output_artifact_count": 4,
            "distinct_source_recording_count": design[
                "distinct_source_recording_count"
            ],
            "independent_unit": design["independent_unit"],
            "independent_unit_count": design["independent_unit_count"],
            "four_outputs_are_independent_replicates": False,
            "balanced_factorial": design["balanced_factorial"],
            "inferential_comparison_allowed": False,
            "design_limitations": design["design_limitations"],
            "design_sha256": sha256_file(args.design.resolve()),
            "ledger_sha256": sha256_file(ledger_path),
            "sources": [
                {
                    "path": source,
                    "sha256": sha256_file(Path(source)),
                    "video_info": source_info_cache[source],
                }
                for source in sorted(source_info_cache)
            ],
            "all_selected_exit_zero": True,
            "all_selected_lifecycles_complete": True,
            "all_annotation_used_false": True,
            "all_videos_match_complete_sources": True,
            "all_events_valid": True,
            "all_certificates_valid": True,
            "all_visual_overlays_proven": True,
            "runs": results,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False))
        return 0

    completion = root / "logs" / "development-real-data-full-v1.complete"
    if not completion.is_file():
        raise RuntimeError("batch completion marker is missing")

    ledger_path = root / "logs" / "development-real-data-full-v1.ledger.jsonl"
    ledger = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    expected_ids = {run_id for run_id, _, _ in RUNS}
    lifecycle: dict[str, list[str]] = {run_id: [] for run_id in expected_ids}
    for row in ledger:
        experiment_id = row.get("experiment_id")
        if experiment_id not in expected_ids:
            raise RuntimeError(f"unexpected experiment in ledger: {experiment_id}")
        if row.get("scope") != "DEVELOPMENT_ONLY_REAL_DATA":
            raise RuntimeError(f"invalid ledger scope for {experiment_id}")
        event = row.get("event")
        if event not in {"START", "EXIT"}:
            raise RuntimeError(f"unexpected ledger event for {experiment_id}: {event}")
        lifecycle[experiment_id].append(event)
    incomplete_lifecycle = {
        run_id: events
        for run_id, events in lifecycle.items()
        if events != ["START", "EXIT"]
    }
    if incomplete_lifecycle:
        raise RuntimeError(f"invalid batch lifecycle: {incomplete_lifecycle}")
    exits = {
        row["experiment_id"]: row["exit_code"]
        for row in ledger
        if row.get("event") == "EXIT"
    }
    if set(exits) != expected_ids or any(code != 0 for code in exits.values()):
        raise RuntimeError(f"batch exits are incomplete or failed: {exits}")

    source_info_cache: dict[str, dict[str, Any]] = {}
    results = []
    for run_id, policy, source in RUNS:
        source_path = Path(source)
        source_info = source_info_cache.setdefault(source, video_info(source_path))
        results.append(
            validate_run(root, run_id, policy, source_path, source_info)
        )

    report = {
        "schema_version": 1,
        "scope": "DEVELOPMENT_ONLY_REAL_DATA",
        "formal_evidence": False,
        "g3_evidence": False,
        "independent_unit": "complete_recording",
        "batch_completion": completion.read_text(encoding="utf-8").strip(),
        "ledger_sha256": sha256_file(ledger_path),
        "all_exit_zero": True,
        "all_lifecycles_complete": True,
        "all_annotation_used_false": True,
        "all_videos_match_complete_sources": True,
        "all_events_valid": True,
        "all_certificates_valid": True,
        "all_visual_overlays_proven": True,
        "runs": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
