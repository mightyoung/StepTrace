"""Guards for the panel specs and the G-V0-6 fixture.

These pin the panel configuration gaps called out in the validation protocol §4
("当前面板配置的缺口"). They exist because the failure mode they prevent is silent:
a spec missing ablation arms, or a 27B stack that inherits the 9B `model_name`
default, both produce a *runnable* panel whose evidence chain is wrong rather than
broken.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from display_project.vlm_sop.state_risk_contract import inference_json_schema
from display_project.vlm_sop.state_risk_knowledge import validate_knowledge
from display_project.vlm_sop.state_risk_prompt import METHOD_IDS

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = REPO_ROOT / "configs" / "experiments"
KNOWN_PACK = REPO_ROOT / "configs" / "vlm" / "state_risk" / "gv06-reference-port.yaml"

B0 = "B0-fixed-streak-v1"
REQUIRED_BUDGET = {
    "job_timeout_seconds",
    "sample_every",
    "window_frames",
    "call_timeout_seconds",
    "max_attempts",
    "max_backlog_seconds",
}
ALL_EIGHT = sorted(METHOD_IDS | {B0})
H8_SUBSET = sorted({B0, "B1-raw-imagery-v1", "P-structured-temporal-v1", "P-ablation-no-fact-split-v1"})
REGISTERED_ALIASES = {"Qwen3.5-9B-Q4_K_M", "Qwen3.8-27B-Q4M"}

SPEC_PATHS = sorted(SPEC_DIR.glob("vlm-state-risk-*.yaml"))


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_spec_files_exist() -> None:
    names = {p.name for p in SPEC_PATHS}
    assert "vlm-state-risk-development.yaml" in names
    assert "vlm-state-risk-qwen35-9b-validation.yaml" in names, "9B stack spec must exist"
    assert "vlm-state-risk-qwen38-validation.yaml" in names, "27B stack spec must exist"


@pytest.mark.parametrize("path", SPEC_PATHS, ids=lambda p: p.name)
def test_every_spec_is_development_only_with_valid_methods(path: Path) -> None:
    spec = _load(path)
    assert spec["profile"] == "development"
    assert spec["formal_admission"] is False
    methods = spec["methods"]
    assert methods and len(set(methods)) == len(methods)
    unknown = set(methods) - (set(METHOD_IDS) | {B0})
    assert not unknown, f"{path.name} declares self-invented arms: {sorted(unknown)}"


@pytest.mark.parametrize("path", SPEC_PATHS, ids=lambda p: p.name)
def test_every_spec_freezes_the_full_budget(path: Path) -> None:
    budget = _load(path)["budget"]
    assert set(budget) == REQUIRED_BUDGET
    assert all(isinstance(budget[k], int) and not isinstance(budget[k], bool)
               for k in ("sample_every", "window_frames", "max_attempts"))
    assert budget["window_frames"] <= 8
    assert budget["max_attempts"] <= 3
    assert 0 < budget["job_timeout_seconds"] <= 86400


@pytest.mark.parametrize("path", SPEC_PATHS, ids=lambda p: p.name)
def test_every_spec_declares_a_registered_model_alias(path: Path) -> None:
    """A spec must not rely on the runner's 9B default when it means a bigger model."""
    spec = _load(path)
    assert "model_name" in spec, f"{path.name} relies on the runner default model_name"
    assert spec["model_name"] in REGISTERED_ALIASES


def test_full_panel_specs_list_all_eight_arms() -> None:
    """Mechanism attribution (H1-H5) needs all four single-mechanism ablations."""
    for name in ("vlm-state-risk-development.yaml", "vlm-state-risk-qwen35-9b-validation.yaml"):
        methods = _load(SPEC_DIR / name)["methods"]
        assert sorted(methods) == ALL_EIGHT, f"{name} is missing arms: {sorted(set(ALL_EIGHT) - set(methods))}"


def test_h8_stack_is_the_declared_subset_and_a_different_alias() -> None:
    spec = _load(SPEC_DIR / "vlm-state-risk-qwen38-validation.yaml")
    assert sorted(spec["methods"]) == H8_SUBSET
    nine = _load(SPEC_DIR / "vlm-state-risk-qwen35-9b-validation.yaml")
    assert spec["model_name"] != nine["model_name"], (
        "the two backbone stacks must not share a model_name; otherwise the 27B ledger "
        "rows would carry the 9B identity"
    )


def test_gv06_fixture_pack_validates_and_its_schema_is_satisfiable() -> None:
    pack = validate_knowledge(yaml.safe_load(KNOWN_PACK.read_text(encoding="utf-8")))
    schema = inference_json_schema(pack)
    assert set(schema["required"]) <= set(schema["properties"])
    assert [s["id"] for s in pack["steps"]] == [
        "base_present",
        "front_chassis_installed",
        "rear_chassis_installed",
    ]
    rule = pack["risk_rules"][0]
    assert rule["required_observations"] == ["prerequisite_evidence"]
    # Deliberate divergence from the shipped pack: ["*"] closes the
    # `not_applicable` escape hatch so G-V0-6 actually exercises fail-closed.
    assert rule["applicable_stages"] == ["*"]
