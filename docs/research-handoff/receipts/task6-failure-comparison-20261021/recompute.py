"""Recompute failed node IDs from original pytest short-summary excerpts.

Standard library only; runs no tests/models/network requests and writes nothing.
"""
from pathlib import Path
import hashlib
import json


def extract(text):
    nodes, suffixes = [], []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.startswith("FAILED "):
            continue
        node, separator, suffix = line[len("FAILED "):].partition(" - ")
        if "..." in node or "…" in node or "::" not in node:
            raise ValueError(f"Incomplete node ID at summary line {number}: {node}")
        nodes.append(node)
        if separator:
            suffixes.append({"summary_line": number, "node_id": node, "removed_suffix": separator + suffix})
    if len(nodes) != 129 or len(set(nodes)) != 129:
        raise ValueError("Expected 129 distinct failed nodes in this frozen evidence")
    return sorted(nodes), suffixes


def recompute(root):
    result = {}
    for run in ("baseline", "after"):
        source = root / f"{run}.summary.txt"
        nodes, suffixes = extract(source.read_text(encoding="utf-8"))
        result[run] = {
            "summary_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "failed_node_ids": nodes,
            "removed_exception_suffixes": suffixes,
        }
    baseline = set(result["baseline"]["failed_node_ids"])
    after = set(result["after"]["failed_node_ids"])
    result["new_failed_node_ids"] = sorted(after - baseline)
    result["resolved_failed_node_ids"] = sorted(baseline - after)
    result["equal_failed_node_sets"] = baseline == after
    return result


if __name__ == "__main__":
    print(json.dumps(recompute(Path(__file__).resolve().parent), ensure_ascii=False, indent=2))
