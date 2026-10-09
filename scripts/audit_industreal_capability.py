from __future__ import annotations

import argparse
import json
from pathlib import Path

from display_project.data.industreal_capability import (
    build_industreal_capability_artifact,
    write_capability_artifact,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Emit the fail-closed IndustReal capability artifact"
    )
    parser.add_argument(
        "registry",
        nargs="?",
        type=Path,
        default=Path("research/audits/industreal-registry.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("research/audits/industreal-capability.json"),
    )
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    artifact = build_industreal_capability_artifact(
        registry, registry_path=args.registry
    )
    write_capability_artifact(args.output, artifact)
    print(
        json.dumps(
            {
                "status": artifact["status"],
                "capability_status": artifact["capability_status"],
                "detection_metric_policy": artifact["detection_metric_policy"],
                "artifact_sha256": artifact["artifact_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
