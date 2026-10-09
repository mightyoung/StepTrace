from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from display_project.research_contract import (  # noqa: E402
    ContractValidationError,
    validate_research_contract,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the frozen P0 research contract."
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args()
    try:
        evidence = validate_research_contract(args.repo_root)
    except ContractValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
