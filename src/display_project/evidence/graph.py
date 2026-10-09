from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json


def _canon(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


_NODE_KINDS = {"context", "object", "relation", "step_state", "negative", "observation"}
_EDGE_KINDS = {"observation", "persistence", "transition", "negative", "dependency"}


@dataclass(frozen=True)
class ProcessEvidenceGraph:
    nodes: tuple = field(default_factory=tuple)
    edges: tuple = field(default_factory=tuple)

    def add_node(self, node):
        return ProcessEvidenceGraph(self.nodes + (deepcopy(dict(node)),), self.edges)

    def add_edge(self, edge):
        return ProcessEvidenceGraph(self.nodes, self.edges + (deepcopy(dict(edge)),))

    def canonical(self):
        return {
            "nodes": sorted((deepcopy(n) for n in self.nodes), key=_canon),
            "edges": sorted((deepcopy(e) for e in self.edges), key=_canon),
        }

    def digest(self):
        self.replay()
        return hashlib.sha256(_canon(self.canonical()).encode()).hexdigest()

    def replay(self):
        ids, kinds = set(), {}
        for node in self.nodes:
            if (
                not isinstance(node, dict)
                or not isinstance(node.get("id"), str)
                or not node["id"]
            ):
                raise ValueError("invalid node")
            if node["id"] in ids:
                raise ValueError("duplicate node")
            if node.get("kind") not in _NODE_KINDS:
                raise ValueError("missing or unknown node kind")
            ids.add(node["id"])
            kinds[node["id"]] = node["kind"]
        direction = {
            "observation": (
                {"context"},
                {"object", "relation", "negative", "observation"},
            ),
            "persistence": ({"object", "relation"}, {"object", "relation"}),
            "transition": ({"step_state"}, {"step_state"}),
            "negative": ({"negative"}, {"step_state"}),
            "dependency": (
                {"object", "relation", "step_state"},
                {"relation", "step_state"},
            ),
        }
        seen, causal = set(), []
        by_id = {n["id"]: n for n in self.nodes}
        for edge in self.edges:
            if not isinstance(edge, dict) or edge.get("kind") not in _EDGE_KINDS:
                raise ValueError("missing or unknown edge kind")
            source, target = edge.get("source"), edge.get("target")
            if (
                not isinstance(source, str)
                or not isinstance(target, str)
                or source not in ids
                or target not in ids
            ):
                raise ValueError("dangling edge")
            if source == target:
                raise ValueError("illegal self-loop")
            key = (source, target, edge["kind"])
            if key in seen:
                raise ValueError("duplicate edge")
            seen.add(key)
            src, dst = direction[edge["kind"]]
            if kinds[source] not in src or kinds[target] not in dst:
                raise ValueError("illegal edge direction")
            if edge["kind"] == "persistence":
                a, b = by_id[source], by_id[target]
                if a.get("entity") != b.get("entity") or a.get("predicate") != b.get(
                    "predicate"
                ):
                    raise ValueError("persistence entity mismatch")
                if (
                    not isinstance(a.get("frame"), int)
                    or not isinstance(b.get("frame"), int)
                    or b["frame"] <= a["frame"]
                ):
                    raise ValueError("persistence time must increase")
            if edge["kind"] == "transition":
                a, b, steps = by_id[source], by_id[target], edge.get("task_steps")
                if not isinstance(steps, (list, tuple)):
                    raise ValueError("transition task_steps required")
                if a.get("step") not in steps or b.get("step") not in steps:
                    raise ValueError("unknown transition step")
                if steps.index(b["step"]) != steps.index(a["step"]) + 1:
                    raise ValueError("non-adjacent transition")
            causal.append((source, target))
        pending = set(ids)
        while pending:
            ready = {
                n
                for n in pending
                if not any(s == n and t in pending for s, t in causal)
            }
            if not ready:
                raise ValueError("causal cycle")
            pending -= ready
        return True
