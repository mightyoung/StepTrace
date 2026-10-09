from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from display_project.pre_g3_authority import (
    build_authority_envelope,
    build_dirty_tree_receipt,
    build_external_attestation,
    validate_authority_envelope,
    validate_dirty_tree_receipt,
)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "src").mkdir()
    (root / "src/core.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "notes.txt").write_text("user note\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=root,
        check=True,
    )
    return root


def _status(root: Path) -> bytes:
    return subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


def test_receipt_preserves_and_validates_dirty_user_worktree(tmp_path: Path):
    root = _repo(tmp_path)
    (root / "src/core.py").write_text("VALUE = 2\n", encoding="utf-8")
    (root / "scratch.txt").write_text("scratch\n", encoding="utf-8")
    before = _status(root)
    receipt = build_dirty_tree_receipt(
        root,
        included_paths=["src"],
        excluded_paths=["scratch.txt"],
    )
    after = _status(root)
    valid, reasons = validate_dirty_tree_receipt(root, receipt)
    assert valid, reasons
    assert before == after
    assert receipt["included_entries"][0]["path"] == "src/core.py"
    assert receipt["excluded_entries"][0]["path"] == "scratch.txt"


def test_tracked_mutation_after_receipt_is_detected(tmp_path: Path):
    root = _repo(tmp_path)
    (root / "src/core.py").write_text("VALUE = 2\n", encoding="utf-8")
    receipt = build_dirty_tree_receipt(
        root, included_paths=["src"], excluded_paths=[]
    )
    (root / "src/core.py").write_text("VALUE = 3\n", encoding="utf-8")
    valid, reasons = validate_dirty_tree_receipt(root, receipt)
    assert valid is False
    assert "TRACKED_BINARY_PATCH_SHA256_MISMATCH" in reasons
    assert "INCLUDED_ENTRIES_MISMATCH" in reasons


def test_untracked_mutation_after_receipt_is_detected(tmp_path: Path):
    root = _repo(tmp_path)
    target = root / "new.py"
    target.write_text("A = 1\n", encoding="utf-8")
    receipt = build_dirty_tree_receipt(
        root, included_paths=["new.py"], excluded_paths=[]
    )
    target.write_text("A = 2\n", encoding="utf-8")
    valid, reasons = validate_dirty_tree_receipt(root, receipt)
    assert valid is False
    assert "UNTRACKED_INVENTORY_SHA256_MISMATCH" in reasons
    assert "INCLUDED_ENTRIES_MISMATCH" in reasons


def test_unclassified_dirty_paths_fail_closed(tmp_path: Path):
    root = _repo(tmp_path)
    (root / "new.py").write_text("A = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="DIRTY_PATH_UNCLASSIFIED"):
        build_dirty_tree_receipt(root, included_paths=["src"], excluded_paths=[])


def test_included_and_excluded_paths_cannot_overlap(tmp_path: Path):
    root = _repo(tmp_path)
    with pytest.raises(ValueError, match="DIRTY_PATH_OWNERSHIP_OVERLAP"):
        build_dirty_tree_receipt(
            root,
            included_paths=["src"],
            excluded_paths=["src/generated"],
        )


def test_traversal_and_case_collisions_are_rejected(tmp_path: Path):
    root = _repo(tmp_path)
    with pytest.raises(ValueError, match="DIRTY_PATH_INVALID"):
        build_dirty_tree_receipt(
            root, included_paths=["../src"], excluded_paths=[]
        )
    with pytest.raises(ValueError, match="DIRTY_PATH_CASE_COLLISION"):
        build_dirty_tree_receipt(
            root, included_paths=["src", "SRC"], excluded_paths=[]
        )


def _envelope(tmp_path: Path):
    root = _repo(tmp_path)
    (root / "src/core.py").write_text("VALUE = 2\n", encoding="utf-8")
    receipt = build_dirty_tree_receipt(
        root, included_paths=["src"], excluded_paths=[]
    )
    envelope = build_authority_envelope(
        logical_id="authority:pre-g3-envelope",
        implementation_commit={
            "commit_sha": receipt["base_head"],
            "tree_sha": receipt["base_tree"],
            "implementation_branch_id": "codex/pre-g3-hardening",
        },
        environment_commitment={
            "logical_id": "environment:pre-g3-runtime",
            "schema_version": "environment-lock-1.0.0",
            "sha256": "a" * 64,
        },
        evidence_bundle_commitment=[
            {
                "logical_id": "gate:g1-g2-current",
                "schema_version": "g1-g2-joint-gate-1.1.0",
                "sha256": "b" * 64,
                "source_timestamp": "2026-08-20T12:00:00Z",
                "lineage": None,
            }
        ],
        dirty_tree_receipt=receipt,
    )
    return root, envelope


def test_authority_envelope_separates_external_attestation(tmp_path: Path):
    _, envelope = _envelope(tmp_path)
    assert "attestation" not in envelope
    valid, reasons = validate_authority_envelope(envelope)
    assert valid, reasons
    attestation = build_external_attestation(
        logical_id="attestation:pre-g3-envelope-review",
        envelope=envelope,
        reviewer_role="independent_verifier",
        attested_at="2026-08-20T13:00:00Z",
        nonce="0123456789abcdef",
        storage_logical_id="handoff:external-attestations/pre-g3",
    )
    valid, reasons = validate_authority_envelope(envelope, attestation)
    assert valid, reasons


def test_coordinated_envelope_rehash_cannot_replace_external_attestation(
    tmp_path: Path,
):
    _, envelope = _envelope(tmp_path)
    attestation = build_external_attestation(
        logical_id="attestation:pre-g3-envelope-review",
        envelope=envelope,
        reviewer_role="architect_reviewer",
        attested_at="2026-08-20T13:00:00Z",
        nonce="0123456789abcdef",
        storage_logical_id="handoff:external-attestations/pre-g3",
    )
    envelope["environment_commitment"]["sha256"] = "c" * 64
    envelope["artifact_sha256"] = "d" * 64
    valid, reasons = validate_authority_envelope(envelope, attestation)
    assert valid is False
    assert "AUTHORITY_ENVELOPE_HASH_INVALID" in reasons
    assert "ATTESTATION_ENVELOPE_BINDING_INVALID" in reasons


def test_embedded_attestation_and_same_storage_identity_fail_closed(tmp_path: Path):
    _, envelope = _envelope(tmp_path)
    with pytest.raises(ValueError, match="STORAGE_NOT_EXTERNAL"):
        build_external_attestation(
            logical_id="attestation:pre-g3-envelope-review",
            envelope=envelope,
            reviewer_role="independent_verifier",
            attested_at="2026-08-20T13:00:00Z",
            nonce="0123456789abcdef",
            storage_logical_id="authority:pre-g3-envelope",
        )
    envelope["attestation"] = {"self": True}
    valid, reasons = validate_authority_envelope(envelope)
    assert valid is False
    assert "AUTHORITY_ENVELOPE_SCHEMA_KEYS_INVALID" in reasons
    assert "EMBEDDED_SELF_ATTESTATION_FORBIDDEN" in reasons


def test_evidence_logical_id_case_collision_is_rejected(tmp_path: Path):
    root = _repo(tmp_path)
    receipt = build_dirty_tree_receipt(root, included_paths=[], excluded_paths=[])
    evidence = [
        {
            "logical_id": name,
            "schema_version": "gate-1.0.0",
            "sha256": digest * 64,
            "source_timestamp": "2026-08-20T12:00:00Z",
            "lineage": None,
        }
        for name, digest in (("gate:Current", "a"), ("gate:current", "b"))
    ]
    with pytest.raises(ValueError, match="EVIDENCE_BUNDLE_COMMITMENT_INVALID"):
        build_authority_envelope(
            logical_id="authority:pre-g3-envelope",
            implementation_commit={
                "commit_sha": receipt["base_head"],
                "tree_sha": receipt["base_tree"],
                "implementation_branch_id": "codex/pre-g3-hardening",
            },
            environment_commitment={
                "logical_id": "environment:pre-g3-runtime",
                "schema_version": "environment-lock-1.0.0",
                "sha256": "c" * 64,
            },
            evidence_bundle_commitment=evidence,
            dirty_tree_receipt=receipt,
        )
