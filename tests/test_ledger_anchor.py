import hashlib
import json

from display_project.experiments.paired_runner import append_jsonl_atomic
from scripts.ledger_anchor import make_anchor, validate_anchor, write_anchor
from scripts.validate_experiment_ledger import validate


def test_anchor_binds_tail_count_path_and_hash(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    anchor = tmp_path / "ledger.anchor.json"
    append_jsonl_atomic(ledger, [{"value": 1}, {"value": 2}])
    write_anchor(ledger, anchor)
    expected = hashlib.sha256(anchor.read_bytes()).hexdigest()
    assert validate_anchor(ledger, anchor) == []
    assert validate(ledger, anchor_path=anchor, expected_anchor_hash=expected)
    assert "externally frozen expected hash" in "\n".join(
        validate(ledger, anchor_path=anchor)
    )


def test_anchor_missing_replacement_and_rebuild_fail_closed(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    anchor = tmp_path / "ledger.anchor.json"
    append_jsonl_atomic(ledger, [{"value": 1}])
    write_anchor(ledger, anchor)
    expected = hashlib.sha256(anchor.read_bytes()).hexdigest()
    assert validate_anchor(ledger, anchor, expected_anchor_hash=expected) == []
    anchor.write_text(json.dumps(make_anchor(ledger)) + "\n", encoding="utf-8")
    assert validate_anchor(ledger, anchor, expected_anchor_hash=expected)
    anchor.unlink()
    assert validate(ledger, anchor_path=anchor)


def test_anchor_detects_rebuilt_chain_and_row_count_change(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    anchor = tmp_path / "ledger.anchor.json"
    append_jsonl_atomic(ledger, [{"value": 1}])
    write_anchor(ledger, anchor)
    append_jsonl_atomic(ledger, [{"value": 2}])
    assert validate_anchor(ledger, anchor)
