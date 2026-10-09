from __future__ import annotations

from pathlib import Path

from display_project.g3_statistical_input import validate_g3_statistical_input
from scripts.build_g3_full_producer_fixture import build_fixture


ROOT = Path(__file__).parents[1]


def test_full_producer_fixture_is_paired_raw_two_task_evidence_only():
    value = build_fixture(ROOT, dll_sha256="f" * 64)
    valid, reasons = validate_g3_statistical_input(value)
    assert valid, reasons
    assert len(value["opportunities"]) == 6480
    assert {row["task"] for row in value["opportunities"]} == {
        "meccano",
        "industreal",
    }
    assert {row["branch"] for row in value["opportunities"]} == {"A", "B"}
    assert {row["method"] for row in value["opportunities"]} == {
        "proposed",
        "fixed_streak",
        "hmm_or_crf",
    }
    assert all("p_value" not in row for row in value["opportunities"])
