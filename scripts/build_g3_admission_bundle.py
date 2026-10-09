"""Build non-authorizing G3 admission inputs from an explicit real-asset spec."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from display_project.g3_admission_bundle import build_g3_admission_bundle


def _write_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_g3_admission_bundle(args.spec)
    output = args.output_dir.resolve()
    _write_atomic(output / "bundle-report.json", report)
    if report["ready"]:
        for name, value in report["artifacts"].items():
            _write_atomic(output / f"{name.replace('_', '-')}.json", value)
    print(json.dumps({"status": report["status"], "reason_codes": report["reason_codes"]}))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
