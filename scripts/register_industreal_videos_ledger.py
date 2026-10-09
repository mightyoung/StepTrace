"""Preflight or apply a PASS IndustReal video audit to research registries."""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from display_project.data.industreal_ledger import IndustRealLedgerError, integrate


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audit", type=Path)
    ap.add_argument("--ledger", type=Path, default=Path("research/dataset-ledger.csv"))
    ap.add_argument(
        "--manifest", type=Path, default=Path("research/split-manifest.json")
    )
    ap.add_argument("--expected-audit-sha256")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    try:
        result = integrate(
            args.audit,
            args.ledger,
            args.manifest,
            expected_audit_sha256=args.expected_audit_sha256,
            apply=args.apply,
        )
    except (IndustRealLedgerError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
