from __future__ import annotations

import argparse
import json
from pathlib import Path

from display_project.data.meccano_yolo_audit import (
    audit_meccano_yolo_dataset,
    build_meccano_candidate_manifest,
    validate_meccano_annotation_sources,
    write_json,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit a MECCANO YOLO development candidate"
    )
    parser.add_argument(
        "dataset_root",
        nargs="?",
        default="data/yolo/meccano-small-objects",
    )
    parser.add_argument(
        "--identity-manifest",
        default="research/split-manifest.json",
    )
    parser.add_argument("--conversion-report")
    parser.add_argument("--qa-output")
    parser.add_argument("--file-manifest-output")
    parser.add_argument(
        "--candidate-manifest-output",
        default="research/audits/meccano-yolo-candidate-split-manifest.json",
    )
    parser.add_argument("--held-out-video", action="append")
    parser.add_argument(
        "--candidate-only",
        action="store_true",
        help="rebind the existing PASS QA report to an updated identity manifest",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    dataset_root = Path(args.dataset_root)
    held_out_videos = args.held_out_video or ["09"]
    conversion_report = (
        Path(args.conversion_report)
        if args.conversion_report
        else dataset_root / "dataset-audit.json"
    )
    qa_output = (
        Path(args.qa_output) if args.qa_output else dataset_root / "dataset-qa.json"
    )
    file_manifest_output = (
        Path(args.file_manifest_output)
        if args.file_manifest_output
        else dataset_root / "file-manifest.jsonl"
    )
    if args.candidate_only:
        qa_report = json.loads(qa_output.read_text(encoding="utf-8"))
        source_summary, source_errors = validate_meccano_annotation_sources(
            qa_report.get("annotation_source_provenance"),
            splits=qa_report.get("videos", {}),
            held_out_videos=held_out_videos,
        )
        if not (
            qa_report.get("status") == "PASS"
            and qa_report.get("passed") is True
            and qa_report.get("annotation_source_provenance", {}).get("status")
            == "PASS"
            and qa_report.get("conversion_crosscheck", {}).get("status") == "PASS"
            and source_summary.get("status") == "PASS"
            and source_errors == []
        ):
            raise ValueError("candidate-only mode requires a complete PASS QA report")
    else:
        qa_report = audit_meccano_yolo_dataset(
            dataset_root,
            held_out_videos=held_out_videos,
            file_manifest_path=file_manifest_output,
            conversion_report_path=conversion_report,
        )
    candidate_manifest = build_meccano_candidate_manifest(
        qa_report,
        args.identity_manifest,
        held_out_videos=held_out_videos,
    )
    if not args.candidate_only:
        write_json(qa_output, qa_report)
    write_json(args.candidate_manifest_output, candidate_manifest)
    print(
        json.dumps(
            {
                "dataset_qa": qa_report["status"],
                "candidate_status": candidate_manifest["status"],
                "images": qa_report["images"],
                "box_total": qa_report["box_total"],
                "content_sha256": qa_report["content_sha256"],
                "held_out_test_assets": len(qa_report["held_out_test_assets"]),
            },
            ensure_ascii=False,
        )
    )
    return (
        0
        if qa_report["passed"] and candidate_manifest["status"] != "FAIL_SPLIT_LEAKAGE"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
