from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from display_project.g3_statistical_input import (  # noqa: E402
    TASK_SPECIFIC_INPUT_SCHEMA,
    validate_g3_statistical_input,
)
from build_g3_full_producer_fixture import build_fixture  # noqa: E402


def build(root: Path, *, dll_sha256: str) -> dict:
    value = build_fixture(root, dll_sha256=dll_sha256)
    value["schema_version"] = TASK_SPECIFIC_INPUT_SCHEMA
    value["cluster_level_by_task"] = {"meccano": "operator", "industreal": "complete_job"}
    value["resampling"]["cluster_level"] = "task_specific"
    for row in value["opportunities"]:
        if row["task"] == "industreal":
            row["operator_id"] = "UNKNOWN"
    valid, reasons = validate_g3_statistical_input(value)
    if not valid:
        raise ValueError("task-specific fixture failed validation: " + ",".join(reasons))
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dll-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = build(ROOT, dll_sha256=args.dll_sha256)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"rows": len(value["opportunities"]), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
