from tests._evidence_helpers import authorized
import pytest

from display_project.sop.task_config import TaskConfigError, compile_task_config


def test_compiles_versioned_steps_and_orders_dependencies():
    task = compile_task_config(
        {
            "schema_version": 1,
            "task_id": "demo",
            "steps": [
                {"id": "finish", "depends_on": ["middle"]},
                {"id": "start"},
                {"id": "middle", "depends_on": ["start"]},
            ],
        }
    )
    assert task.schema_version == 1
    assert task.task_id == "demo"
    assert task.steps == ("start", "middle", "finish")


@pytest.mark.parametrize(
    "config",
    [
        {"schema_version": 2, "task_id": "x", "steps": ["a"]},
        {"version": 0, "task_id": "x", "steps": ["a"]},
        {"task_id": "x", "steps": ["a"]},
    ],
)
def test_rejects_unsupported_or_missing_schema_version(config):
    with pytest.raises(TaskConfigError, match="unsupported schema_version"):
        compile_task_config(config)


@pytest.mark.parametrize(
    "mutation",
    [
        {"allowed_classes": ["hand", 1]},
        {"class_aliases": {"hand": ["gripper", 1]}},
        {"detector_prompts": ["hand", 1]},
        {"steps": [{"id": "a", "prompts": ["part", 1]}]},
    ],
)
def test_rejects_non_string_class_and_prompt_entries(mutation):
    config = {"schema_version": 1, "task_id": "x", "steps": ["a"]}
    config.update(mutation)
    with pytest.raises(TaskConfigError):
        compile_task_config(config)


def test_rejects_duplicate_step_ids():
    with pytest.raises(TaskConfigError, match="duplicate step id"):
        compile_task_config({"schema_version": 1, "task_id": "x", "steps": ["a", "a"]})


def test_rejects_dangling_dependencies():
    with pytest.raises(TaskConfigError, match="dangling dependency"):
        compile_task_config(
            {
                "schema_version": 1,
                "task_id": "x",
                "steps": [{"id": "a", "depends_on": ["missing"]}],
            }
        )


def test_rejects_dependency_cycles():
    with pytest.raises(TaskConfigError, match="contain a cycle"):
        compile_task_config(
            {
                "schema_version": 1,
                "task_id": "x",
                "steps": [
                    {"id": "a", "depends_on": ["b"]},
                    {"id": "b", "depends_on": ["a"]},
                ],
            }
        )


def test_compiled_task_uses_shared_runtime_for_arbitrary_step_count():
    task = compile_task_config(
        {"schema_version": 1, "task_id": "x", "steps": ["one", "two", "three"]}
    )
    machine = task.state_machine()
    assert machine.steps == ("one", "two", "three")
    assert machine.observe(authorized("one")).decision == "normal"


def test_state_machine_default_remains_five_step_baseline():
    from display_project.sop.state_machine import SOPStateMachine

    assert SOPStateMachine().steps == tuple(f"step_{i}" for i in range(1, 6))
