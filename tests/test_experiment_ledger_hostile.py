import json

from scripts.validate_experiment_ledger import validate


def test_non_fixture_omitted_record_kind_and_attempt_identity_cannot_bypass_registration(tmp_path):
    path = tmp_path / "attempts.jsonl"
    path.write_text(json.dumps({"experiment_profile": "DEVELOPMENT_ONLY"}) + "\n", encoding="utf-8")
    errors = validate(path)
    assert any("record_kind must be attempt_outcome" in error for error in errors)
    assert any("attempt_identity" in error for error in errors)
