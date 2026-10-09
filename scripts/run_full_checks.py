"""Single verification entry point for local machines, the server, and CI.

Runs, in order: ruff lint, the full pytest suite, and (when the frozen
artifacts exist) the research-contract validator. Any failure exits non-zero.
Usage:

    python scripts/run_full_checks.py [--skip-contract]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(label: str, command: list[str]) -> bool:
    print(f"== {label}: {' '.join(command)}", flush=True)
    completed = subprocess.run(command, cwd=ROOT)
    status = "PASS" if completed.returncode == 0 else "FAIL"
    print(f"== {label}: {status}", flush=True)
    return completed.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-contract",
        action="store_true",
        help="skip the research-contract validator (e.g. before artifacts exist)",
    )
    args = parser.parse_args()

    results: list[tuple[str, bool]] = []
    if shutil.which("ruff") is not None:
        results.append(("ruff", _run("ruff", ["ruff", "check", "src", "scripts", "tests"])))
    else:
        print("== ruff: SKIPPED (not installed; pip install -e .[dev])", flush=True)
    results.append(
        ("pytest", _run("pytest", [sys.executable, "-m", "pytest", "-q"]))
    )
    if not args.skip_contract:
        results.append(
            (
                "research-contract",
                _run(
                    "research-contract",
                    [sys.executable, "scripts/validate_research_contract.py"],
                ),
            )
        )

    failed = [label for label, passed in results if not passed]
    if failed:
        print(f"FAILED: {', '.join(failed)}", flush=True)
        return 1
    print("ALL CHECKS PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
