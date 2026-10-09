"""Freeze reproducible development preflight anchors with explicit provenance.

The anchors bind committed code, the active Python environment, the official
MECCANO annotation sources plus converted-dataset QA, and the candidate split
manifests.  A non-zero anchor is only a commitment: this artifact explicitly
does not close G1 or G2 and does not authorize G3 execution.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import platform
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable


ANCHOR_NAMES = ("code_hash", "environment_hash", "annotation_hash", "partition_hash")


def _canonical(payload: object) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(payload: object) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _file_evidence(path: Path, root: Path) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return {
        "path": _relative(path, root),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _directory_evidence(path: Path, root: Path) -> dict[str, object]:
    if not path.is_dir():
        raise FileNotFoundError(path)
    files = [_file_evidence(item, root) for item in sorted(path.rglob("*")) if item.is_file()]
    if not files:
        raise ValueError(f"annotation source directory is empty: {path}")
    return {
        "path": _relative(path, root),
        "file_count": len(files),
        "files": files,
        "content_sha256": _digest(files),
    }


def _git_value(root: Path, revision: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", revision],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40,64}", value):
        raise ValueError(f"git returned an invalid object id for {revision}")
    return value


def _installed_packages() -> tuple[tuple[str, str], ...]:
    packages = {
        (distribution.metadata["Name"].strip().lower(), distribution.version)
        for distribution in metadata.distributions()
        if distribution.metadata.get("Name")
    }
    return tuple(sorted(packages))


def _code_provenance(root: Path) -> dict[str, object]:
    return {
        "scope": "COMMITTED_HEAD_ONLY",
        "git_commit": _git_value(root, "HEAD"),
        "git_tree": _git_value(root, "HEAD^{tree}"),
        "git_object_format": "sha1",
    }


def _environment_provenance(
    root: Path, packages: Iterable[tuple[str, str]]
) -> dict[str, object]:
    locked = [
        {"name": name.strip().lower(), "version": version.strip()}
        for name, version in sorted(packages, key=lambda item: item[0].lower())
    ]
    if not locked:
        raise ValueError("the installed package inventory is empty")
    return {
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "cache_tag": sys.implementation.cache_tag,
            "compiler": platform.python_compiler(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "package_inventory_kind": "INSTALLED_DISTRIBUTIONS_EXACT_VERSIONS",
        "packages": locked,
        "project_requirements": _file_evidence(root / "pyproject.toml", root),
    }


def _annotation_provenance(
    root: Path,
    active_annotations: Path,
    hand_annotations: Path,
    dataset_audit: Path,
    dataset_qa: Path,
) -> dict[str, object]:
    audit_evidence = _file_evidence(dataset_audit, root)
    qa_evidence = _file_evidence(dataset_qa, root)
    qa = json.loads(dataset_qa.read_text(encoding="utf-8-sig"))
    if not (
        isinstance(qa, dict)
        and qa.get("status") == "PASS"
        and qa.get("passed") is True
        and qa.get("dataset_profile") == "DEVELOPMENT_CANDIDATE"
        and qa.get("held_out_videos") == ["09"]
        and qa.get("held_out_test_assets") == []
        and qa.get("conversion_report_sha256") == audit_evidence["sha256"]
    ):
        raise ValueError("dataset QA does not bind an uncontaminated conversion report")
    return {
        "scope": "MECCANO_OFFICIAL_COCO_VGG_AND_CONVERTED_DATASET_QA",
        "active_object_coco": _directory_evidence(active_annotations, root),
        "hand_vgg_csv": _directory_evidence(hand_annotations, root),
        "dataset_audit": audit_evidence,
        "dataset_qa": qa_evidence,
        "dataset_content_sha256": qa.get("content_sha256"),
        "held_out_videos": qa["held_out_videos"],
        "held_out_test_assets": qa["held_out_test_assets"],
    }


def _partition_provenance(
    root: Path, candidate_manifest: Path, split_manifest: Path
) -> dict[str, object]:
    candidate = json.loads(candidate_manifest.read_text(encoding="utf-8"))
    candidate_evidence = _file_evidence(candidate_manifest, root)
    split_evidence = _file_evidence(split_manifest, root)
    if not isinstance(candidate, dict):
        raise ValueError("candidate manifest must be an object")
    if candidate.get("source_identity_manifest_sha256") != split_evidence["sha256"]:
        raise ValueError("candidate manifest does not bind the split manifest")
    tombstones = candidate.get("excluded_held_out_records")
    if not isinstance(tombstones, list) or not any(
        isinstance(record, dict)
        and record.get("dataset_video_id") == "09"
        and record.get("tombstone_id") == "TOMBSTONE-MECCANO-0009"
        and record.get("present_in_candidate_dataset") is False
        for record in tombstones
    ):
        raise ValueError("candidate manifest does not preserve TOMBSTONE-MECCANO-0009")
    return {
        "scope": "DEVELOPMENT_CANDIDATE_PARTITION",
        "candidate_manifest": candidate_evidence,
        "split_manifest": split_evidence,
        "candidate_status": candidate.get("status"),
        "formal_admission": candidate.get("formal_admission"),
        "tombstone_0009_preserved": True,
    }


def _update_manifest(path: Path, anchors: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    if not re.search(r"(?m)^profile:\s*DEVELOPMENT_ONLY\s*$", text):
        raise ValueError("only a DEVELOPMENT_ONLY manifest may be updated")
    for name in ANCHOR_NAMES:
        pattern = re.compile(rf'(?m)^(\s{{2}}{re.escape(name)}:\s*)"[0-9a-f]{{64}}"\s*$')
        text, count = pattern.subn(rf'\g<1>"{anchors[name]}"', text)
        if count != 1:
            raise ValueError(f"manifest must contain exactly one {name}")
    path.write_text(text, encoding="utf-8")


def freeze_preflight_anchors(
    *,
    root: Path,
    manifest: Path,
    output: Path,
    active_annotations: Path,
    hand_annotations: Path,
    dataset_audit: Path,
    dataset_qa: Path,
    candidate_manifest: Path,
    split_manifest: Path,
    packages: Iterable[tuple[str, str]] | None = None,
    update_manifest: bool = False,
) -> dict[str, object]:
    root = root.resolve()
    provenance = {
        "code": _code_provenance(root),
        "environment": _environment_provenance(
            root, _installed_packages() if packages is None else packages
        ),
        "annotation": _annotation_provenance(
            root,
            active_annotations,
            hand_annotations,
            dataset_audit,
            dataset_qa,
        ),
        "partition": _partition_provenance(root, candidate_manifest, split_manifest),
    }
    anchors = {
        "code_hash": _digest(provenance["code"]),
        "environment_hash": _digest(provenance["environment"]),
        "annotation_hash": _digest(provenance["annotation"]),
        "partition_hash": _digest(provenance["partition"]),
    }
    artifact: dict[str, object] = {
        "schema_version": "display_project.g1_preflight_anchors-1.0.0",
        "status": "FROZEN_DEVELOPMENT_ONLY",
        "formal_admission": "DOES_NOT_CLOSE_G1_OR_G2",
        "g3_execution_authorized": False,
        "anchors": anchors,
        "provenance": provenance,
    }
    artifact["artifact_sha256"] = _digest(artifact)
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output.exists() and output.read_text(encoding="utf-8") != encoded:
        raise FileExistsError(f"refusing to overwrite changed frozen artifact: {output}")
    output.write_text(encoded, encoding="utf-8")
    if update_manifest:
        _update_manifest(manifest, anchors)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("configs/experiments/paired-development-example.yaml"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("research/audits/g1-preflight-anchors.json"),
    )
    parser.add_argument(
        "--active-annotations",
        type=Path,
        default=Path("datasets/MECCANO/MECCANO_active_objects_annotations"),
    )
    parser.add_argument(
        "--hand-annotations",
        type=Path,
        default=Path("datasets/MECCANO/MECCANO_hands_bounding_box_annotations"),
    )
    parser.add_argument(
        "--dataset-audit",
        type=Path,
        default=Path("data/yolo/meccano-small-objects/dataset-audit.json"),
    )
    parser.add_argument(
        "--dataset-qa",
        type=Path,
        default=Path("data/yolo/meccano-small-objects/dataset-qa.json"),
    )
    parser.add_argument(
        "--candidate-manifest",
        type=Path,
        default=Path("research/audits/meccano-yolo-candidate-split-manifest.json"),
    )
    parser.add_argument(
        "--split-manifest",
        type=Path,
        default=Path("research/split-manifest.json"),
    )
    parser.add_argument("--update-manifest", action="store_true")
    args = parser.parse_args()
    artifact = freeze_preflight_anchors(
        root=args.root,
        manifest=args.manifest,
        output=args.output,
        active_annotations=args.active_annotations,
        hand_annotations=args.hand_annotations,
        dataset_audit=args.dataset_audit,
        dataset_qa=args.dataset_qa,
        candidate_manifest=args.candidate_manifest,
        split_manifest=args.split_manifest,
        packages=None,
        update_manifest=args.update_manifest,
    )
    print(json.dumps(artifact["anchors"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
