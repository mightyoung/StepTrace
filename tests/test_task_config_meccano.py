from pathlib import Path

from display_project.sop.task_config import load_task_config


def test_meccano_config_compiles_without_task_specific_runtime_class():
    task = load_task_config(Path("configs/tasks/meccano.yaml"))
    assert task.task_id == "meccano"
    assert len(task.steps) == 5
    assert task.state_machine().steps == task.steps
