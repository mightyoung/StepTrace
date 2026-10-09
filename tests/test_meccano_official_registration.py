import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADMITTED_PROJECT_SPLITS = {
    "DEVELOPMENT",
}


def _ledger():
    with (ROOT / "research/dataset-ledger.csv").open(
        encoding="utf-8-sig", newline=""
    ) as f:
        return list(csv.DictReader(f))


def test_official_registration_has_exactly_twenty_canonical_roots():
    rows = [
        r
        for r in _ledger()
        if r["source_dataset"] == "MECCANO"
        and r["record_type"] == "CANONICAL_ROOT_VIDEO"
    ]
    assert {r["asset_id"] for r in rows} == {f"MECCANO-{i:04d}" for i in range(1, 21)}


def test_0009_is_single_frozen_tombstone_row():
    rows = [r for r in _ledger() if r["asset_id"] == "MECCANO-0009"]
    assert len(rows) == 1
    row = rows[0]
    assert row["tombstone_id"] == "TOMBSTONE-MECCANO-0009"
    assert row["project_split"] == "DEVELOPMENT_ONLY_CONTAMINATED"
    assert row["source_path"] == "data/meccano/subset/0009.mp4"


def test_anonymous_identity_admits_all_non_tombstoned_roots():
    rows = [
        r
        for r in _ledger()
        if r["source_dataset"] == "MECCANO"
        and r["record_type"] == "CANONICAL_ROOT_VIDEO"
        and r["asset_id"] != "MECCANO-0009"
    ]
    assert len(rows) == 19
    assert {r["operator_id"] for r in rows} == {
        f"MECCANO-P-{i:02d}" for i in range(1, 21) if i != 9
    }
    assert {r["complete_job_id"] for r in rows} == {
        f"MECCANO-ASSEMBLY-{i:02d}" for i in range(1, 21) if i != 9
    }
    assert all(r["error_types"] == "UNKNOWN" for r in rows)
    assert all(r["project_split"] != "UNASSIGNED_BLOCKED_IDENTITY" for r in rows)
    assert all(
        r["formal_use_allowed"] == "NO" and r["independent_unit_eligible"] == "YES"
        for r in rows
    )


def test_manifest_join_and_split_unit_are_root_locked():
    data = json.loads(
        (ROOT / "research/split-manifest.json").read_text(encoding="utf-8")
    )
    assert data["schema_version"] == "1.1.0"
    assert data["split_unit"] == [
        "canonical_root_id",
        "operator_id",
        "complete_job_id",
        "video_id",
    ]
    records = [r for r in data["records"] if r["source_dataset"] == "MECCANO"]
    assert len(records) == 20
    assert len({r["video_id"] for r in records}) == 20
    assert all(r["canonical_root_id"] == r["asset_id"] for r in records)
    assert all(
        r["project_split"]
        in {"DEVELOPMENT_ONLY_CONTAMINATED", *ADMITTED_PROJECT_SPLITS}
        for r in records
    )
    assert all(
        r["project_split"] != "UNASSIGNED_BLOCKED_IDENTITY"
        for r in records
        if r["video_id"] != "0009"
    )
    assert records[-1]["video_id"] == "0020"
    assert data["dataset_identity_status"]["MECCANO"] == "READY"
    assert data["status_scope"] == "GLOBAL_OPERATOR_LEVEL_IDENTITY"
    assert data["g2_admission_scope"] == (
        "MECCANO_FORMAL_CANDIDATE_PLUS_INDUSTREAL_ACTION_STEP_STATE_ONLY"
    )
    assert "does not override dataset-scoped MECCANO READY" in data[
        "status_interpretation"
    ]


def test_current_audit_matches_versioned_manifest():
    manifest = json.loads(
        (ROOT / "research/split-manifest.json").read_text(encoding="utf-8")
    )
    current = json.loads(
        (ROOT / "research/audits/split-manifest-current.json").read_text(
            encoding="utf-8"
        )
    )
    assert current == manifest


def test_anonymous_identity_provenance_is_hash_bound_and_narrowly_scoped():
    path = ROOT / "research/audits/meccano-identity-provenance.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    artifact_hash = payload.pop("artifact_sha256")
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert artifact_hash == hashlib.sha256(canonical).hexdigest()
    assert payload["status"] == "VERIFIED_ANONYMOUS_SEQUENCE_IDENTITY"
    assert payload["local_inventory"]["canonical_video_count"] == 20
    assert payload["mapping_policy"]["personal_identity_claimed"] is False
    assert payload["mapping_policy"]["demographic_identity_claimed"] is False
    assert payload["tombstone_0009_preserved"] is True


def test_registration_preserves_non_meccano_and_is_idempotent(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "register_meccano_official", ROOT / "scripts/register_meccano_official.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ledger = tmp_path / "dataset-ledger.csv"
    ledger.write_bytes((ROOT / "research/dataset-ledger.csv").read_bytes())
    fieldnames, _ = module._load_rows(ledger)
    with ledger.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, quoting=csv.QUOTE_ALL, lineterminator="\n"
        )
        writer.writerow(
            {
                key: (
                    "INDUSTREAL-KEEP"
                    if key == "asset_id"
                    else "IndustReal"
                    if key == "source_dataset"
                    else "CANONICAL_ROOT_VIDEO"
                    if key == "record_type"
                    else "UNKNOWN"
                )
                for key in fieldnames
            }
        )
    manifest = tmp_path / "split-manifest.json"
    manifest.write_bytes((ROOT / "research/split-manifest.json").read_bytes())
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["records"].insert(
        0,
        {
            "asset_id": "INDUSTREAL-KEEP",
            "source_dataset": "IndustReal",
            "video_id": "KEEP",
        },
    )
    manifest.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    current = tmp_path / "current.json"
    kwargs = {
        "inventory_path": module.INVENTORY,
        "action_root": module.ACTION_ROOT,
        "ledger_path": ledger,
        "manifest_path": manifest,
        "current_audit_path": current,
        "identity_provenance_path": tmp_path / "identity-provenance.json",
    }
    module.register(**kwargs)
    first = {
        "ledger": ledger.read_bytes(),
        "manifest": manifest.read_bytes(),
        "current": current.read_bytes(),
    }
    module.register(**kwargs)
    assert {
        "ledger": ledger.read_bytes(),
        "manifest": manifest.read_bytes(),
        "current": current.read_bytes(),
    } == first
    assert b"INDUSTREAL-KEEP" in first["ledger"]
    assert b"INDUSTREAL-KEEP" in first["manifest"]
    assert first["manifest"] == first["current"]
    assert b"\r\n" not in first["ledger"] + first["manifest"] + first["current"]


def test_registration_transaction_rolls_back_on_second_and_third_replace(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "register_meccano_official", ROOT / "scripts/register_meccano_official.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for fail_at in (2, 3):
        case = tmp_path / f"fail-{fail_at}"
        case.mkdir()
        ledger = case / "dataset-ledger.csv"
        ledger.write_bytes((ROOT / "research/dataset-ledger.csv").read_bytes())
        manifest = case / "split-manifest.json"
        manifest.write_bytes((ROOT / "research/split-manifest.json").read_bytes())
        current = case / "current.json"
        current.write_bytes(manifest.read_bytes())
        before = {p: p.read_bytes() for p in (ledger, manifest, current)}
        real_replace = module.os.replace
        calls = 0

        def fail_once(source, target):
            nonlocal calls
            calls += 1
            if calls == fail_at:
                raise OSError("injected replace failure")
            return real_replace(source, target)

        module.os.replace = fail_once
        try:
            try:
                module.register(
                    ledger_path=ledger,
                    manifest_path=manifest,
                    current_audit_path=current,
                    identity_provenance_path=case / "identity-provenance.json",
                )
            except OSError:
                pass
            else:
                raise AssertionError("injected replace failure was not raised")
        finally:
            module.os.replace = real_replace
        assert {p: p.read_bytes() for p in (ledger, manifest, current)} == before
        assert not list(case.glob(".*.tmp"))
