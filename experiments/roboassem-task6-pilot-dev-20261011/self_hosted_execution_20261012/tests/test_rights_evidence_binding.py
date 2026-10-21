"""Synthetic CPU-only admission counterexamples; no media, model or human truth."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from task6_bundle import run_preflight, validate_rights_attestation  # noqa: E402

EVIDENCE = b"synthetic rights scope record"
CONTRACT = b'{"synthetic_fixture_only":true}'
PROVIDER = "synthetic-provider/host-01"


def attestation() -> dict:
    return {
        "schema_version": "steptrace.data-rights-attestation.v1",
        "experiment_id": "synthetic-only",
        "input_contract_sha256": hashlib.sha256(CONTRACT).hexdigest(),
        "authorization_status": "authorized",
        "rights_basis": "synthetic scope; no real authorization",
        "license_id": "synthetic-only",
        "evidence_reference": "synthetic-reference",
        "evidence_file": "evidence.txt",
        "evidence_bytes": len(EVIDENCE),
        "evidence_sha256": hashlib.sha256(EVIDENCE).hexdigest(),
        "inference_authorized": True,
        "transfer_authorized": True,
        "provider_id": PROVIDER,
        "expires_utc": "2999-01-01T00:00:00Z",
        "public_redistribution_authorized": False,
        "synthetic_template_only": False,
    }


def validate(record: dict, provider: str = PROVIDER) -> list[str]:
    return validate_rights_attestation(
        record, experiment_id="synthetic-only",
        input_contract_sha256=hashlib.sha256(CONTRACT).hexdigest(),
        provider_id=provider,
    )


def preflight(record: dict, evidence: bytes) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inputs = root / "inputs"
        inputs.mkdir()
        (inputs / "execution-input-contract.json").write_bytes(CONTRACT)
        (inputs / "source.synthetic.txt").write_bytes(b"synthetic provenance")
        (inputs / "evidence.txt").write_bytes(evidence)
        rights = inputs / "rights.json"
        rights.write_text(json.dumps(record), encoding="utf-8")
        manifest = {
            "experiment_id": "synthetic-only",
            "input_contract": {"bytes": len(CONTRACT), "sha256": hashlib.sha256(CONTRACT).hexdigest()},
            "inputs": {"frames": [], "source_video": {
                "filename": "source.synthetic.txt", "bytes": len(b"synthetic provenance"),
                "sha256": hashlib.sha256(b"synthetic provenance").hexdigest(),
            }},
            "runner_files": [], "environment": {}, "model": {"files": []},
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return run_preflight(
            manifest_path=manifest_path, input_root=inputs, run_root=root / "run",
            rights_path=rights, provider_id=PROVIDER, phase="inputs",
        )


class RightsEvidenceBindingTests(unittest.TestCase):
    def test_matching_synthetic_evidence_passes_only_input_gate(self) -> None:
        report = preflight(attestation(), EVIDENCE)
        self.assertTrue(report["input_ready"], report["blockers"])
        self.assertFalse(report["execution_ready"])
        self.assertEqual((report["network_calls"], report["generation_calls"]), (0, 0))

    def test_same_length_substitution_blocks_data_rights(self) -> None:
        report = preflight(attestation(), b"x" * len(EVIDENCE))
        rights = next(check for check in report["checks"] if check["name"] == "data_rights")
        self.assertEqual(rights["status"], "blocked")
        self.assertTrue(any("evidence" in issue and "mismatch" in issue for issue in rights["issues"]))
        self.assertFalse(report["input_ready"])
        self.assertFalse(report["execution_ready"])

    def test_size_substitution_blocks_data_rights(self) -> None:
        report = preflight(attestation(), EVIDENCE + b"changed")
        self.assertFalse(report["input_ready"])
        self.assertTrue(any("evidence" in issue and "mismatch" in issue for issue in report["blockers"]))

    def test_missing_or_malformed_expected_evidence_identity_blocks(self) -> None:
        for field, bad in (("evidence_bytes", None), ("evidence_bytes", True),
                           ("evidence_bytes", 0), ("evidence_bytes", "29"),
                           ("evidence_sha256", None), ("evidence_sha256", ""),
                           ("evidence_sha256", "g" * 64), ("evidence_sha256", ["a" * 64])):
            with self.subTest(field=field, value=bad):
                record = attestation()
                record[field] = bad
                self.assertTrue(any(field in issue for issue in validate(record)))

    def test_missing_identity_in_legacy_record_is_not_implicitly_authorized(self) -> None:
        record = attestation()
        record.pop("evidence_bytes")
        record.pop("evidence_sha256")
        report = preflight(record, EVIDENCE)
        self.assertFalse(report["input_ready"])

    def test_equal_placeholder_provider_cannot_establish_receiver(self) -> None:
        for provider in ("", " ", "UNSET", "unknown", "self-hosted", "自托管"):
            with self.subTest(provider=provider):
                record = attestation()
                record["provider_id"] = provider
                self.assertTrue(any("provider_id" in issue for issue in validate(record, provider)))

    def test_named_receiver_and_bound_identity_pass_structural_gate(self) -> None:
        self.assertEqual(validate(attestation()), [])
