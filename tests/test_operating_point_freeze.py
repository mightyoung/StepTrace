from display_project.metrics.paired import (
    BRANCH_B,
    PRIMARY_METHODS,
    freeze_operating_point,
)

TASKS = ("task_a", "task_b")


def _cells(coverage=0.5, fsar=0.2):
    points = [
        {
            "threshold": 0.2,
            "threshold_semantic": "confidence",
            "conservative_direction": "higher",
            "coverage": coverage,
            "fsar": fsar + 0.1,
            "emitted_advances": 1,
        },
        {
            "threshold": 0.8,
            "threshold_semantic": "confidence",
            "conservative_direction": "higher",
            "coverage": coverage,
            "fsar": fsar,
            "emitted_advances": 1,
        },
    ]
    return {(method, task): points for method in PRIMARY_METHODS for task in TASKS}


def test_branch_a_freezes_all_six_cells_and_tie_breaks():
    result = freeze_operating_point(_cells(), frozen_tasks=TASKS, target=0.5, delta_c=0)
    assert result.eligible and result.threshold == 0.8
    assert len(result.thresholds_by_cell) == 6


def test_branch_a_checks_pairwise_delta_c_not_only_cell_target():
    cells = _cells()
    cells[("proposed", "task_a")] = [
        {
            "threshold": 0.5,
            "threshold_semantic": "confidence",
            "conservative_direction": "higher",
            "coverage": 0.4,
            "fsar": 0.1,
            "emitted_advances": 1,
        }
    ]
    cells[("fixed_streak", "task_a")] = [
        {
            "threshold": 0.5,
            "threshold_semantic": "confidence",
            "conservative_direction": "higher",
            "coverage": 0.6,
            "fsar": 0.1,
            "emitted_advances": 1,
        }
    ]
    result = freeze_operating_point(cells, frozen_tasks=TASKS, target=0.5, delta_c=0.1)
    assert not result.eligible and "pairwise" in result.reason


def test_branch_a_requires_every_cell_target_match():
    cells = _cells()
    cells[("hmm_or_crf", "task_b")] = [
        {
            "threshold": 0.5,
            "threshold_semantic": "confidence",
            "conservative_direction": "higher",
            "coverage": 0.9,
            "fsar": 0.1,
            "emitted_advances": 1,
        }
    ]
    assert not freeze_operating_point(
        cells, frozen_tasks=TASKS, target=0.5, delta_c=0.01
    ).eligible


def test_branch_b_checks_target_and_pairwise_delta_r():
    cells = _cells(fsar=0.2)
    cells[("proposed", "task_a")] = [
        {
            "threshold": 0.5,
            "threshold_semantic": "confidence",
            "conservative_direction": "higher",
            "coverage": 0.5,
            "fsar": 0.1,
            "emitted_advances": 1,
        }
    ]
    cells[("fixed_streak", "task_a")] = [
        {
            "threshold": 0.5,
            "threshold_semantic": "confidence",
            "conservative_direction": "higher",
            "coverage": 0.5,
            "fsar": 0.3,
            "emitted_advances": 1,
        }
    ]
    result = freeze_operating_point(
        cells, frozen_tasks=TASKS, branch=BRANCH_B, target=0.2, delta_r=0.1
    )
    assert not result.eligible and "pairwise" in result.reason


def test_branch_b_no_point_in_tolerance_is_ineligible():
    cells = _cells(fsar=0.2)
    cells[("hmm_or_crf", "task_b")] = [
        {
            "threshold": 0.5,
            "threshold_semantic": "confidence",
            "conservative_direction": "higher",
            "coverage": 0.5,
            "fsar": 0.9,
            "emitted_advances": 1,
        }
    ]
    assert not freeze_operating_point(
        cells, frozen_tasks=TASKS, branch=BRANCH_B, target=0.2, delta_r=0.01
    ).eligible


def _branch_b_point(threshold, coverage, fsar):
    return {
        "threshold": threshold,
        "threshold_semantic": "confidence",
        "conservative_direction": "higher",
        "coverage": coverage,
        "fsar": fsar,
        "emitted_advances": 1,
    }


def test_branch_b_tie_uses_higher_coverage_before_conservative_threshold():
    cells = {}
    for task in TASKS:
        cells[("proposed", task)] = [
            _branch_b_point(0.2, 0.2, 0.25),
            _branch_b_point(0.8, 0.8, 0.75),
        ]
        for comparator in ("fixed_streak", "hmm_or_crf"):
            cells[(comparator, task)] = [_branch_b_point(0.6, 0.6, 0.75)]
    result = freeze_operating_point(
        cells,
        frozen_tasks=TASKS,
        branch=BRANCH_B,
        target=0.5,
        delta_r=0.3,
    )
    assert result.eligible
    assert result.thresholds_by_cell["proposed::task_a"] == 0.8
    assert result.tie_break == (
        "HIGHER_ESTIMATED_COVERAGE",
        "MORE_CONSERVATIVE_THRESHOLD",
    )


def test_operating_point_materializes_iterable_cells_and_rejects_fractional_counts():
    generator_cells = {
        key: (point for point in values) for key, values in _cells().items()
    }
    assert freeze_operating_point(
        generator_cells, frozen_tasks=TASKS, target=0.5, delta_c=0
    ).eligible

    invalid = _cells()
    invalid[("proposed", "task_a")] = [
        {
            **invalid[("proposed", "task_a")][0],
            "emitted_advances": 0.5,
        }
    ]
    assert not freeze_operating_point(
        invalid, frozen_tasks=TASKS, target=0.5, delta_c=0
    ).eligible
