"""Portable (repository-relative) evidence paths must verify on any machine."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from display_project.data.meccano_yolo_audit import (
    validate_meccano_annotation_sources,
)
from display_project.provenance import portable_path, resolve_portable

SPLIT_VIDEOS = {"train": ["01"], "val": ["07"], "test": ["08"]}


def _write_sources(root: Path, *, relative: bool) -> dict:
    entries = []
    for split, videos in SPLIT_VIDEOS.items():
        coco = root / "official-annotations" / f"instances_meccano_{split}.json"
        coco.parent.mkdir(parents=True, exist_ok=True)
        coco.write_text(
            json.dumps({"images": [], "annotations": [], "categories": []}),
            encoding="utf-8",
        )
        entries.append(_entry(coco, root, "ACTIVE_OBJECT_COCO", split, videos, relative))
        for video in videos:
            hand = root / "official-annotations" / split / f"{int(video):04d}.csv"
            hand.parent.mkdir(parents=True, exist_ok=True)
            with hand.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=[
                        "filename",
                        "region_shape_attributes",
                        "region_attributes",
                    ],
                )
                writer.writeheader()
            entries.append(_entry(hand, root, "HAND_VGG_CSV", split, [video], relative))
    body = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return {
        "status": "PASS",
        "source_count": len(entries),
        "sources_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "sources": entries,
        "held_out_videos_excluded": ["09"],
    }


def _entry(path: Path, root: Path, family: str, split: str, videos, relative: bool):
    recorded = (
        path.resolve().relative_to(root.resolve()).as_posix()
        if relative
        else path.resolve().as_posix()
    )
    return {
        "family": family,
        "split": split,
        "path": recorded,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "schema_status": "PASS",
        "used_videos": list(videos),
    }


@pytest.mark.parametrize("relative", [True, False])
def test_sources_validate_with_relative_and_absolute_paths(tmp_path, relative):
    provenance = _write_sources(tmp_path, relative=relative)
    summary, errors = validate_meccano_annotation_sources(
        provenance,
        splits=SPLIT_VIDEOS,
        repository_root=tmp_path,
    )
    assert errors == []
    assert summary["status"] == "PASS"


def test_relative_source_paths_verify_from_any_cwd(tmp_path, monkeypatch):
    provenance = _write_sources(tmp_path, relative=True)
    monkeypatch.chdir(tmp_path.parent)
    summary, errors = validate_meccano_annotation_sources(
        provenance,
        splits=SPLIT_VIDEOS,
        repository_root=tmp_path,
    )
    assert errors == []
    assert summary["status"] == "PASS"


def test_upward_traversal_is_rejected(tmp_path):
    provenance = _write_sources(tmp_path, relative=True)
    provenance["sources"][0]["path"] = "../outside.json"
    summary, errors = validate_meccano_annotation_sources(
        provenance,
        splits=SPLIT_VIDEOS,
        repository_root=tmp_path,
    )
    assert summary["status"] == "FAIL"
    assert any("must not traverse upward" in error for error in errors)


def test_tampered_relative_source_fails_closed(tmp_path):
    provenance = _write_sources(tmp_path, relative=True)
    target = tmp_path / provenance["sources"][0]["path"]
    target.write_text(
        json.dumps({"images": [], "annotations": [], "categories": [], "x": 1}),
        encoding="utf-8",
    )
    summary, errors = validate_meccano_annotation_sources(
        provenance,
        splits=SPLIT_VIDEOS,
        repository_root=tmp_path,
    )
    assert summary["status"] == "FAIL"
    assert any("sha256 mismatch" in error for error in errors)


def test_portable_path_prefers_repository_relative(tmp_path):
    inside = tmp_path / "research" / "audits" / "x.json"
    inside.parent.mkdir(parents=True)
    inside.write_text("{}", encoding="utf-8")
    assert portable_path(inside, tmp_path) == "research/audits/x.json"
    outside = tmp_path.parent / "elsewhere.json"
    assert Path(portable_path(outside, tmp_path)).is_absolute()


def test_resolve_portable_rejects_traversal(tmp_path):
    assert (
        resolve_portable("research/audits/x.json", tmp_path)
        == (tmp_path / "research/audits/x.json").resolve()
    )
    with pytest.raises(ValueError):
        resolve_portable("../escape.json", tmp_path)
