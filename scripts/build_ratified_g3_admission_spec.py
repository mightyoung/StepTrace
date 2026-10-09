"""Create a versioned, non-authorizing G3 admission spec after amendment ratification."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
INDUSTREAL_G3_RECORDINGS = (
    "05_assy_0_1",
    "05_assy_2_2",
    "05_main_0_1",
    "26_assy_0_1",
    "26_assy_1_5",
    "26_main_0_1",
)
MECCANO_RECORDINGS = ("0013", "0016")


def _ledger_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _find(rows: list[dict[str, str]], *, video_id: str, dataset: str) -> dict[str, str]:
    matches = [
        row
        for row in rows
        if (
            row.get("video_id") == video_id
            or str(row.get("asset_id", "")).rsplit("-", 1)[-1] == video_id
            or str(row.get("source_path", "")).replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0] == video_id
        )
        and str(row.get("source_dataset", "")).replace("_", "").replace("-", "").casefold()
        == dataset.replace("_", "").replace("-", "").casefold()
        and row.get("record_type") == "CANONICAL_ROOT_VIDEO"
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one ledger row for {dataset}:{video_id}, got {len(matches)}")
    return matches[0]


def build(base_spec: Path, output: Path) -> None:
    base = yaml.safe_load(base_spec.read_text(encoding="utf-8"))
    if not isinstance(base, dict):
        raise ValueError("base spec is not a mapping")
    split_path = ROOT / "research/split-manifest.json"
    industreal_split_path = ROOT / "research/split-manifest-1.4.0-industreal-fallback.json"
    ledger_path = ROOT / "research/dataset-ledger.csv"
    split_payload = json.loads(split_path.read_text(encoding="utf-8"))
    split_rows = split_payload["records"]
    industreal_split_rows = json.loads(
        industreal_split_path.read_text(encoding="utf-8")
    )["records"]
    ledger_rows = _ledger_rows(ledger_path)
    split_by_key = {
        (str(row.get("source_dataset")).replace("_", "").replace("-", "").casefold(), str(row.get("video_id"))): row
        for row in split_rows
    }
    industreal_split_by_key = {
        (
            str(row.get("source_dataset")).replace("_", "").replace("-", "").casefold(),
            str(row.get("video_id")),
        ): row
        for row in industreal_split_rows
    }

    units: list[dict[str, Any]] = []
    for video_id in MECCANO_RECORDINGS:
        row = _find(ledger_rows, video_id=video_id, dataset="MECCANO")
        units.append(
            {
                "task_id": "meccano",
                "dataset_id": "MECCANO",
                "operator_id": row["operator_id"],
                "complete_job_id": row["complete_job_id"],
                "canonical_root_id": row["canonical_root_id"],
                "canonical_group_id": row["canonical_group_id"],
                "video_id": video_id,
                "video": f"/srv/project/datasets/MECCANO/_codex_candidate_rgb_videos/MECCANO_RGB_Videos/{video_id}.mp4",
                "split_manifest": "/srv/project/sop-compliance-demo/g3-preflight-20260820/research/split-manifest.json",
                "dataset_ledger": "/srv/project/sop-compliance-demo/g3-preflight-20260820/research/dataset-ledger.csv",
            }
        )

    versioned_split = "/srv/project/sop-compliance-demo/g3-preflight-20260820/research/split-manifest-1.4.0-industreal-fallback.json"
    versioned_ledger = "/srv/project/sop-compliance-demo/g3-preflight-20260820/research/dataset-ledger-1.4.0-industreal-fallback.csv"
    for video_id in INDUSTREAL_G3_RECORDINGS:
        row = _find(ledger_rows, video_id=video_id, dataset="IndustReal")
        split = industreal_split_by_key[("industreal", video_id)]
        if split.get("project_split") != "DEVELOPMENT_G3_EVALUATION":
            raise ValueError(f"recording is not held-out G3 evaluation: {video_id}")
        units.append(
            {
                "task_id": "industreal",
                "dataset_id": "INDUSTREAL",
                "operator_id": "UNKNOWN",
                "complete_job_id": row["complete_job_id"],
                "canonical_root_id": row["canonical_root_id"],
                "canonical_group_id": row["canonical_group_id"],
                "video_id": video_id,
                "video": f"/srv/project/datasets/IndustReal/official/rgb_videos/{video_id}.mp4",
                "split_manifest": versioned_split,
                "dataset_ledger": versioned_ledger,
                "cluster_level": "complete_job",
                "operator_identity_status": "UNKNOWN_DISCLOSED",
            }
        )

    base["randomization_seed"] = 20260828
    base["units"] = units
    base["statistical_producer"]["producer_version"] = "0.2.0-complete-job-fallback"
    base["statistical_producer"]["real_input_enabled"] = True
    base["result_destination"] = {
        "ledger_id": "ledger:g3-development-paired-20260830-fix02",
        "ledger": "/srv/project/sop-compliance-demo/g3-preflight-20260820/results/paired-results-20260830-fix02.jsonl",
    }
    base["protocol_amendment"] = {
        "path": "research/audits/industreal-identity-amendment-1.4.0-ratified.json",
        "status": "RATIFIED_APPLIED_TO_VERSIONED_COPIES",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=ROOT / "configs/g3/remote-development-admission-20260820.yaml")
    parser.add_argument("--output", type=Path, default=ROOT / "configs/g3/remote-development-admission-20260821-ratified-fallback.json")
    args = parser.parse_args()
    build(args.base, args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
