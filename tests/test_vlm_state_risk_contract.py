from __future__ import annotations

import json

import pytest

from display_project.vlm_sop.state_risk_contract import (
    StateRiskContractError,
    inference_json_schema,
    validate_inference,
)
from display_project.vlm_sop.state_risk_knowledge import (
    StateRiskKnowledgeError,
    load_knowledge,
    validate_knowledge,
)


def _knowledge():
    return {
        "schema_version": "1.0",
        "task_id": "task-a",
        "steps": [
            {
                "id": "pick",
                "title": "Pick component",
                "prerequisites": [],
                "completion_cues": ["component held"],
                "negative_cues": ["component remains on bench"],
                "required_observations": ["hand_component_relation"],
                "source_refs": ["sop-1#pick"],
            },
            {
                "id": "install",
                "title": "Install component",
                "prerequisites": ["pick"],
                "completion_cues": ["component seated"],
                "negative_cues": ["component misaligned"],
                "required_observations": ["component_alignment"],
                "source_refs": ["sop-1#install"],
            },
        ],
        "risk_rules": [
            {
                "id": "misalignment",
                "category": "quality",
                "applicable_stages": ["install"],
                "trigger_conditions": ["component visibly misaligned"],
                "exceptions": [],
                "required_observations": ["component_alignment"],
                "severity": "moderate",
                "severity_source": "sop-1#risk",
                "source_refs": ["sop-1#risk"],
            },
            {
                "id": "hand_contact",
                "category": "safety",
                "applicable_stages": ["*"],
                "trigger_conditions": ["hand enters configured zone"],
                "exceptions": ["machine isolated"],
                "required_observations": ["hand_zone_relation"],
                "severity": "high",
                "severity_source": "sop-1#risk",
                "source_refs": ["sop-1#risk"],
            },
        ],
        "deviation_types": ["prerequisite_violation"],
        "source_refs": ["sop-1#pick", "sop-1#install", "sop-1#risk"],
        "status": "SYNTHETIC_SOFTWARE_TEST_ONLY",
    }


def _window(*, view="complete", missing=None):
    return {
        "schema_version": "1.0",
        "task_id": "task-a",
        "window_id": "window-1",
        "decision_time": 2.0,
        "frame_refs": [{"id": "frame-1", "time": 1.0, "image_sha256": "d" * 64, "image_verified": True}],
        "object_track_refs": [{"id": "track-1", "time": 1.5}],
        "relation_events": [{"id": "relation-1", "time": 1.8}],
        "observability": {
            "view": view,
            "missing": missing or [],
            "absence_confirmed": [],
        },
        "hashes": {"source_sha256": "a" * 64, "model_sha256": "b" * 64, "config_sha256": "c" * 64},
    }


def _inference():
    return {
        "schema_version": "1.0",
        "task_id": "task-a",
        "window_id": "window-1",
        "estimated_stage": "install",
        "completion_state": "in_progress",
        "deviations": [{"type_id": "prerequisite_violation", "evidence_refs": ["frame-1"]}],
        "risk_assessments": [
            {"rule_id": "misalignment", "status": "triggered", "evidence_refs": ["relation-1"], "missing_evidence": []},
            {"rule_id": "hand_contact", "status": "not_triggered", "evidence_refs": ["frame-1"], "missing_evidence": []},
        ],
        "evidence_refs": ["frame-1", "relation-1"],
        "conflicting_evidence_refs": [],
        "brief_rationale": "The visible alignment relation supports the configured rule.",
        "authority": "state_estimation_only",
    }


def test_knowledge_is_normalized_and_does_not_alias_input():
    raw = _knowledge()
    result = validate_knowledge(raw)
    raw["steps"][0]["title"] = "mutated"
    assert result["steps"][0]["title"] == "Pick component"


def test_semantic_uncertainty_is_allowed_even_with_complete_observations():
    inference = _inference()
    inference["risk_assessments"][0].update(status="unknown", evidence_refs=[],
                                           missing_evidence=["visual interpretation ambiguous"])
    checked = validate_inference(inference, knowledge=_knowledge(), window=_window())
    assert checked["risk_assessments"][0]["status"] == "unknown"


@pytest.mark.parametrize(
    "mutation, match",
    [
        (lambda value: value.update(authority="state_writer"), "fields mismatch"),
        (lambda value: value["steps"][1]["prerequisites"].append("missing-step"), "unknown prerequisites"),
        (lambda value: value["risk_rules"][0].update(applicable_stages=["missing-stage"]), "unknown stages"),
        (lambda value: value["risk_rules"][0].update(severity_source="unbound-source"), "severity_source"),
        (lambda value: value.update(status="FORMAL_RESULT"), "unsupported knowledge status"),
        (lambda value: value["steps"][0].update(title="Ground truth step"), "forbidden truth"),
        (lambda value: value["steps"][0].update(prerequisites=["install"]), "acyclic"),
    ],
)
def test_knowledge_fails_closed(mutation, match):
    raw = _knowledge()
    mutation(raw)
    with pytest.raises(StateRiskKnowledgeError, match=match):
        validate_knowledge(raw)


def test_inference_accepts_exact_rule_coverage_and_valid_references():
    result = validate_inference(_inference(), knowledge=_knowledge(), window=_window())
    assert result["estimated_stage"] == "install"
    assert [item["rule_id"] for item in result["risk_assessments"]] == ["misalignment", "hand_contact"]
    assert result["authority"] == "state_estimation_only"


def test_missing_required_observation_requires_unknown_and_names_gap():
    raw = _inference()
    raw["risk_assessments"][0] = {
        "rule_id": "misalignment",
        "status": "unknown",
        "evidence_refs": [],
        "missing_evidence": ["component_alignment"],
    }
    result = validate_inference(
        raw,
        knowledge=_knowledge(),
        window=_window(missing=["component_alignment"]),
    )
    assert result["risk_assessments"][0]["status"] == "unknown"


def test_determinate_stage_and_completion_require_supporting_references():
    raw = _inference()
    raw["evidence_refs"] = []
    with pytest.raises(StateRiskContractError, match="stage or completion requires evidence_refs"):
        validate_inference(raw, knowledge=_knowledge(), window=_window())


def test_declared_missing_tightening_outcome_prevents_completed_state():
    knowledge = _knowledge()
    knowledge["steps"][1].update(
        id="tighten_screw",
        required_observations=["tool_connection_relation", "verified_tightening_outcome"],
    )
    knowledge["risk_rules"][0]["applicable_stages"] = ["tighten_screw"]
    raw = _inference()
    raw.update(estimated_stage="tighten_screw", completion_state="completed")
    with pytest.raises(StateRiskContractError, match="completed state requires all stage observations"):
        validate_inference(
            raw,
            knowledge=knowledge,
            window=_window(missing=["verified_tightening_outcome"]),
        )


def test_triggered_and_not_triggered_risk_conclusions_require_supporting_references():
    for status in ("triggered", "not_triggered"):
        raw = _inference()
        raw["risk_assessments"][0].update(status=status, evidence_refs=[])
        with pytest.raises(StateRiskContractError, match="requires evidence_refs"):
            validate_inference(raw, knowledge=_knowledge(), window=_window())


def test_nested_conclusion_references_must_be_declared_as_inference_support():
    raw = _inference()
    raw["evidence_refs"] = ["frame-1"]
    with pytest.raises(StateRiskContractError, match="included in inference evidence_refs"):
        validate_inference(raw, knowledge=_knowledge(), window=_window())


def test_unknown_stage_does_not_turn_scoped_rule_into_not_applicable():
    raw = _inference()
    raw.update(estimated_stage=None, completion_state="unknown", evidence_refs=["frame-1"], deviations=[])
    raw["risk_assessments"][0] = {
        "rule_id": "misalignment", "status": "unknown", "evidence_refs": [],
        "missing_evidence": ["estimated_stage"],
    }
    result = validate_inference(raw, knowledge=_knowledge(), window=_window())
    assert result["risk_assessments"][0]["status"] == "unknown"


def test_contract_validates_real_evidence_window_shape():
    window = _window()
    window["observability"] = {"view": "complete", "missing": []}
    with pytest.raises(StateRiskContractError, match="invalid evidence window"):
        validate_inference(_inference(), knowledge=_knowledge(), window=window)


@pytest.mark.parametrize(
    "mutation, window, match",
    [
        (lambda value: value["risk_assessments"].pop(), _window(), "every configured risk rule"),
        (lambda value: value["risk_assessments"].append(dict(value["risk_assessments"][0])), _window(), "unknown or duplicate"),
        (lambda value: value.update(evidence_refs=["future-or-oracle-ref"]), _window(), "unknown evidence"),
        (lambda value: value.update(execution_state="released"), _window(), "forbidden authority field"),
        (lambda value: None, _window(view="occluded"), "cannot be not_triggered"),
    ],
)
def test_inference_rejects_incomplete_rules_bad_refs_and_authority_escalation(mutation, window, match):
    raw = _inference()
    mutation(raw)
    with pytest.raises(StateRiskContractError, match=match):
        validate_inference(raw, knowledge=_knowledge(), window=window)


def test_bound_schema_and_static_schema_are_strict():
    schema = inference_json_schema(_knowledge())
    assert schema["additionalProperties"] is False
    assert schema["properties"]["task_id"] == {"const": "task-a"}
    assert schema["properties"]["estimated_stage"]["enum"] == ["pick", "install", None]
    assert schema["properties"]["risk_assessments"]["minItems"] == 2
    static_schema = json.loads(
        (pytest.importorskip("pathlib").Path(__file__).parents[1] / "configs/vlm/qwen_state_risk.schema.json").read_text(encoding="utf-8")
    )
    assert static_schema["properties"]["authority"] == {"const": "state_estimation_only"}


def test_bound_schema_defines_every_required_property():
    """A schema listing a key in `required` but not in `properties` is unsatisfiable
    under `additionalProperties: false` — the model then can never emit a valid object.
    Regression: the four top-level fields used to be nested inside `risk_assessments`."""
    packs = [_knowledge()]
    root = pytest.importorskip("pathlib").Path(__file__).parents[1]
    for path in sorted((root / "configs/vlm/state_risk").glob("*.yaml")):
        packs.append(load_knowledge(path))
    for pack in packs:
        schema = inference_json_schema(pack)
        required = set(schema["required"])
        properties = set(schema["properties"])
        assert required <= properties, (
            f"task_id={pack.get('task_id')!r}: required keys missing from properties: "
            f"{sorted(required - properties)}"
        )
        assert required == set(inference_json_schema(_knowledge())["required"])
        for group in ("deviations", "risk_assessments"):
            item = schema["properties"][group]["items"]
            assert set(item["required"]) <= set(item["properties"])
            assert item["additionalProperties"] is False


def test_bound_schema_accepts_a_contract_correct_object():
    """End-to-end: the generated schema must accept an object that validate_inference accepts."""
    jsonschema = pytest.importorskip("jsonschema")
    knowledge = _knowledge()
    inference = validate_inference(_inference(), knowledge=knowledge, window=_window())
    errors = list(jsonschema.Draft202012Validator(inference_json_schema(knowledge)).iter_errors(inference))
    assert errors == [], [f"{list(e.path)}: {e.message}" for e in errors]


def test_shipped_knowledge_packs_are_explicitly_non_formal_and_valid():
    root = pytest.importorskip("pathlib").Path(__file__).parents[1]
    packs = sorted((root / "configs/vlm/state_risk").glob("*.yaml"))
    assert {path.name for path in packs} == {
        "meccano-development.yaml", "software-fixture.yaml"
    }
    assert {load_knowledge(path)["status"] for path in packs} == {
        "DEVELOPMENT_ONLY", "SYNTHETIC_SOFTWARE_TEST_ONLY"
    }
    assert all("meccano" not in load_knowledge(path)["task_id"] for path in packs)
