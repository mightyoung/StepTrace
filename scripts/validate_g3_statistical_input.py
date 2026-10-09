"""Validate raw paired G3 opportunities before invoking the external R producer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from display_project.g3_statistical_input import validate_g3_statistical_input


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason_codes": [f"load:{type(exc).__name__}"]}))
        return 2
    valid, reasons = validate_g3_statistical_input(payload)
    print(json.dumps({"status": "READY" if valid else "BLOCKED", "reason_codes": reasons}))
    return 0 if valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
