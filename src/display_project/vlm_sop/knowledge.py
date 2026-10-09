"""Validated, truth-free SOP knowledge packs curated from controlled documents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml  # type: ignore[import-untyped]


class SOPKnowledgeError(ValueError):
    pass


_FORBIDDEN_KEYS = frozenset(
    {
        "annotation",
        "annotations",
        "answer",
        "correct_step",
        "dataset_answer",
        "ground_truth",
        "label_file",
        "oracle",
        "psr_labels",
        "psr_labels_with_errors",
        "target_step",
        "truth",
        "truth_store",
    }
)
_FORBIDDEN_VALUE_MARKERS = (
    "psr_labels.csv",
    "psr_labels_with_errors.csv",
    "psr_labels_raw.csv",
    "ar_labels.csv",
)


def _reject_truth_material(value: object, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold()
            if normalized in _FORBIDDEN_KEYS:
                raise SOPKnowledgeError(
                    f"runtime SOP knowledge contains forbidden truth field: {path}.{key}"
                )
            _reject_truth_material(child, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            _reject_truth_material(child, f"{path}[{index}]")
    elif isinstance(value, str):
        lowered = value.casefold()
        if any(marker in lowered for marker in _FORBIDDEN_VALUE_MARKERS):
            raise SOPKnowledgeError(
                f"runtime SOP knowledge references a forbidden label artifact: {path}"
            )


def _strings(raw: object, field: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if raw is None and allow_empty:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise SOPKnowledgeError(f"{field} must be a list")
    values = tuple(raw)
    if not allow_empty and not values:
        raise SOPKnowledgeError(f"{field} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise SOPKnowledgeError(f"{field} entries must be non-empty strings")
    return tuple(item.strip() for item in values)


@dataclass(frozen=True, slots=True)
class SOPStepCard:
    id: str
    title: str
    instruction: str
    prerequisites: tuple[str, ...]
    allowed_next: tuple[str, ...]
    required_objects: tuple[str, ...]
    completion_cues: tuple[str, ...]
    negative_cues: tuple[str, ...]
    ambiguity_rules: tuple[str, ...]
    source_refs: tuple[str, ...]

    def prompt_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "instruction": self.instruction,
            "prerequisites": list(self.prerequisites),
            "allowed_next": list(self.allowed_next),
            "required_objects": list(self.required_objects),
            "completion_cues": list(self.completion_cues),
            "negative_cues": list(self.negative_cues),
            "ambiguity_rules": list(self.ambiguity_rules),
            "source_refs": list(self.source_refs),
        }


@dataclass(frozen=True, slots=True)
class SOPKnowledgePack:
    schema_version: int
    pack_id: str
    task_id: str
    language: str
    status: str
    authority: str
    documents: tuple[Mapping[str, object], ...]
    steps: tuple[SOPStepCard, ...]
    global_rules: tuple[str, ...]

    @property
    def step_ids(self) -> tuple[str, ...]:
        return tuple(step.id for step in self.steps)

    def step(self, step_id: str) -> SOPStepCard:
        for card in self.steps:
            if card.id == step_id:
                return card
        raise SOPKnowledgeError(f"unknown SOP knowledge step: {step_id}")


def _required_text(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SOPKnowledgeError(f"{field} must be a non-empty string")
    return value.strip()


def load_sop_knowledge(path: str | Path) -> SOPKnowledgePack:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise SOPKnowledgeError("SOP knowledge pack must be a mapping")
    _reject_truth_material(payload)
    if payload.get("schema_version") != 1:
        raise SOPKnowledgeError("unsupported SOP knowledge schema_version")
    authority = _required_text(payload, "authority")
    if authority != "audit_only":
        raise SOPKnowledgeError("SOP knowledge authority must be audit_only")
    raw_documents = payload.get("documents", ())
    if isinstance(raw_documents, (str, bytes)) or not isinstance(
        raw_documents, Sequence
    ):
        raise SOPKnowledgeError("documents must be a list")
    documents: list[Mapping[str, object]] = []
    document_ids: set[str] = set()
    for item in raw_documents:
        if not isinstance(item, Mapping):
            raise SOPKnowledgeError("document entries must be mappings")
        document_id = _required_text(item, "id")
        if document_id in document_ids:
            raise SOPKnowledgeError(f"duplicate document id: {document_id}")
        documents.append(dict(item))
        document_ids.add(document_id)
    raw_steps = payload.get("steps")
    if isinstance(raw_steps, (str, bytes)) or not isinstance(raw_steps, Sequence):
        raise SOPKnowledgeError("steps must be a non-empty list")
    if not raw_steps:
        raise SOPKnowledgeError("steps must be a non-empty list")
    cards: list[SOPStepCard] = []
    ids: set[str] = set()
    for raw in raw_steps:
        if not isinstance(raw, Mapping):
            raise SOPKnowledgeError("step entries must be mappings")
        step_id = _required_text(raw, "id")
        if step_id in ids:
            raise SOPKnowledgeError(f"duplicate SOP knowledge step: {step_id}")
        card = SOPStepCard(
            id=step_id,
            title=_required_text(raw, "title"),
            instruction=_required_text(raw, "instruction"),
            prerequisites=_strings(raw.get("prerequisites", ()), "prerequisites"),
            allowed_next=_strings(raw.get("allowed_next", ()), "allowed_next"),
            required_objects=_strings(
                raw.get("required_objects", ()), "required_objects"
            ),
            completion_cues=_strings(
                raw.get("completion_cues"), "completion_cues", allow_empty=False
            ),
            negative_cues=_strings(raw.get("negative_cues", ()), "negative_cues"),
            ambiguity_rules=_strings(
                raw.get("ambiguity_rules", ()), "ambiguity_rules"
            ),
            source_refs=_strings(
                raw.get("source_refs"), "source_refs", allow_empty=False
            ),
        )
        cards.append(card)
        ids.add(step_id)
    for card in cards:
        unknown = (set(card.prerequisites) | set(card.allowed_next)) - ids
        if unknown:
            raise SOPKnowledgeError(
                f"step {card.id} references unknown steps: {sorted(unknown)}"
            )
        for source_ref in card.source_refs:
            document_id = source_ref.split("#", 1)[0]
            if document_id not in document_ids:
                raise SOPKnowledgeError(
                    f"step {card.id} references unknown document: {document_id}"
                )
    return SOPKnowledgePack(
        schema_version=1,
        pack_id=_required_text(payload, "pack_id"),
        task_id=_required_text(payload, "task_id"),
        language=_required_text(payload, "language"),
        status=_required_text(payload, "status"),
        authority=authority,
        documents=tuple(documents),
        steps=tuple(cards),
        global_rules=_strings(payload.get("global_rules", ()), "global_rules"),
    )


def reject_truth_material(value: object) -> None:
    """Public guard for runtime evidence supplied to a prompt builder."""

    _reject_truth_material(value, "runtime_context")
