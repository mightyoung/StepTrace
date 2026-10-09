from __future__ import annotations
import json
import subprocess
import sys
import pytest
from display_project.experiments.hmm_fit import fit_hmm_artifact
from display_project.experiments.hmm_sequence import load_hmm_artifact


def _records():
    return [
        {
            "group_hash": "a" * 64,
            "partition": "DEVELOPMENT",
            "state": "s0",
            "features": [1.0, 2.0, 0.0],
        },
        {
            "group_hash": "a" * 64,
            "partition": "DEVELOPMENT",
            "state": "s1",
            "features": [2.0, 3.0, 1.0],
        },
        {
            "group_hash": "b" * 64,
            "partition": "DEVELOPMENT",
            "state": "s0",
            "features": [1.5, 2.5, 0.2],
        },
        {
            "group_hash": "b" * 64,
            "partition": "DEVELOPMENT",
            "state": "s1",
            "features": [2.5, 3.5, 1.2],
        },
    ]


def test_fit_validates_and_loads(tmp_path):
    a = fit_hmm_artifact(
        _records(),
        task_id="task",
        task_config_hash="c" * 64,
        partition_manifest_hash="d" * 64,
        states=["s0", "s1"],
        fit_code_hash="e" * 64,
    )
    p = tmp_path / "hmm-fit-test.json"
    p.write_text(json.dumps(a), encoding="utf-8")
    assert load_hmm_artifact(p)["artifact_hash"] == a["artifact_hash"]


@pytest.mark.parametrize(
    "bad",
    [{**_records()[0], "partition": "TEST"}, {**_records()[0], "video_id": "0001"}],
)
def test_fit_rejects_bad_records(bad):
    with pytest.raises(ValueError):
        fit_hmm_artifact(
            [bad],
            task_id="task",
            task_config_hash="c" * 64,
            partition_manifest_hash="d" * 64,
            states=["s0"],
            fit_code_hash="e" * 64,
        )


def test_cli_missing_input_emits_blocker(tmp_path):
    blocker = tmp_path / "blocker.json"
    output = tmp_path / "fitted.json"
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "scripts/fit_hmm_sequence.py",
            "--output",
            str(output),
            "--blocker-output",
            str(blocker),
            "--task-id",
            "task",
            "--task-config-hash",
            "c" * 64,
            "--partition-manifest",
            str(manifest),
            "--states",
            "s0",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert json.loads(blocker.read_text())["status"] == "BLOCKED"
    assert not output.exists()
