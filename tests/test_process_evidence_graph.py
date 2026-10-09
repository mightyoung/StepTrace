import pytest
from display_project.evidence.graph import ProcessEvidenceGraph


def valid():
    return ProcessEvidenceGraph(
        (
            {"id": "ctx", "kind": "context"},
            {"id": "pre", "kind": "step_state", "step": "START", "frame": 0},
            {"id": "cand", "kind": "step_state", "step": "a", "frame": 0},
            {
                "id": "rel",
                "kind": "relation",
                "entity": "t",
                "predicate": "p",
                "frame": 0,
            },
            {
                "id": "rel2",
                "kind": "relation",
                "entity": "t",
                "predicate": "p",
                "frame": 1,
            },
        ),
        (
            {
                "source": "pre",
                "target": "cand",
                "kind": "transition",
                "task_steps": ["START", "a"],
            },
            {"source": "ctx", "target": "rel", "kind": "observation"},
            {"source": "rel", "target": "cand", "kind": "dependency"},
            {"source": "rel", "target": "rel2", "kind": "persistence"},
        ),
    )


def test_immutable_and_canonical_digest():
    g = valid()
    assert g.replay()
    assert (
        g.digest()
        == ProcessEvidenceGraph(
            tuple(reversed(g.nodes)), tuple(reversed(g.edges))
        ).digest()
    )


def test_duplicate_dangling_unknown_and_direction_fail():
    with pytest.raises(ValueError, match="duplicate"):
        ProcessEvidenceGraph(
            ({"id": "a", "kind": "context"}, {"id": "a", "kind": "context"}), ()
        ).replay()
    with pytest.raises(ValueError, match="dangling"):
        ProcessEvidenceGraph(
            ({"id": "a", "kind": "context"},),
            ({"source": "a", "target": "x", "kind": "observation"},),
        ).replay()
    with pytest.raises(ValueError, match="direction"):
        ProcessEvidenceGraph(
            ({"id": "a", "kind": "context"}, {"id": "b", "kind": "step_state"}),
            ({"source": "a", "target": "b", "kind": "dependency"},),
        ).replay()


def test_all_edge_kinds_hostile():
    with pytest.raises(ValueError):
        ProcessEvidenceGraph(
            ({"id": "a", "kind": "step_state"},),
            ({"source": "a", "target": "a", "kind": "transition"},),
        ).replay()
    with pytest.raises(ValueError):
        ProcessEvidenceGraph(
            (
                {
                    "id": "a",
                    "kind": "relation",
                    "entity": "x",
                    "predicate": "p",
                    "frame": 1,
                },
                {
                    "id": "b",
                    "kind": "relation",
                    "entity": "x",
                    "predicate": "p",
                    "frame": 0,
                },
            ),
            ({"source": "a", "target": "b", "kind": "persistence"},),
        ).replay()
    with pytest.raises(ValueError):
        ProcessEvidenceGraph(
            (
                {"id": "a", "kind": "step_state", "step": "a"},
                {"id": "b", "kind": "step_state", "step": "c"},
            ),
            (
                {
                    "source": "a",
                    "target": "b",
                    "kind": "transition",
                    "task_steps": ["a", "b", "c"],
                },
            ),
        ).replay()
