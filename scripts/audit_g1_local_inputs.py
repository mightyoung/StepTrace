"""Audit whether the three missing G1 artifacts can be built locally.

This command is intentionally read-only with respect to training inputs and
never creates calibration observations, HMM observations, or model weights.
It reports exact blockers so a missing input cannot be replaced with a test
fixture, synthetic record, or the tombstoned MECCANO-0009 asset.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from typing import Any


HASH_CHARS = frozenset("0123456789abcdef")
FORBIDDEN_IDENTITY_KEYS = frozenset(
    {"video_id", "frame_id", "test_video", "formal_test"}
)
DEFAULT_HMM_PARTITION_MANIFEST = Path("data/g1/meccano-g1-episode-manifest.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in HASH_CHARS for character in value)
    )


def _blocked(code: str, reason: str, **evidence: Any) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "ready": False,
        "error_code": code,
        "reason": reason,
        **evidence,
    }


def _audit_calibration(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return _blocked(
            "CALIBRATION_INPUT_MISSING",
            "A provenance-bound, non-test CALIBRATION probability/label input is required.",
            input_path=path.as_posix(),
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return _blocked("CALIBRATION_INPUT_INVALID", str(error), input_path=path.as_posix())
    if not isinstance(payload, dict):
        return _blocked("CALIBRATION_INPUT_INVALID", "input must be a JSON object")
    if payload.get("split_role") != "CALIBRATION":
        return _blocked("CALIBRATION_SPLIT_INVALID", "split_role must be CALIBRATION")
    if payload.get("truth_store_used") is not False:
        return _blocked(
            "CALIBRATION_TRUTH_BOUNDARY_INVALID",
            "truth_store_used must be explicitly false",
        )
    forbidden = sorted(FORBIDDEN_IDENTITY_KEYS.intersection(payload))
    if forbidden:
        return _blocked(
            "CALIBRATION_IDENTITY_FORBIDDEN",
            f"forbidden identity fields are present: {forbidden}",
        )
    if not _is_hash(payload.get("partition_hash")) or not _is_hash(
        payload.get("source_digest")
    ):
        return _blocked(
            "CALIBRATION_PROVENANCE_INVALID",
            "partition_hash and source_digest must be lowercase sha256 values",
        )
    probabilities = payload.get("probabilities")
    labels = payload.get("labels")
    if (
        not isinstance(probabilities, list)
        or not isinstance(labels, list)
        or not probabilities
        or len(probabilities) != len(labels)
    ):
        return _blocked(
            "CALIBRATION_OBSERVATIONS_INVALID",
            "non-empty probabilities and labels lists of equal length are required",
        )
    return {
        "status": "READY",
        "ready": True,
        "input_path": path.as_posix(),
        "input_sha256": _sha256(path),
        "observation_count": len(labels),
        "partition_hash": payload["partition_hash"],
    }


def _audit_hmm(path: Path, partition_manifest: Path) -> dict[str, Any]:
    if not path.is_file():
        return _blocked(
            "HMM_FIT_INPUT_MISSING",
            "Pre-extracted, non-test DEVELOPMENT observations are required.",
            input_path=path.as_posix(),
        )
    if not partition_manifest.is_file():
        return _blocked(
            "HMM_PARTITION_MANIFEST_MISSING",
            "A frozen partition manifest is required before HMM fitting.",
            partition_manifest=partition_manifest.as_posix(),
        )
    try:
        partition_payload = json.loads(partition_manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return _blocked("HMM_PARTITION_MANIFEST_INVALID", str(error))
    legacy_identity_manifest_ready = (
        isinstance(partition_payload, dict)
        and partition_payload.get("dataset_identity_status", {}).get("MECCANO")
        == "READY"
        and any(
            isinstance(record, dict)
            and record.get("tombstone_id") == "TOMBSTONE-MECCANO-0009"
            and record.get("formal_use_allowed") == "NO"
            for record in partition_payload.get("records", [])
        )
    )
    g1_episode_manifest_ready = (
        isinstance(partition_payload, dict)
        and partition_payload.get("status")
        == "FROZEN_DEVELOPMENT_CALIBRATION"
        and partition_payload.get("selection_policy", {}).get(
            "tombstone_0009_excluded"
        )
        is True
        and partition_payload.get("selection_policy", {}).get(
            "runtime_receives_action_labels"
        )
        is False
        and partition_payload.get("selection_policy", {}).get(
            "runtime_truth_store_allowed"
        )
        is False
        and isinstance(partition_payload.get("records"), list)
        and bool(partition_payload["records"])
        and all(
            isinstance(record, dict)
            and record.get("video_id") != "0009"
            and record.get("formal_test") is False
            and record.get("runtime_truth_store_allowed") is False
            and record.get("project_split")
            in {"DEVELOPMENT", "CALIBRATION"}
            and _is_hash(record.get("group_hash"))
            for record in partition_payload["records"]
        )
    )
    if not (legacy_identity_manifest_ready or g1_episode_manifest_ready):
        return _blocked(
            "HMM_PARTITION_MANIFEST_NOT_READY",
            "MECCANO identity readiness and the 0009 exclusion must be explicit",
        )
    records: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"line {line_number} is not an object")
            if set(record) != {"group_hash", "partition", "state", "features"}:
                raise ValueError(f"line {line_number} schema mismatch")
            if record.get("partition") != "DEVELOPMENT":
                raise ValueError(f"line {line_number} is not DEVELOPMENT")
            if FORBIDDEN_IDENTITY_KEYS.intersection(record):
                raise ValueError(f"line {line_number} contains forbidden video/frame identity")
            if not _is_hash(record.get("group_hash")):
                raise ValueError(f"line {line_number} group_hash is invalid")
            if not isinstance(record.get("state"), str) or not record["state"]:
                raise ValueError(f"line {line_number} state is invalid")
            features = record.get("features")
            if (
                not isinstance(features, list)
                or len(features) != 3
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    for value in features
                )
            ):
                raise ValueError(f"line {line_number} features are invalid")
            records.append(record)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        return _blocked("HMM_FIT_INPUT_INVALID", str(error), input_path=path.as_posix())
    if not records:
        return _blocked("HMM_FIT_INPUT_EMPTY", "no observations were found")
    return {
        "status": "READY",
        "ready": True,
        "input_path": path.as_posix(),
        "input_sha256": _sha256(path),
        "observation_count": len(records),
        "group_count": len({record["group_hash"] for record in records}),
        "partition_manifest": partition_manifest.as_posix(),
        "partition_manifest_sha256": _sha256(partition_manifest),
    }


def _audit_yolo(
    dataset_qa: Path, base_model: Path, base_provenance: Path
) -> dict[str, Any]:
    missing = [
        path.as_posix()
        for path in (dataset_qa, base_model, base_provenance)
        if not path.is_file()
    ]
    if missing:
        return _blocked(
            "YOLO_TRAINING_INPUT_MISSING",
            f"required files are missing: {missing}",
        )
    try:
        qa = json.loads(dataset_qa.read_text(encoding="utf-8-sig"))
        provenance = json.loads(base_provenance.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return _blocked("YOLO_TRAINING_INPUT_INVALID", str(error))
    if not (
        isinstance(qa, dict)
        and qa.get("status") == "PASS"
        and qa.get("passed") is True
        and qa.get("dataset_profile") == "DEVELOPMENT_CANDIDATE"
        and qa.get("held_out_videos") == ["09"]
        and qa.get("held_out_test_assets") == []
        and _is_hash(qa.get("content_sha256"))
    ):
        return _blocked(
            "YOLO_DATASET_QA_INVALID",
            "dataset QA is not an uncontaminated development candidate",
        )
    model_hash = _sha256(base_model)
    if not (
        isinstance(provenance, dict)
        and provenance.get("artifact_role") == "PRETRAINED_BASE_MODEL"
        and provenance.get("training_status") == "NOT_FINE_TUNED"
        and provenance.get("formal_admission") == "PROHIBITED_AS_MECCANO_BEST_PT"
        and provenance.get("sha256") == model_hash
    ):
        return _blocked(
            "YOLO_BASE_PROVENANCE_INVALID",
            "base-model provenance does not bind the local checkpoint",
        )
    ultralytics_available = importlib.util.find_spec("ultralytics") is not None
    torch_available = importlib.util.find_spec("torch") is not None
    cuda_available = False
    torch_version = None
    if torch_available:
        import torch

        torch_version = torch.__version__
        cuda_available = bool(torch.cuda.is_available())
    evidence = {
        "dataset_qa_sha256": _sha256(dataset_qa),
        "dataset_content_sha256": qa["content_sha256"],
        "base_model_sha256": model_hash,
        "torch_version": torch_version,
        "cuda_available": cuda_available,
        "ultralytics_available": ultralytics_available,
    }
    if not ultralytics_available:
        return _blocked(
            "ULTRALYTICS_MISSING",
            "Install the declared yolo optional dependency before training.",
            **evidence,
        )
    if not cuda_available:
        return _blocked(
            "CUDA_UNAVAILABLE",
            "A CUDA-capable PyTorch runtime is required for the frozen training run.",
            **evidence,
        )
    return {"status": "READY", "ready": True, **evidence}


def build_report(
    *,
    calibration_input: Path,
    hmm_input: Path,
    partition_manifest: Path,
    dataset_qa: Path,
    base_model: Path,
    base_provenance: Path,
) -> dict[str, Any]:
    chains = {
        "calibration": _audit_calibration(calibration_input),
        "hmm_or_crf": _audit_hmm(hmm_input, partition_manifest),
        "yolo": _audit_yolo(dataset_qa, base_model, base_provenance),
    }
    ready = all(chain["ready"] for chain in chains.values())
    return {
        "schema_version": "display_project.g1_local_input_readiness-1.0.0",
        "status": "READY" if ready else "BLOCKED",
        "all_generators_ready": ready,
        "synthetic_or_tombstoned_substitution_allowed": False,
        "chains": chains,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--calibration-input",
        type=Path,
        default=Path("data/calibration/development-calibration.json"),
    )
    parser.add_argument(
        "--hmm-input",
        type=Path,
        default=Path("data/hmm/development-observations.jsonl"),
    )
    parser.add_argument(
        "--partition-manifest",
        type=Path,
        # HMM readiness is bound to the frozen episode manifest, not the
        # detector's candidate split manifest.  The latter remains the input
        # for the YOLO/data-isolation audit and lacks episode selection
        # semantics required by the sequence-model chain.
        default=DEFAULT_HMM_PARTITION_MANIFEST,
    )
    parser.add_argument(
        "--dataset-qa",
        type=Path,
        default=Path("data/yolo/meccano-small-objects/dataset-qa.json"),
    )
    parser.add_argument(
        "--base-model", type=Path, default=Path("models/yolo-base/yolo11s.pt")
    )
    parser.add_argument(
        "--base-provenance",
        type=Path,
        default=Path("models/yolo-base/yolo11s.provenance.json"),
    )
    parser.add_argument(
        "--output", type=Path, default=Path("models/g1-local-input-readiness.json")
    )
    args = parser.parse_args()
    report = build_report(
        calibration_input=args.calibration_input,
        hmm_input=args.hmm_input,
        partition_manifest=args.partition_manifest,
        dataset_qa=args.dataset_qa,
        base_model=args.base_model,
        base_provenance=args.base_provenance,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["all_generators_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
