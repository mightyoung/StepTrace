from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dataset_commitment(
    path: Path,
    *,
    task_profile: str | None = None,
    class_names: list[str] | None = None,
) -> tuple[str, dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "PASS"
        or payload.get("passed") is not True
        or payload.get("dataset_profile") != "DEVELOPMENT_CANDIDATE"
        or payload.get("held_out_videos") != ["09"]
        or payload.get("held_out_test_assets") != []
    ):
        raise ValueError("dataset QA is not an uncontaminated development candidate")
    digest = payload.get("content_sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError("dataset QA content_sha256 is invalid")
    if task_profile is not None and payload.get("task_profile") != task_profile:
        raise ValueError("dataset QA task profile does not match the training request")
    if class_names and payload.get("names") != class_names:
        raise ValueError("dataset QA class names do not match the training request")
    return digest, payload


def _publish_artifact(
    source: Path,
    destination: Path,
    *,
    dataset_content_sha256: str,
    base_model_sha256: str,
    training_run_id: str,
    task_profile: str | None = None,
    class_names: list[str] | None = None,
    ultralytics_version: str | None = None,
    training_parameters: dict[str, object] | None = None,
) -> dict[str, object]:
    if not source.is_file() or not zipfile.is_zipfile(source):
        raise ValueError("training did not produce a PyTorch zip checkpoint")
    if not training_run_id.strip():
        raise ValueError("training_run_id must be non-empty")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_hash = _sha256(source)
    if destination.exists():
        if _sha256(destination) != source_hash:
            raise FileExistsError(f"refusing to overwrite {destination}")
    else:
        shutil.copy2(source, destination)
    provenance = {
        "schema_version": (
            "display_project.yolo_training_artifact-1.1.0"
            if task_profile else "display_project.yolo_training_artifact-1.0.0"
        ),
        "status": "TRAINED",
        "artifact_sha256": source_hash,
        "dataset_content_sha256": dataset_content_sha256,
        "base_model_sha256": base_model_sha256,
        "training_run_id": training_run_id,
        "split_role": "TRAIN",
        "training_completed": True,
    }
    if task_profile:
        provenance.update(
            task_profile=task_profile,
            class_names=list(class_names or []),
            ultralytics_version=ultralytics_version or "UNKNOWN",
            training_parameters=dict(training_parameters or {}),
        )
    provenance_path = Path(f"{destination}.provenance.json")
    encoded = (json.dumps(provenance, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    if provenance_path.exists():
        if provenance_path.read_bytes() != encoded:
            raise FileExistsError(f"refusing to overwrite {provenance_path}")
    else:
        provenance_path.write_bytes(encoded)
    return {
        "artifact": destination.as_posix(),
        "artifact_sha256": source_hash,
        "provenance": provenance_path.as_posix(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Train YOLO for MECCANO small objects")
    parser.add_argument("--data", default="configs/yolo_meccano_small_objects.yaml")
    parser.add_argument("--base-model", default="models/yolo-base/yolo11s.pt")
    parser.add_argument("--project", default="runs/yolo-meccano")
    parser.add_argument("--name", default="small-objects")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--task-profile")
    parser.add_argument("--class-name", action="append", dest="class_names")
    parser.add_argument(
        "--dataset-qa",
        default="data/yolo/meccano-small-objects/dataset-qa.json",
    )
    parser.add_argument(
        "--artifact-output",
        default="models/yolo-small-objects/best.pt",
    )
    parser.add_argument("--training-run-id")
    args = parser.parse_args()

    base_model = Path(args.base_model)
    if not base_model.is_file():
        raise FileNotFoundError(f"base model is missing: {base_model}")
    dataset_content_sha256, _ = _dataset_commitment(
        Path(args.dataset_qa),
        task_profile=args.task_profile,
        class_names=args.class_names,
    )

    import ultralytics

    model = ultralytics.YOLO(str(base_model))
    result = model.train(
        data=str(Path(args.data)),
        imgsz=args.imgsz,
        epochs=args.epochs,
        batch=args.batch,
        workers=args.workers,
        device=args.device,
        project=args.project,
        name=args.name,
        exist_ok=True,
        plots=True,
        seed=args.seed,
        deterministic=True,
    )
    save_dir = getattr(result, "save_dir", None)
    if save_dir is None:
        raise RuntimeError("Ultralytics did not return save_dir")
    training_run_id = args.training_run_id or (
        f"{args.name}-epochs{args.epochs}-imgsz{args.imgsz}"
    )
    published = _publish_artifact(
        Path(save_dir) / "weights" / "best.pt",
        Path(args.artifact_output),
        dataset_content_sha256=dataset_content_sha256,
        base_model_sha256=_sha256(base_model),
        training_run_id=training_run_id,
        task_profile=args.task_profile,
        class_names=args.class_names,
        ultralytics_version=getattr(ultralytics, "__version__", "UNKNOWN"),
        training_parameters={
            "imgsz": args.imgsz,
            "epochs": args.epochs,
            "batch": args.batch,
            "workers": args.workers,
            "device": args.device,
            "seed": args.seed,
            "deterministic": True,
        },
    )
    print(
        json.dumps(
            {"save_dir": str(save_dir), **published},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
