"""Deterministic, fail-closed fitter for the development HMM baseline.

The fitter consumes pre-extracted numeric observations only.  It never reads
videos or annotation/truth stores and refuses records carrying those fields.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .hmm_sequence import FEATURE_NAMES, canonical_sha256

_FORBIDDEN = (
    "video",
    "frame",
    "timestamp",
    "annotation",
    "truth",
    "ground_truth",
    "review",
)


def _reject(value: Any) -> None:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, Mapping):
            for key, child in item.items():
                if any(token in str(key).lower() for token in _FORBIDDEN):
                    raise ValueError(f"forbidden fit input field: {key}")
                pending.append(child)
        elif isinstance(item, (list, tuple)):
            pending.extend(item)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _log_probs(counts: Sequence[float]) -> list[float]:
    total = sum(counts)
    if total <= 0:
        raise ValueError("empty probability counts")
    return [math.log(value / total) for value in counts]


def fit_hmm_artifact(
    records: Iterable[Mapping[str, Any]],
    *,
    task_id: str,
    task_config_hash: str,
    partition_manifest_hash: str,
    states: Sequence[str],
    fit_code_hash: str,
    min_posterior: float = 0.75,
) -> dict[str, Any]:
    """Fit diagonal-Gaussian emissions and self/next transitions.

    Records must be DEVELOPMENT observations with a stable ``group_hash``;
    unknown partitions, duplicate groups, malformed features, and missing
    states fail closed.
    """
    if not states or len(set(states)) != len(states):
        raise ValueError("states must be unique and non-empty")
    if not 0 <= min_posterior <= 1:
        raise ValueError("invalid min_posterior")
    state_index = {state: i for i, state in enumerate(states)}
    groups: dict[str, list[tuple[int, list[float]]]] = {}
    for raw in records:
        _reject(raw)
        if set(raw) != {"group_hash", "partition", "state", "features"}:
            raise ValueError("fit record schema mismatch")
        group = raw["group_hash"]
        if (
            not isinstance(group, str)
            or len(group) != 64
            or any(c not in "0123456789abcdef" for c in group)
        ):
            raise ValueError("invalid group hash")
        if raw["partition"] != "DEVELOPMENT":
            raise ValueError("only DEVELOPMENT observations may be fitted")
        state = raw["state"]
        features = raw["features"]
        if (
            state not in state_index
            or not isinstance(features, list)
            or len(features) != 3
        ):
            raise ValueError("observation state/features mismatch")
        if any(
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(float(v))
            for v in features
        ):
            raise ValueError("non-finite observation")
        groups.setdefault(group, []).append(
            (state_index[state], [float(v) for v in features])
        )
    if not groups:
        raise ValueError("no development observations")
    if any(not samples for samples in groups.values()):
        raise ValueError("empty fit group")
    all_samples = [sample for samples in groups.values() for sample in samples]
    if {state for state, _ in all_samples} != set(range(len(states))):
        raise ValueError("fit observations do not cover every task state")
    n = len(states)
    initial = [1.0] * n
    transitions = [[0.0] * n for _ in range(n)]
    for samples in groups.values():
        initial[samples[0][0]] += 1.0
        for (left, _), (right, _) in zip(samples, samples[1:]):
            if right not in (left, left + 1):
                raise ValueError("observed transition is not self/next")
            transitions[left][right] += 1.0
        transitions[samples[-1][0]][samples[-1][0]] += 1.0
    means: list[list[float]] = []
    variances: list[list[float]] = []
    for state in range(n):
        values = [features for observed, features in all_samples if observed == state]
        means.append([sum(row[j] for row in values) / len(values) for j in range(3)])
        variances.append(
            [
                max(
                    1e-6,
                    sum((row[j] - means[-1][j]) ** 2 for row in values) / len(values),
                )
                for j in range(3)
            ]
        )
    transition_logs = []
    for row_index, row in enumerate(transitions):
        allowed = [j for j in range(n) if j in (row_index, row_index + 1)]
        counts = [row[j] if row[j] > 0 else 1.0 for j in allowed]
        logs: list[float | None] = [None] * n
        for j, value in zip(allowed, _log_probs(counts)):
            logs[j] = value
        transition_logs.append(logs)
    artifact = {
        "schema_version": 1,
        "baseline_kind": "hmm",
        "task_id": task_id,
        "task_config_hash": task_config_hash,
        "states": list(states),
        "observation_schema": {
            "feature_names": list(FEATURE_NAMES),
            "feature_types": ["float", "float", "float"],
            "extractor_code_hash": fit_code_hash,
            "model": "diagonal_gaussian",
            "tracker": "NOT_APPLICABLE",
        },
        "initial_log_probs": _log_probs(initial),
        "transition_log_probs": transition_logs,
        "emission": {"means": means, "variances": variances},
        "min_posterior": min_posterior,
        "fit_provenance": {
            "project_split": "DEVELOPMENT",
            "partition_manifest_hash": partition_manifest_hash,
            "fit_code_hash": fit_code_hash,
            "canonical_group_commitment": canonical_sha256(sorted(groups)),
            "fit_group_hashes": sorted(groups),
            "count": len(all_samples),
        },
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def fit_hmm_jsonl(
    input_path: str | Path, output_path: str | Path, **kwargs: Any
) -> dict[str, Any]:
    path = Path(input_path)
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    artifact = fit_hmm_artifact(records, **kwargs)
    Path(output_path).write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return artifact


__all__ = ["fit_hmm_artifact", "fit_hmm_jsonl"]
