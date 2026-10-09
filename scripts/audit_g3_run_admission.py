"""Evaluate current G3 run admission without fabricating missing commitments."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from display_project.g3_run_admission import evaluate_g3_run_admission


def _optional(path: Path | None, default: Any) -> Any:
    if path is None:
        return default
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-snapshot", type=Path, required=True)
    parser.add_argument("--snapshot-predecessor", type=Path, required=True)
    parser.add_argument("--authority-envelope", type=Path)
    parser.add_argument("--authority-attestation", type=Path)
    parser.add_argument("--task-bindings", type=Path)
    parser.add_argument("--method-manifest", type=Path)
    parser.add_argument("--unit-inventory", type=Path)
    parser.add_argument("--statistical-producer", type=Path)
    parser.add_argument("--result-destination", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    decision = evaluate_g3_run_admission(
        authority_envelope=_optional(args.authority_envelope, {}),
        authority_attestation=_optional(args.authority_attestation, {}),
        current_snapshot=_optional(args.current_snapshot, {}),
        snapshot_predecessor=_optional(args.snapshot_predecessor, None),
        task_bindings=_optional(args.task_bindings, []),
        method_manifest=_optional(args.method_manifest, {}),
        unit_inventory=_optional(args.unit_inventory, []),
        statistical_producer=_optional(args.statistical_producer, None),
        result_destination=_optional(args.result_destination, {}),
        profile="DEVELOPMENT_G3_EVALUATION",
        consumer_id="runner:g3-paired-panel",
        nonce="diagnostic-admission-audit-20260830-fix02",
        issued_at=_iso(now),
        expires_at=_iso(now + timedelta(hours=48)),
        now=now,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "status": decision["status"],
                "ready": decision["ready"],
                "reason_codes": decision["reason_codes"],
                "output": args.output.as_posix(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if decision["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
