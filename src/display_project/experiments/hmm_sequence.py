"""Dependency-free, online HMM prediction baseline for development runs.

The adapter emits experiment-only predictions and posterior state estimates.
It has no production SOP authority, and neither the artifact validator nor the
runtime call accepts annotations or ground truth.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


HASH_RE = re.compile(r"^[0-9a-f]{64}$")

FEATURE_NAMES = ("luma_mean", "luma_std", "frame_motion")
_FORBIDDEN = (
    "video",
    "frame",
    "timestamp",
    "step_sequence",
    "reference",
    "ground_truth",
    "annotation",
    "review",
    "truth",
)


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _canonical(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            _canonical(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _reject_forbidden(value: Any) -> None:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, Mapping):
            for key, child in item.items():
                lowered = str(key).lower()
                if any(token in lowered for token in _FORBIDDEN):
                    raise ValueError(f"forbidden HMM artifact field: {key}")
                pending.append(child)
        elif isinstance(item, (list, tuple)):
            pending.extend(item)


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _logsumexp(values: Sequence[float]) -> float:
    if not values:
        return -math.inf
    peak = max(values)
    return peak + math.log(sum(math.exp(value - peak) for value in values))


def _log_normalize(values: Sequence[float]) -> list[float]:
    if not values:
        raise ValueError("empty probability vector")
    peak = max(values)
    if not math.isfinite(peak):
        raise ValueError("non-finite log probability")
    total = sum(math.exp(v - peak) for v in values)
    return [v - peak - math.log(total) for v in values]


def load_hmm_artifact(
    path: str | Path,
    *,
    expected_hash: str | None = None,
    expected_partition_hash: str | None = None,
    expected_task_id: str | None = None,
    expected_task_config_hash: str | None = None,
) -> dict[str, Any]:
    p = Path(path)
    payload = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("HMM artifact must be an object")
    artifact = dict(payload)
    _reject_forbidden(artifact)
    required = {
        "schema_version",
        "baseline_kind",
        "task_id",
        "task_config_hash",
        "states",
        "observation_schema",
        "initial_log_probs",
        "transition_log_probs",
        "emission",
        "min_posterior",
        "fit_provenance",
        "artifact_hash",
    }
    if set(artifact) != required:
        raise ValueError("HMM artifact keys mismatch")
    if (
        isinstance(artifact["schema_version"], bool)
        or artifact["schema_version"] != 1
        or artifact["baseline_kind"] != "hmm"
    ):
        raise ValueError("unsupported HMM artifact")
    states = artifact["states"]
    if (
        not isinstance(states, list)
        or not states
        or any(not isinstance(s, str) or not s for s in states)
        or len(states) != len(set(states))
    ):
        raise ValueError("states must be a non-empty list")
    n = len(states)
    if expected_task_id is not None and artifact["task_id"] != expected_task_id:
        raise ValueError("HMM task mismatch")
    if (
        expected_task_config_hash is not None
        and artifact["task_config_hash"] != expected_task_config_hash
    ):
        raise ValueError("HMM task config hash mismatch")
    obs = artifact["observation_schema"]
    if not isinstance(obs, Mapping) or set(obs) != {
        "feature_names",
        "feature_types",
        "extractor_code_hash",
        "model",
        "tracker",
    }:
        raise ValueError("observation schema mismatch")
    if obs["feature_names"] != list(FEATURE_NAMES) or obs["feature_types"] != [
        "float",
        "float",
        "float",
    ]:
        raise ValueError("observation features mismatch")
    if not isinstance(obs["extractor_code_hash"], str) or not HASH_RE.fullmatch(
        obs["extractor_code_hash"]
    ):
        raise ValueError("extractor code hash missing")
    if obs["model"] != "diagonal_gaussian" or obs["tracker"] != "NOT_APPLICABLE":
        raise ValueError("unsupported observation model")
    initial = artifact["initial_log_probs"]
    transition = artifact["transition_log_probs"]
    if (
        not isinstance(initial, list)
        or len(initial) != n
        or any(not _finite(v) for v in initial)
    ):
        raise ValueError("initial log probabilities shape")
    if (
        not isinstance(transition, list)
        or len(transition) != n
        or any(not isinstance(row, list) or len(row) != n for row in transition)
    ):
        raise ValueError("transition shape")
    for row_index, row in enumerate(transition):
        if any(value is not None and not _finite(value) for value in row):
            raise ValueError("non-finite transition")
        finite_count = 0
        total = 0.0
        for j, value in enumerate(row):
            if value is not None:
                if j < row_index or j > row_index + 1:
                    raise ValueError("illegal HMM transition")
                finite_count += 1
                total += math.exp(float(value))
        if finite_count == 0 or abs(total - 1.0) > 1e-6:
            raise ValueError("transition row not normalized")
    if abs(sum(math.exp(v) for v in initial) - 1.0) > 1e-6:
        raise ValueError("initial probabilities not normalized")
    emission = artifact["emission"]
    if not isinstance(emission, Mapping) or set(emission) != {"means", "variances"}:
        raise ValueError("emission schema")
    for key in ("means", "variances"):
        matrix = emission[key]
        if (
            not isinstance(matrix, list)
            or len(matrix) != n
            or any(not isinstance(row, list) or len(row) != 3 for row in matrix)
        ):
            raise ValueError("emission shape")
        if any(not _finite(v) for row in matrix for v in row):
            raise ValueError("non-finite emission")
    if any(v <= 0 for row in emission["variances"] for v in row):
        raise ValueError("emission variance must be positive")
    if (
        not _finite(artifact["min_posterior"])
        or not 0 <= float(artifact["min_posterior"]) <= 1
    ):
        raise ValueError("invalid min_posterior")
    provenance = artifact["fit_provenance"]
    if not isinstance(provenance, Mapping) or set(provenance) != {
        "project_split",
        "partition_manifest_hash",
        "fit_code_hash",
        "canonical_group_commitment",
        "fit_group_hashes",
        "count",
    }:
        raise ValueError("fit provenance schema")
    if provenance["project_split"] != "DEVELOPMENT" or any(
        not isinstance(provenance[k], str) or not HASH_RE.fullmatch(provenance[k])
        for k in (
            "partition_manifest_hash",
            "fit_code_hash",
            "canonical_group_commitment",
        )
    ):
        raise ValueError("fit provenance anchors invalid")
    groups = provenance["fit_group_hashes"]
    if (
        not isinstance(groups, list)
        or not groups
        or groups != sorted(set(groups))
        or any(
            not isinstance(group, str) or not HASH_RE.fullmatch(group)
            for group in groups
        )
        or provenance["canonical_group_commitment"] != canonical_sha256(groups)
    ):
        raise ValueError("fit group hashes invalid")
    if (
        isinstance(provenance["count"], bool)
        or not isinstance(provenance["count"], int)
        or provenance["count"] <= 0
    ):
        raise ValueError("fit provenance count invalid")
    calculated = canonical_sha256(
        {k: artifact[k] for k in required if k != "artifact_hash"}
    )
    if artifact["artifact_hash"] != calculated or (
        expected_hash is not None and artifact["artifact_hash"] != expected_hash
    ):
        raise ValueError("HMM artifact hash mismatch")
    if (
        expected_partition_hash is not None
        and provenance["partition_manifest_hash"] != expected_partition_hash
    ):
        raise ValueError("HMM partition hash mismatch")
    return artifact


def _features(image: Any, previous: Any) -> list[float]:
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    motion = 0.0 if previous is None else float(cv2.absdiff(gray, previous).mean())
    return [float(gray.mean()), float(gray.std()), motion]


class HMMSequenceAdapter:
    def __init__(
        self,
        *,
        fit_artifact: str | Path,
        expected_fit_artifact_hash: str,
        expected_fit_partition_hash: str,
        task_config_hash: str,
        task_id: str,
        states: Sequence[str],
        name: str = "hmm_or_crf",
    ) -> None:
        self.name = name
        self.artifact = load_hmm_artifact(
            fit_artifact,
            expected_hash=expected_fit_artifact_hash,
            expected_partition_hash=expected_fit_partition_hash,
            expected_task_id=task_id,
            expected_task_config_hash=task_config_hash,
        )
        if list(states) != self.artifact["states"]:
            raise ValueError("HMM states do not match compiled task")

    def __call__(
        self, video: str | Path, seed: int = 0, retry: int = 0, **kwargs: Any
    ) -> dict[str, Any]:
        import cv2

        group = kwargs.get("input_canonical_group")
        context = kwargs.get("run_context")
        if isinstance(context, Mapping):
            group = context.get("input_canonical_group", group)
        if (
            not isinstance(group, str)
            or len(group) != 64
            or any(c not in "0123456789abcdef" for c in group)
        ):
            return {
                "schema_version": 2,
                "method": self.name,
                "status": "unavailable",
                "error_code": "INPUT_GROUP_COMMITMENT_MISSING",
                "frames_processed": 0,
                "events": [],
                "partial_events": [],
                "authorizations": [],
                "certificates": [],
                "state_writes": 0,
                "coverage": {"definitive": 0, "opportunities": 0, "value": 0.0},
                "method_decisions": [],
                "authority_scope": "experiment_prediction_only",
                "annotation_used": False,
            }
        if group in self.artifact["fit_provenance"]["fit_group_hashes"]:
            return {
                "schema_version": 2,
                "method": self.name,
                "status": "unavailable",
                "error_code": "INPUT_GROUP_OVERLAPS_FIT",
                "frames_processed": 0,
                "events": [],
                "partial_events": [],
                "authorizations": [],
                "certificates": [],
                "state_writes": 0,
                "coverage": {"definitive": 0, "opportunities": 0, "value": 0.0},
                "method_decisions": [],
                "authority_scope": "experiment_prediction_only",
                "annotation_used": False,
            }
        cap = cv2.VideoCapture(str(video))
        events: list[dict[str, Any]] = []
        method_decisions: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []
        prev = None
        alpha = self.artifact["initial_log_probs"]
        transitions = [
            [-math.inf if value is None else float(value) for value in row]
            for row in self.artifact["transition_log_probs"]
        ]
        means = self.artifact["emission"]["means"]
        variances = self.artifact["emission"]["variances"]
        try:
            while True:
                ok, image = cap.read()
                if not ok:
                    break
                feat = _features(image, prev)
                prev = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                emission = [
                    -0.5
                    * sum(
                        math.log(2 * math.pi * variances[i][j])
                        + (feat[j] - means[i][j]) ** 2 / variances[i][j]
                        for j in range(3)
                    )
                    for i in range(len(means))
                ]
                if events:
                    alpha = _log_normalize(
                        [
                            emission[j]
                            + _logsumexp(
                                [
                                    alpha[i] + transitions[i][j]
                                    for i in range(len(alpha))
                                    if math.isfinite(transitions[i][j])
                                ]
                            )
                            for j in range(len(alpha))
                        ]
                    )
                else:
                    alpha = _log_normalize(
                        [alpha[i] + emission[i] for i in range(len(alpha))]
                    )
                posterior = [math.exp(v) for v in alpha]
                state = self.artifact["states"][
                    max(range(len(posterior)), key=posterior.__getitem__)
                ]
                frame_id = len(events)
                definitive = max(posterior) >= float(self.artifact["min_posterior"])
                digest = hashlib.sha256(image.tobytes()).hexdigest()
                trace.append({"frame": len(events), "source_frame_sha256": digest})
                event = {
                    "frame": frame_id,
                    "observed_state": state,
                    "predicted_step": state if definitive else None,
                    "confirmed_step": state if definitive else None,
                    "decision": "definitive" if definitive else "abstain",
                    "abstained": not definitive,
                    "posterior": posterior,
                    "features": feat,
                    "source_frame_sha256": digest,
                }
                events.append(event)
                if definitive:
                    method_decisions.append(dict(event))
        finally:
            cap.release()
        return {
            "schema_version": 2,
            "method": self.name,
            "status": "success",
            "frames_processed": len(events),
            "events": events,
            "partial_events": [],
            "authorizations": [],
            "certificates": [],
            "state_writes": 0,
            "coverage": {
                "definitive": len(method_decisions),
                "opportunities": len(events),
                "value": len(method_decisions) / len(events) if events else 0.0,
            },
            "annotation_used": False,
            "method_decisions": method_decisions,
            "authority_scope": "experiment_prediction_only",
            "complete_video": True,
            "frame_trace": trace,
            "fit_artifact_hash": self.artifact["artifact_hash"],
            "input_canonical_group": group,
        }


__all__ = [
    "HMMSequenceAdapter",
    "load_hmm_artifact",
    "canonical_sha256",
    "FEATURE_NAMES",
]
