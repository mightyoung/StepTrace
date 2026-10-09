"""Canonical hashes and append-only evidence sidecar helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Mapping


def canonical_json(value: object) -> str:
    """Serialize JSON deterministically, including compiled dataclasses."""
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def portable_path(path: str | Path, base: str | Path | None = None) -> str:
    """Return a machine-independent POSIX path for frozen evidence artifacts.

    Paths under ``base`` (default: current working directory, i.e. the
    repository root by project convention) are recorded repository-relative so
    the artifact verifies on any checkout. Paths outside ``base`` stay
    absolute.
    """
    resolved = Path(path).resolve()
    root = Path(base).resolve() if base is not None else Path.cwd().resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def resolve_portable(path: str | Path, base: str | Path | None = None) -> Path:
    """Resolve a recorded evidence path against the repository root.

    Relative paths must stay inside the repository (no ``..`` traversal);
    absolute paths are accepted for backward compatibility with artifacts
    frozen before portable paths were introduced.
    """
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    if ".." in candidate.parts:
        raise ValueError(f"path traversal is not allowed: {path}")
    root = Path(base).resolve() if base is not None else Path.cwd().resolve()
    return (root / candidate).resolve()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_asset(path: str | Path) -> str:
    """Hash a file or a directory tree independent of insertion order."""
    root = Path(path).expanduser().resolve()
    if root.is_file():
        return sha256_file(root)
    if not root.is_dir():
        raise FileNotFoundError(root)
    entries = []
    for item in sorted(
        (p for p in root.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(root).as_posix(),
    ):
        rel = item.relative_to(root).as_posix()
        entries.append(
            {"path": rel, "size": item.stat().st_size, "sha256": sha256_file(item)}
        )
    if not entries:
        raise ValueError(f"empty asset directory: {root}")
    return hashlib.sha256(canonical_json(entries).encode("utf-8")).hexdigest()


def composite_model_hash(*paths: str | Path) -> str:
    """Combine model content hashes without machine-specific absolute paths."""
    if not paths:
        raise ValueError("at least one model asset is required")
    assets = [
        {"role": f"asset-{index}", "sha256": sha256_asset(path)}
        for index, path in enumerate(paths)
    ]
    return hashlib.sha256(canonical_json(assets).encode("utf-8")).hexdigest()


def compiled_task_hash(task: object) -> str:
    return hashlib.sha256(canonical_json(task).encode("utf-8")).hexdigest()


def sidecar_record(cert: Any, graph: Any) -> dict[str, Any]:
    return {
        "certificate": cert.payload() | {"certificate_hash": cert.certificate_hash},
        "graph": graph.canonical(),
    }


def load_sidecar_record(record: Mapping[str, Any]):
    from display_project.evidence import (
        EvidenceCertificate,
        ProcessEvidenceGraph,
        VerificationContext,
    )

    cert_data = dict(record.get("certificate", {}))
    graph_data = record.get("graph", {})
    vc_data = cert_data.get("verification_context")
    if not isinstance(vc_data, Mapping):
        raise ValueError("verification_context missing")
    vc = VerificationContext(**dict(vc_data))
    cert_data["verification_context"] = vc
    for key in (
        "evidence",
        "negative",
        "authorizing_evidence_refs",
        "negative_evidence_refs",
    ):
        cert_data[key] = tuple(cert_data.get(key, ()))
    cert_hash = cert_data.pop("certificate_hash", None)
    cert = EvidenceCertificate(**cert_data, certificate_hash=cert_hash or "")
    graph = ProcessEvidenceGraph(
        tuple(graph_data.get("nodes", ())), tuple(graph_data.get("edges", ()))
    )
    return cert, graph
