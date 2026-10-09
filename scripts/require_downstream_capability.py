from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from display_project.g3_capability import require_g3_capability  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed G3 guard; current prechecks never authorize"
    )
    parser.add_argument("--capability", required=True)
    parser.add_argument("--stage", required=True)
    args = parser.parse_args(argv)
    try:
        require_g3_capability(args.capability, args.stage)
    except (PermissionError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("G3 downstream authorization is disabled", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
