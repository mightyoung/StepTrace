import shutil
from pathlib import Path

from scripts.validate_vlm_state_risk_protocol import validate


ROOT = Path(__file__).resolve().parents[1]


def _copy_tree(source: Path, target: Path) -> None:
    """Mirror `source` into `target`, recursing into subdirectories.

    The protocol directory is not flat: it holds `validation-stage/` alongside the
    frozen YAML/MD files, so a plain `read_bytes()` per entry hits a directory.
    """
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination)
        else:
            destination.write_bytes(item.read_bytes())


def test_vlm_state_risk_protocol_is_development_only_and_preserves_old_hashes():
    assert validate(ROOT) == []


def test_vlm_state_risk_protocol_rejects_changed_legacy_file(tmp_path):
    target = tmp_path / "research" / "vlm-state-risk-20260907"
    target.mkdir(parents=True)
    _copy_tree(ROOT / "research" / "vlm-state-risk-20260907", target)
    for legacy in ("claim-freeze.yaml", "analysis-plan.md", "deviation-coverage-audit.yaml"):
        path = tmp_path / "research" / legacy
        path.parent.mkdir(parents=True, exist_ok=True)
        original = ROOT / "research" / legacy
        path.write_bytes(original.read_bytes())
    checklist = ROOT / ".omx" / "plans" / "computers-in-industry-submission-checklist.json"
    destination = tmp_path / ".omx" / "plans"
    destination.mkdir(parents=True)
    (destination / checklist.name).write_bytes(checklist.read_bytes())
    (tmp_path / "research" / "analysis-plan.md").write_text("changed", encoding="utf-8")
    assert any("old frozen hash changed" in item for item in validate(tmp_path))
