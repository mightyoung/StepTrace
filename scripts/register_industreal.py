import argparse
import hashlib
import json
from pathlib import Path
from display_project.data.industreal import audit_industreal_records
from display_project.data.industreal_errors import (
    EXPECTED_LABELS_SHA256,
    EXPECTED_MD5,
    build_error_extension_registry,
    validate_error_extension,
)


def _canonical_hash(payload: dict) -> str:
    canonical_payload = dict(payload)
    canonical_payload["canonical_sha256"] = ""
    canonical = (
        json.dumps(canonical_payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write(payload: dict, path: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    print(text, end="")
    if path:
        Path(path).write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("records", nargs="?")
    p.add_argument("--split-csv")
    p.add_argument("--inventory")
    p.add_argument("--expected-videos", type=int, default=84)
    p.add_argument("--expected-operators", type=int, default=27)
    p.add_argument("--output")
    p.add_argument("--error-extension-tar")
    p.add_argument("--error-extension-labels")
    p.add_argument("--asd-results")
    a = p.parse_args()
    if a.error_extension_tar or a.error_extension_labels:
        if not (a.error_extension_tar and a.error_extension_labels):
            raise SystemExit(
                "--error-extension-tar and --error-extension-labels are required together"
            )
        report = validate_error_extension(
            a.error_extension_tar,
            a.error_extension_labels,
            expected_md5=EXPECTED_MD5,
            expected_labels_sha256=EXPECTED_LABELS_SHA256,
        )
        registry = build_error_extension_registry(report, asd_zip=a.asd_results)
        registry["cli_status"] = (
            "BLOCKED_EXTENSION_AVAILABLE_RGB_VIDEO_OPERATOR_JOB_AND_PSR_BLOCKED"
            if report["status"] == "PASS"
            else "BLOCKED_ERROR_EXTENSION_VALIDATION"
        )
        registry["canonical_sha256"] = _canonical_hash(registry)
        _write(registry, a.output)
        raise SystemExit(0 if report["status"] == "PASS" else 2)
    if not a.records:
        raise SystemExit(
            "records is required unless --error-extension-tar/--error-extension-labels are supplied"
        )
    report = dict(
        audit_industreal_records(
            a.records,
            official_split_csv=a.split_csv,
            official_inventory=a.inventory,
            expected_videos=a.expected_videos,
            expected_operators=a.expected_operators,
        )
    )
    report["schema_version"] = "industreal-registry-1.1.0"
    report["canonical_sha256"] = _canonical_hash(report)
    _write(report, a.output)
    raise SystemExit(0 if report.get("status") == "PASS" else 2)
