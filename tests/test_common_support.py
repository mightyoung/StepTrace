import pytest

from display_project.metrics.paired import PRIMARY_METHODS, common_support

TASKS = ("task_a", "task_b")


def _cells():
    rows = [
        {
            "threshold": 0.2,
            "threshold_semantic": "confidence",
            "conservative_direction": "higher",
            "coverage": 0.2,
            "fsar": 0.1,
            "emitted_advances": 1,
        },
        {
            "threshold": 0.8,
            "threshold_semantic": "confidence",
            "conservative_direction": "higher",
            "coverage": 0.8,
            "fsar": 0.2,
            "emitted_advances": 1,
        },
    ]
    return {(method, task): rows for method in PRIMARY_METHODS for task in TASKS}


def test_common_support_requires_exact_named_six_cells():
    assert common_support(_cells(), frozen_tasks=TASKS) == (0.2, 0.8)


def test_common_support_rejects_missing_cell_even_if_count_replaced_by_junk():
    cells = _cells()
    cells.pop(("proposed", "task_a"))
    cells[("junk", "task_a")] = next(iter(cells.values()))
    assert common_support(cells, frozen_tasks=TASKS) is None


def test_common_support_rejects_extra_cell():
    cells = _cells()
    cells[("proposed", "task_c")] = next(iter(cells.values()))
    assert common_support(cells, frozen_tasks=TASKS) is None


def test_common_support_rejects_zero_or_undefined_cell():
    cells = _cells()
    cells[("hmm_or_crf", "task_b")] = [
        {"threshold": 0.5, "coverage": 0.5, "fsar": float("nan"), "emitted_advances": 0}
    ]
    assert common_support(cells, frozen_tasks=TASKS) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("coverage", -0.1),
        ("coverage", 1.1),
        ("fsar", -0.1),
        ("fsar", 1.1),
        ("threshold", float("nan")),
    ],
)
def test_common_support_rejects_invalid_probability_ranges(field, value):
    cells = {key: [dict(item) for item in value] for key, value in _cells().items()}
    cells[("proposed", "task_a")][0][field] = value
    assert common_support(cells, frozen_tasks=TASKS) is None
