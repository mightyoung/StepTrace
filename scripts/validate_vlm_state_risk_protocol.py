"""Fail-closed validator for the versioned VLM state/risk M0 protocol."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "research" / "vlm-state-risk-20260907"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_local_sources(root: Path, labels: dict[str, Any], errors: list[str]) -> None:
    sources = labels.get("local_sources")
    if not isinstance(sources, list) or not sources:
        errors.append("label feasibility must record hashed local source artifacts")
        return
    for item in sources:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            errors.append("invalid local source record")
            continue
        path = root / item["path"]
        expected = item.get("sha256")
        if not isinstance(expected, str) or len(expected) != 64 or expected != expected.lower():
            errors.append(f"invalid local source hash: {item['path']}")
        elif not path.is_file():
            errors.append(f"local source missing: {item['path']}")
        elif _sha(path) != expected:
            errors.append(f"local source hash changed: {item['path']}")


def _validate_runner_contract(root: Path, errors: list[str]) -> None:
    runner = root / "scripts" / "run_vlm_state_risk.py"
    if not runner.is_file():
        errors.append("missing development runner: scripts/run_vlm_state_risk.py")
        return
    try:
        tree = ast.parse(runner.read_text(encoding="utf-8"), filename=str(runner))
    except (OSError, SyntaxError) as exc:
        errors.append(f"development runner unreadable: {exc}")
        return
    flags: set[str] = set()
    choices: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        flag = node.args[0].value
        if not isinstance(flag, str):
            continue
        flags.add(flag)
        for keyword in node.keywords:
            if keyword.arg == "choices" and isinstance(keyword.value, (ast.List, ast.Tuple)):
                choices[flag] = {item.value for item in keyword.value.elts if isinstance(item, ast.Constant)}
    required = {"--video", "--knowledge", "--model-hash", "--config-hash", "--output-dir", "--profile", "--endpoint"}
    missing = sorted(required - flags)
    if missing:
        errors.append(f"runner CLI missing flags: {', '.join(missing)}")
    if choices.get("--profile") != {"development"}:
        errors.append("runner --profile must allow development only")


def validate(root: Path = ROOT) -> list[str]:
    base = root / "research" / "vlm-state-risk-20260907"
    errors: list[str] = []
    required = ["claim-freeze.yaml", "analysis-plan.md", "label-feasibility.yaml", "migration-record.json", "summary-zh.md"]
    for name in required:
        if not (base / name).is_file():
            errors.append(f"missing artifact: {name}")
    if errors:
        return errors
    try:
        claim = yaml.safe_load((base / "claim-freeze.yaml").read_text(encoding="utf-8"))
        labels = yaml.safe_load((base / "label-feasibility.yaml").read_text(encoding="utf-8"))
        migration = json.loads((base / "migration-record.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return [f"protocol unreadable: {exc}"]
    if claim.get("protocol_id") != "vlm-state-risk-v1" or claim.get("status") != "DEVELOPMENT_PROTOCOL_ONLY":
        errors.append("claim freeze must remain development-only")
    if claim.get("scope", {}).get("authority") != "state_estimation_only":
        errors.append("new authority must be state_estimation_only")
    if claim.get("scope", {}).get("formal_test") != "not_authorized":
        errors.append("formal test must remain unauthorized")
    if claim.get("scope", {}).get("execution_control") != "prohibited":
        errors.append("execution control must be prohibited")
    if labels.get("status") != "BLOCKED_FOR_FORMAL_RISK_SCORING":
        errors.append("label feasibility must remain blocked")
    for record in labels.get("records", []):
        if record.get("formal_use") != "prohibited":
            errors.append(f"formal use not prohibited for {record.get('task_id')}")
    _validate_local_sources(root, labels, errors)
    _validate_runner_contract(root, errors)
    if migration.get("formal_admission") is not False or migration.get("remote_runs_performed") is not False:
        errors.append("migration cannot authorize formal or remote runs")
    if migration.get("truth_or_completion_claims_added") is not False:
        errors.append("migration must not add truth/completion claims")
    for item in migration.get("old_protocol", {}).values():
        path = root / item["path"]
        if not path.is_file():
            errors.append(f"old frozen file missing: {item['path']}")
        elif _sha(path) != item["sha256"]:
            errors.append(f"old frozen hash changed: {item['path']}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    errors = validate(args.root.resolve())
    if errors:
        print("FAIL")
        print("\n".join(errors))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
