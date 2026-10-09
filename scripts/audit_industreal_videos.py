from __future__ import annotations

import argparse
import sys
from pathlib import Path

from display_project.data.industreal_videos import (
    audit_industreal_videos,
    write_inventory,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_path", type=Path)
    parser.add_argument("labels_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--extract",
        type=Path,
        help="dedicated extraction directory; an existing directory is audit-only",
    )
    parser.add_argument(
        "--verified-on",
        help="exact ISO verification date (YYYY-MM-DD); defaults to today",
    )
    args = parser.parse_args()
    report = audit_industreal_videos(
        args.zip_path,
        args.labels_dir,
        extracted_dir=args.extract,
        verified_on=args.verified_on,
    )
    write_inventory(args.output, report)
    print(report["status"])
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
