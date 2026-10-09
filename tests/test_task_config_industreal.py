from pathlib import Path

from display_project.sop.task_config import load_task_config


def test_industreal_config_compiles_with_same_shared_runtime():
    task = load_task_config(Path("configs/tasks/industreal.yaml"))
    assert task.task_id == "industreal"
    assert len(task.steps) == 4
    machine = task.state_machine()
    assert machine.steps == task.steps
    assert machine.__class__.__name__ == "SOPStateMachine"
