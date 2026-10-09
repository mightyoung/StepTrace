import pytest

from display_project.evidence.negative import NegativeEvidence, should_abstain


def test_negative_evidence_requires_reason():
    with pytest.raises(ValueError):
        NegativeEvidence("")


def test_negative_evidence_forces_abstention_for_object_and_mapping_forms():
    assert should_abstain([NegativeEvidence("missing screw")]) is True
    assert should_abstain([{"negative": True}]) is True
    assert should_abstain([{"abstain": True}]) is True


def test_positive_evidence_does_not_abstain():
    assert should_abstain([{"source": "detector", "confidence": .9}]) is False


def test_negative_mapping_false_values_do_not_abstain():
    assert should_abstain([{"negative": False, "abstain": False}]) is False
