from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "research/audits/g3-ratified-fallback-preflight-20260821.json"


def test_ratified_fallback_preflight_audit_is_non_authorizing_and_hash_bound():
    value = json.loads(AUDIT.read_text(encoding="utf-8"))
    supplied = value["artifact_sha256"]
    body = {key: item for key, item in value.items() if key != "artifact_sha256"}
    expected = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert supplied == expected
    assert value["ratification_status"] == "RATIFIED_APPLIED_TO_VERSIONED_COPIES"
    assert value["admission_bundle"]["status"] == "READY"
    assert value["g3_admission"]["status"] == "BLOCKED"
    assert value["g3_admission"]["downstream_authorized"] is False
    assert value["task_specific_producer_replay"]["consumer_validation"] == "PASS"
    assert value["g3_execution_authorized"] is False
