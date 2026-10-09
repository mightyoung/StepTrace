from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import zipfile

from scripts import train_yolo_small_objects


def _checkpoint(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("archive/data.pkl", b"checkpoint" * 256)
        archive.writestr("archive/version", "3")


def test_successful_training_publishes_hash_bound_weight_provenance(
    tmp_path: Path, monkeypatch, capsys
):
    dataset_hash = hashlib.sha256(b"candidate").hexdigest()
    qa = tmp_path / "dataset-qa.json"
    qa.write_text(
        json.dumps(
            {
                "status": "PASS",
                "passed": True,
                "dataset_profile": "DEVELOPMENT_CANDIDATE",
                "held_out_videos": ["09"],
                "held_out_test_assets": [],
                "content_sha256": dataset_hash,
                "task_profile": "hand",
                "names": ["hand"],
            }
        ),
        encoding="utf-8",
    )
    base_model = tmp_path / "base.pt"
    base_model.write_bytes(b"official-base")
    save_dir = tmp_path / "run"

    class FakeYOLO:
        def __init__(self, model_path: str):
            assert model_path == str(base_model)

        def train(self, **kwargs):
            assert kwargs["imgsz"] == 1280
            assert kwargs["seed"] == 17
            assert kwargs["deterministic"] is True
            _checkpoint(save_dir / "weights" / "best.pt")
            return SimpleNamespace(save_dir=save_dir)

    monkeypatch.setitem(
        sys.modules, "ultralytics",
        SimpleNamespace(YOLO=FakeYOLO, __version__="8.3.test"),
    )
    artifact = tmp_path / "published" / "best.pt"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "train-yolo",
            "--base-model",
            str(base_model),
            "--dataset-qa",
            str(qa),
            "--artifact-output",
            str(artifact),
            "--training-run-id",
            "run-001",
            "--task-profile",
            "hand",
            "--class-name",
            "hand",
            "--seed",
            "17",
        ],
    )

    assert train_yolo_small_objects.main() == 0
    provenance = json.loads(
        Path(f"{artifact}.provenance.json").read_text(encoding="utf-8")
    )
    assert provenance == {
        "schema_version": "display_project.yolo_training_artifact-1.1.0",
        "status": "TRAINED",
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "dataset_content_sha256": dataset_hash,
        "base_model_sha256": hashlib.sha256(base_model.read_bytes()).hexdigest(),
        "training_run_id": "run-001",
        "task_profile": "hand",
        "class_names": ["hand"],
        "ultralytics_version": "8.3.test",
        "training_parameters": {
            "imgsz": 1280,
            "epochs": 80,
            "batch": 8,
            "workers": 4,
            "device": "cuda",
            "seed": 17,
            "deterministic": True,
        },
        "split_role": "TRAIN",
        "training_completed": True,
    }
    printed = json.loads(capsys.readouterr().out)
    assert printed["artifact"] == artifact.as_posix()
    assert printed["artifact_sha256"] == provenance["artifact_sha256"]


def test_training_rejects_held_out_contamination_before_loading_ultralytics(
    tmp_path: Path, monkeypatch
):
    qa = tmp_path / "dataset-qa.json"
    qa.write_text(
        json.dumps(
            {
                "status": "PASS",
                "passed": True,
                "dataset_profile": "DEVELOPMENT_CANDIDATE",
                "held_out_videos": ["09"],
                "held_out_test_assets": ["images/test/09_00001.jpg"],
                "content_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    base_model = tmp_path / "base.pt"
    base_model.write_bytes(b"base")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "train-yolo",
            "--base-model",
            str(base_model),
            "--dataset-qa",
            str(qa),
        ],
    )

    try:
        train_yolo_small_objects.main()
    except ValueError as error:
        assert "uncontaminated development candidate" in str(error)
    else:
        raise AssertionError("held-out contamination reached the trainer")


def test_training_rejects_task_profile_mismatch_before_loading_ultralytics(
    tmp_path: Path, monkeypatch
):
    qa = tmp_path / "dataset-qa.json"
    qa.write_text(
        json.dumps(
            {
                "status": "PASS",
                "passed": True,
                "dataset_profile": "DEVELOPMENT_CANDIDATE",
                "held_out_videos": ["09"],
                "held_out_test_assets": [],
                "content_sha256": "a" * 64,
                "task_profile": "fasteners",
                "names": ["screw", "bolt", "screwdriver"],
            }
        ),
        encoding="utf-8",
    )
    base_model = tmp_path / "base.pt"
    base_model.write_bytes(b"base")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "train-yolo", "--base-model", str(base_model),
            "--dataset-qa", str(qa), "--task-profile", "hand",
            "--class-name", "hand",
        ],
    )

    try:
        train_yolo_small_objects.main()
    except ValueError as error:
        assert "task profile" in str(error)
    else:
        raise AssertionError("profile mismatch reached the trainer")
