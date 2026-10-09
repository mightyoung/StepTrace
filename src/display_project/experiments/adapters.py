"""Spawn-safe, fail-closed paired experiment adapters."""

from __future__ import annotations
import importlib
import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, cast

from display_project.evidence.certificate import AUTHORIZING_EVIDENCE_SOURCES
from display_project.provenance import sha256_file


def _frame_sha256(frame: Any) -> str:
    return hashlib.sha256(frame.tobytes() if frame is not None else b"").hexdigest()


def _source_frame_trace(path: Path, count: int) -> list[dict[str, Any]]:
    import cv2

    cap = cv2.VideoCapture(str(path))
    trace: list[dict[str, Any]] = []
    try:
        while len(trace) < count:
            ok, image = cap.read()
            if not ok:
                break
            trace.append(
                {"frame": len(trace), "source_frame_sha256": _frame_sha256(image)}
            )
    finally:
        cap.release()
    return trace


def _validated_frame_count(value: object) -> int | None:
    """Return a non-negative integer frame count, rejecting ambiguous values."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


METHOD_SCHEMA = 2
_BUILTIN_PROCESSORS = {
    "display_project.experiments.adapters:software_fixture_processor",
}


def _resolve_processor(name: str) -> Callable[..., Mapping[str, Any]]:
    if name not in _BUILTIN_PROCESSORS:
        raise ValueError("processor is not allowlisted")
    module_name, separator, attribute = name.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("processor must be a module:attribute path")
    processor = getattr(importlib.import_module(module_name), attribute, None)
    if not callable(processor):
        raise ValueError("allowlisted processor is not callable")
    return cast(Callable[..., Mapping[str, Any]], processor)


def _result(
    method: str,
    status: str,
    *,
    error_code: str | None = None,
    error: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "schema_version": METHOD_SCHEMA,
        "method": method,
        "status": status,
        "frames_processed": 0,
        "events": [],
        "partial_events": [],
        "authorizations": [],
        "certificates": [],
        "state_writes": 0,
        "coverage": {"definitive": 0, "opportunities": 0, "value": 0.0},
        "annotation_used": False,
        "error_code": error_code,
        "error": error,
        **extra,
    }


def _normalization_failure(method: str, code: str, error: str) -> dict[str, Any]:
    return _result(method, "crash", error_code=code, error=error)


def _normalize(method: str, result: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(result)
    out.setdefault("schema_version", METHOD_SCHEMA)
    out.setdefault("method", method)
    if out.get("status") not in {
        "success",
        "unavailable",
        "crash",
        "unreadable",
        "partial",
    }:
        return _normalization_failure(
            method, "RESULT_STATUS_INVALID", "status is missing or invalid"
        )
    frames_processed = out.get("frames_processed")
    if (
        isinstance(frames_processed, bool)
        or not isinstance(frames_processed, int)
        or frames_processed < 0
    ):
        return _normalization_failure(
            method, "FRAMES_PROCESSED_INVALID", "frames_processed is invalid"
        )
    events = out.get("events")
    if isinstance(events, (str, Path)):
        path = Path(events)
        if not path.is_file():
            return _normalization_failure(method, "EVENTS_JSONL_MISSING", str(path))
        try:
            events = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, ValueError) as exc:
            return _normalization_failure(method, "EVENTS_JSONL_INVALID", str(exc))
    if not isinstance(events, list) or any(
        not isinstance(event, Mapping) for event in events
    ):
        return _normalization_failure(
            method, "EVENTS_SCHEMA_INVALID", "events must be a list of objects"
        )
    if out.get("status") == "success" and frames_processed != len(events):
        return _normalization_failure(
            method, "EVENT_COUNT_MISMATCH", "frames_processed must equal event count"
        )
    partial = out.get("partial_events", [])
    if not isinstance(partial, list):
        return _normalization_failure(
            method, "PARTIAL_EVENTS_SCHEMA_INVALID", "partial_events must be a list"
        )
    authorizations = out.get("authorizations", [])
    certificates = out.get("certificates", [])
    if not isinstance(authorizations, list) or not isinstance(certificates, list):
        return _normalization_failure(
            method,
            "AUTHORIZATION_SCHEMA_INVALID",
            "authorizations and certificates must be lists",
        )
    coverage = out.get("coverage")
    if not isinstance(coverage, Mapping):
        return _normalization_failure(
            method, "COVERAGE_SCHEMA_INVALID", "coverage must be an object"
        )
    if set(coverage) != {"definitive", "opportunities", "value"}:
        return _normalization_failure(
            method, "COVERAGE_SCHEMA_INVALID", "coverage fields mismatch"
        )
    definitive, opportunities, value = (
        coverage["definitive"],
        coverage["opportunities"],
        coverage["value"],
    )
    if (
        isinstance(definitive, bool)
        or not isinstance(definitive, int)
        or definitive < 0
        or isinstance(opportunities, bool)
        or not isinstance(opportunities, int)
        or opportunities < 0
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0.0 <= float(value) <= 1.0
    ):
        return _normalization_failure(
            method, "COVERAGE_SCHEMA_INVALID", "coverage values are invalid"
        )
    expected_value = definitive / opportunities if opportunities else 0.0
    if (
        definitive > opportunities
        or opportunities != len(events)
        or definitive != len(authorizations)
        or abs(float(value) - expected_value) > 1e-12
    ):
        return _normalization_failure(
            method, "COVERAGE_INCONSISTENT", "coverage counts and value disagree"
        )
    if out.get("annotation_used") is not False:
        return _normalization_failure(
            method, "ANNOTATION_USED_FORBIDDEN", "annotation_used must be false"
        )
    state_writes = out.get("state_writes", len(authorizations))
    if (
        isinstance(state_writes, bool)
        or not isinstance(state_writes, int)
        or state_writes != len(authorizations)
    ):
        return _normalization_failure(
            method,
            "STATE_WRITES_INCONSISTENT",
            "state_writes must equal authorizations",
        )
    out["events"] = [dict(event) for event in events]
    out["partial_events"] = partial
    out["annotation_used"] = False
    out["state_writes"] = state_writes
    if out.get("status") == "success":
        out.setdefault("complete_video", True)
    return out


class ExplicitUnavailableAdapter:
    def __init__(self, name: str, reason: str = "method is not registered") -> None:
        self.name, self.reason = name, reason

    def __call__(
        self, video: str | Path, seed: int = 0, retry: int = 0, **kwargs: Any
    ) -> dict[str, Any]:
        return _result(
            self.name,
            "unavailable",
            error_code="METHOD_UNAVAILABLE",
            error=self.reason,
            seed=seed,
            retry=retry,
        )


class VlmAuditOnlyAdapter:
    def __init__(
        self, review_json: str | Path | None = None, *, name: str = "vlm_only"
    ) -> None:
        self.review_json, self.name = review_json, name

    def __call__(
        self, video: str | Path, seed: int = 0, retry: int = 0, **kwargs: Any
    ) -> dict[str, Any]:
        import cv2

        path = Path(video)
        if not path.is_file():
            return _result(
                self.name, "unavailable", error_code="VIDEO_MISSING", error=str(path)
            )
        reviews: dict[str, Any] = {}
        if self.review_json is not None:
            try:
                payload = json.loads(Path(self.review_json).read_text(encoding="utf-8"))
                reviews = payload if isinstance(payload, dict) else {"reviews": payload}
            except (OSError, ValueError) as exc:
                return _result(
                    self.name,
                    "unavailable",
                    error_code="REVIEW_JSON_INVALID",
                    error=str(exc),
                )
        cap = cv2.VideoCapture(str(path))
        events: list[dict[str, Any]] = []
        frame = 0
        try:
            while True:
                ok, image = cap.read()
                if not ok:
                    break
                events.append(
                    {
                        "frame": frame,
                        "decision": "unknown",
                        "observed_step": None,
                        "authorization": False,
                        "evidence_source": "vlm_audit",
                        "source_frame_sha256": _frame_sha256(image),
                    }
                )
                frame += 1
        finally:
            cap.release()
        if not events:
            return _result(
                self.name, "unreadable", error_code="VIDEO_UNREADABLE", reviews=reviews
            )
        return _normalize(
            self.name,
            {
                "status": "success",
                "frames_processed": len(events),
                "frame_trace": events,
                "events": events,
                "reviews": reviews,
                "authorizations": [],
                "certificates": [],
                "state_writes": 0,
                "annotation_used": False,
                "coverage": {
                    "definitive": 0,
                    "opportunities": len(events),
                    "value": 0.0,
                },
            },
        )


def _build_detector(
    detector_kind: str,
    model_path: str,
    *,
    device: str,
    yolo_image_size: int,
    yolo_iou_threshold: float,
) -> Any:
    from display_project.detection import GroundingDinoDetector, UltralyticsYoloDetector

    if detector_kind == "grounding_dino":
        return GroundingDinoDetector(model_path, device=device)
    if detector_kind == "yolo":
        return UltralyticsYoloDetector(
            model_path,
            device=device,
            image_size=yolo_image_size,
            iou_threshold=yolo_iou_threshold,
        )
    raise ValueError("unsupported detector_kind")


def _artifact_paths(
    output_root: str | Path, video: str | Path, method: str, seed: int, retry: int
) -> tuple[Path, Path, Path]:
    safe_method = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in method
    )
    stem = f"{Path(video).stem}.{safe_method}.seed-{seed}.retry-{retry}"
    root = Path(output_root)
    output = root / f"{stem}.mp4"
    events = root / f"{stem}.events.jsonl"
    certificates = events.parent / (events.stem + ".certificates") / "records.jsonl"
    return output, events, certificates


def software_fixture_processor(
    video: str | Path,
    *,
    policy: str,
    method: str | None = None,
    seed: int = 0,
    retry: int = 0,
    **kwargs: Any,
) -> Mapping[str, Any]:
    """Spawn-importable no-authority fixture used only by integration tests."""
    import cv2

    capture = cv2.VideoCapture(str(video))
    events: list[dict[str, Any]] = []
    try:
        frame = 0
        while True:
            ok, image = capture.read()
            if not ok:
                break
            events.append(
                {
                    "frame": frame,
                    "decision": "unknown",
                    "authorization": False,
                    "experiment_profile": "SOFTWARE_FIXTURE",
                    "observed_step": None,
                    "evidence_source": "vlm_audit",
                    "source_frame_sha256": _frame_sha256(image),
                }
            )
            frame += 1
    finally:
        capture.release()
    result: dict[str, Any] = {
        "status": "success",
        "frames_processed": len(events),
        "frame_trace": events,
        "events": events,
        "partial_events": [],
        "authorizations": [],
        "certificates": [],
        "state_writes": 0,
        "annotation_used": False,
        "coverage": {"definitive": 0, "opportunities": len(events), "value": 0.0},
        "policy": policy,
        "experiment_profile": "SOFTWARE_FIXTURE",
        "seed": seed,
        "retry": retry,
    }
    if method == "hmm_or_crf":
        result.update(
            authority_scope="experiment_prediction_only",
            method_decisions=[],
        )
    return result


def _event_authorized(event: Mapping[str, Any]) -> bool:
    evidence = event.get("evidence")
    if not isinstance(evidence, list):
        return bool(event.get("authorization", False))
    flags = []
    for item in evidence:
        if isinstance(item, Mapping) and isinstance(item.get("value"), Mapping):
            value = cast(Mapping[str, Any], item["value"])
            if "coverage_eligible" in value:
                flags.append(value["coverage_eligible"])
    if any(not isinstance(flag, bool) for flag in flags):
        raise ValueError("coverage_eligible must be boolean")
    return any(cast(list[bool], flags))


def _read_recovery_jsonl(path: Path) -> list[Any]:
    """Read a durable JSONL prefix, dropping only an unterminated torn tail."""
    text = path.read_text(encoding="utf-8")
    terminated = text.endswith("\n")
    lines = text.splitlines()
    parsed: list[Any] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not terminated:
                break
            raise
    return parsed


def _parse_render_audit(
    policy: str,
    events: list[dict[str, Any]],
    *,
    expected_input_hash: str | None = None,
    expected_model_hash: str | None = None,
    expected_config_hash: str | None = None,
    allow_single_trailing_orphan: bool = False,
    preparsed_certificate_records: Mapping[str | Path, list[Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from display_project.evidence import verify_certificate
    from display_project.provenance import load_sidecar_record

    if any(
        expected is not None
        for expected in (expected_input_hash, expected_model_hash, expected_config_hash)
    ) and not all(
        isinstance(expected, str)
        and len(expected) == 64
        and all(character in "0123456789abcdef" for character in expected)
        for expected in (expected_input_hash, expected_model_hash, expected_config_hash)
    ):
        raise ValueError("certificate provenance context is incomplete or invalid")
    if allow_single_trailing_orphan and preparsed_certificate_records is None:
        raise ValueError("trailing orphan allowance is recovery-only")
    authorizations: list[dict[str, Any]] = []
    certificate_records: dict[str, dict[str, Any]] = {}
    verified_certificates: dict[str, Any] = {}
    certificate_order: list[str] = []
    sidecars: set[Path] = set()
    for event in events:
        reference = event.get("certificate_artifact_ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference:
                raise ValueError("certificate artifact reference is invalid")
            sidecars.add(Path(reference))
    if preparsed_certificate_records is None:
        record_sources: list[tuple[Path, list[Any]]] = []
        for sidecar in sidecars:
            if not sidecar.is_file():
                raise FileNotFoundError(sidecar)
            records = [
                json.loads(line)
                for line in sidecar.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            record_sources.append((sidecar, records))
    else:
        if len(preparsed_certificate_records) != 1:
            raise ValueError("recovery requires exactly one certificate sidecar")
        recovery_path, recovery_records = next(
            iter(preparsed_certificate_records.items())
        )
        recovery_sidecar = Path(recovery_path)
        if not recovery_sidecar.is_file():
            raise FileNotFoundError(recovery_sidecar)
        if any(sidecar.resolve() != recovery_sidecar.resolve() for sidecar in sidecars):
            raise ValueError("event references a different certificate sidecar")
        record_sources = [(recovery_sidecar, recovery_records)]
    for _sidecar, records in record_sources:
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("certificate sidecar record is invalid")
            cert, graph = load_sidecar_record(record)
            cert_hash = cert.certificate_hash
            if not cert_hash or cert_hash in certificate_records:
                raise ValueError("certificate hash is missing or duplicated")
            # Bind provenance from the runner/manifest, never from the sidecar.
            for field, expected in (
                ("input_hash", expected_input_hash),
                ("model_hash", expected_model_hash),
                ("config_hash", expected_config_hash),
            ):
                if expected is not None and getattr(cert, field) != expected:
                    raise ValueError(
                        f"certificate {field} does not match frozen context"
                    )
            verification = verify_certificate(
                cert,
                graph,
                context=cert.verification_context,
                task_id=cert.task_id,
                expected_step=cert.expected_step,
                current_step=cert.current_step,
            )
            if not verification.valid:
                raise ValueError(
                    f"certificate verification failed: {verification.reason}"
                )
            if graph.digest() != cert.graph_digest:
                raise ValueError("certificate graph digest mismatch")
            certificate_records[cert_hash] = record
            verified_certificates[cert_hash] = cert
            certificate_order.append(cert_hash)
    referenced: set[str] = set()
    for event in events:
        authorized = _event_authorized(event)
        cert_hash = event.get("certificate_hash")
        cert_ref = event.get("certificate_artifact_ref")
        if authorized:
            if (
                event.get("decision") != "normal"
                or event.get("evidence_source") != "deterministic"
            ):
                raise ValueError(
                    "authorized event is not a deterministic normal transition"
                )
            if event.get("observed_step") != event.get("current_step"):
                raise ValueError("authorized event transition state is inconsistent")
            if policy in {"proposed", "uncalibrated_certificate"}:
                if (
                    not isinstance(cert_hash, str)
                    or cert_hash not in certificate_records
                    or not cert_ref
                ):
                    raise ValueError(
                        "certificate policy authorization lacks a matching certificate"
                    )
                if cert_hash in referenced:
                    raise ValueError("certificate is referenced by multiple events")
                cert = verified_certificates[cert_hash]
                expected_method = (
                    "Proposed" if policy == "proposed" else "NoCalibrationCertificate"
                )
                expected_version = (
                    None if policy == "proposed" else "UNCALIBRATED_RAW_V1"
                )
                if cert.method != expected_method:
                    raise ValueError("certificate method does not match policy")
                if (
                    expected_version is not None
                    and cert.calibration_version != expected_version
                ):
                    raise ValueError("certificate version does not match policy")
                if (
                    event.get("graph_digest") != cert.graph_digest
                    or event.get("expected_step") != cert.expected_step
                    or event.get("previous_step") != cert.current_step
                    or event.get("observed_step") != cert.candidate
                    or not str(event.get("event_id", "")).startswith(cert.task_id + "-")
                ):
                    raise ValueError(
                        "event and certificate transition metadata disagree"
                    )
                referenced.add(cert_hash)
            elif cert_hash is not None or cert_ref is not None:
                raise ValueError("non-proposed policy emitted a certificate")
            authorizations.append(
                {"event_id": event.get("event_id"), "certificate_hash": cert_hash}
            )
        elif cert_hash is not None or cert_ref is not None:
            raise ValueError("non-authorizing event references a certificate")
    if policy in {"proposed", "uncalibrated_certificate"}:
        referenced_order = [
            event.get("certificate_hash")
            for event in events
            if event.get("certificate_hash") is not None
        ]
        if allow_single_trailing_orphan:
            if certificate_order[: len(referenced_order)] != referenced_order or len(
                certificate_order
            ) - len(referenced_order) not in {0, 1}:
                raise ValueError(
                    "certificate records are missing, reordered, or contain non-tail extras"
                )
        elif referenced != set(certificate_records):
            raise ValueError("certificate records are missing, extra, or unreferenced")
    if policy == "fixed_streak" and certificate_records:
        raise ValueError("non-proposed policy produced certificate records")
    return authorizations, list(certificate_records.values())


def _recover_render_partial(
    artifact_paths: object,
    *,
    expected_input_hash: str | None,
    expected_model_hash: str | None,
    expected_config_hash: str | None,
) -> dict[str, Any]:
    """Recover only a validated prefix from an interrupted renderer."""
    base: dict[str, Any] = {
        "events": [],
        "partial_events": [],
        "frames_processed": 0,
        "frame_trace": [],
        "artifact_refs": [],
    }
    if artifact_paths is None:
        base["error_code"] = None
        return base
    if not isinstance(artifact_paths, Mapping):
        base.update(
            error_code="RECOVERY_INVALID", error="artifact paths are not an object"
        )
        return base
    try:
        events_path = Path(artifact_paths["events"])
        cert_path = Path(artifact_paths["certificates"])
    except (KeyError, TypeError, ValueError) as exc:
        base.update(
            error_code="RECOVERY_INVALID", error=f"artifact paths invalid: {exc}"
        )
        return base
    if not events_path.exists() and not cert_path.exists():
        base["error_code"] = None
        return base
    if not events_path.is_file():
        base.update(error_code="RECOVERY_INVALID", error="events artifact missing")
        return base
    try:
        from display_project.fusion.event_schema import validate_event

        rows = _read_recovery_jsonl(events_path)
        if not all(isinstance(row, Mapping) for row in rows):
            raise ValueError("event row is not an object")
        events = [dict(row) for row in rows]
        for index, event in enumerate(events):
            validate_event(event)
            if (
                event.get("annotation_used") is True
                or event.get("dataset_answer") is not None
            ):
                raise ValueError("partial event contains annotation/truth authority")
            evidence = event.get("evidence", [])
            if any(
                not isinstance(item, Mapping)
                or item.get("source") not in AUTHORIZING_EVIDENCE_SOURCES
                for item in evidence
            ):
                raise ValueError("partial event contains unauthorized evidence source")
            if (
                event.get("frame") not in (None, index)
                and event.get("event_id", "").find(f"frame-{index:06d}") < 0
            ):
                raise ValueError("event frame index is not a prefix")
        if cert_path.exists() and not cert_path.is_file():
            raise ValueError("certificate artifact is not a file")
        policy = str(artifact_paths.get("effective_policy", "proposed"))
        if policy == "no_calibration":
            policy = "uncalibrated_certificate"
        if policy not in {"fixed_streak", "proposed", "uncalibrated_certificate"}:
            raise ValueError("effective policy anchor is invalid")
        if (
            events
            and policy in {"proposed", "uncalibrated_certificate"}
            and not cert_path.is_file()
        ):
            raise ValueError(
                "certificate sidecar is required for certificate policy recovery"
            )
        if cert_path.exists():
            certificate_records = _read_recovery_jsonl(cert_path)
            _parse_render_audit(
                policy,
                events,
                expected_input_hash=expected_input_hash,
                expected_model_hash=expected_model_hash,
                expected_config_hash=expected_config_hash,
                allow_single_trailing_orphan=True,
                preparsed_certificate_records={cert_path: certificate_records},
            )
        source = artifact_paths.get("source")
        if not source:
            raise ValueError("source artifact anchor is missing")
        source_trace = _source_frame_trace(Path(source), len(events))
        if len(source_trace) != len(events):
            raise ValueError("source frame prefix is unavailable")
        for index, event in enumerate(events):
            digest = event.get("source_frame_sha256")
            if digest != source_trace[index]["source_frame_sha256"]:
                raise ValueError("event source frame hash does not match source prefix")
            if event.get("frame") not in (None, index):
                raise ValueError("event frame index is not a prefix")
        base.update(
            events=events,
            partial_events=events,
            frames_processed=len(events),
            frame_trace=[
                {"frame": i, "source_frame_sha256": e["source_frame_sha256"]}
                for i, e in enumerate(events)
            ],
            artifact_refs=[str(events_path), str(cert_path)]
            if cert_path.exists()
            else [str(events_path)],
            error_code=None,
        )
        return base
    except Exception as exc:
        base.update(error_code="RECOVERY_INVALID", error=str(exc))
        return base


@dataclass(frozen=True)
class RenderSopAdapter:
    policy: str = "proposed"
    processor_name: str | None = None
    detector_kind: str = "grounding_dino"
    model_path: str | None = None
    yolo_image_size: int = 1280
    yolo_iou_threshold: float = 0.50
    segmenter_path: str | None = None
    font_path: str | None = None
    task_config_path: str | None = None
    output_root: str | None = None
    device: str = "auto"
    calibration_artifact: str | None = None
    expected_calibration_artifact_hash: str | None = None
    expected_partition_hash: str | None = None
    raw_risk_threshold: float | None = None
    model_hash: str | None = None
    config_hash: str | None = None
    name: str = "render_sop"

    def __post_init__(self) -> None:
        if self.policy not in {
            "fixed_streak",
            "proposed",
            "uncalibrated_certificate",
            "no_calibration",
        }:
            raise ValueError("unsupported decision policy")
        if self.detector_kind not in {"grounding_dino", "yolo"}:
            raise ValueError("unsupported detector_kind")

    def __call__(
        self, video: str | Path, seed: int = 0, retry: int = 0, **kwargs: Any
    ) -> dict[str, Any]:
        path = Path(video)
        if not path.is_file():
            return _result(
                self.name, "unavailable", error_code="VIDEO_MISSING", error=str(path)
            )
        if self.policy == "proposed":
            if not self.calibration_artifact:
                return _result(
                    self.name, "unavailable", error_code="CALIBRATION_ARTIFACT_MISSING"
                )
            if (
                not self.expected_calibration_artifact_hash
                or not self.expected_partition_hash
            ):
                return _result(
                    self.name, "unavailable", error_code="CALIBRATION_ANCHOR_MISSING"
                )
            if (
                len(self.expected_calibration_artifact_hash) != 64
                or len(self.expected_partition_hash) != 64
                or any(
                    c not in "0123456789abcdef"
                    for c in self.expected_calibration_artifact_hash
                )
                or any(
                    c not in "0123456789abcdef" for c in self.expected_partition_hash
                )
            ):
                return _result(
                    self.name, "unavailable", error_code="CALIBRATION_ANCHOR_INVALID"
                )
            if not Path(self.calibration_artifact).is_file():
                return _result(
                    self.name, "unavailable", error_code="CALIBRATION_ARTIFACT_MISSING"
                )
        effective_policy = (
            "uncalibrated_certificate"
            if self.policy == "no_calibration"
            else self.policy
        )
        if effective_policy == "uncalibrated_certificate":
            if self.calibration_artifact is not None:
                return _result(
                    self.name,
                    "unavailable",
                    error_code="CALIBRATION_ARTIFACT_FORBIDDEN",
                )
            if (
                not isinstance(self.raw_risk_threshold, float)
                or not 0.0 <= float(self.raw_risk_threshold) <= 1.0
            ):
                return _result(
                    self.name,
                    "unavailable",
                    error_code="RAW_RISK_THRESHOLD_MISSING_OR_INVALID",
                )
        if self.processor_name:
            try:
                processor = _resolve_processor(self.processor_name)
                return _normalize(
                    self.name,
                    processor(
                        path,
                        policy=effective_policy,
                        method=self.name,
                        seed=seed,
                        retry=retry,
                        **kwargs,
                    ),
                )
            except Exception as exc:
                return _result(
                    self.name, "crash", error_code="PROCESSOR_ERROR", error=str(exc)
                )
        if (
            not self.output_root
            or not self.font_path
            or not self.model_path
            or not self.segmenter_path
            or not self.task_config_path
        ):
            return _result(
                self.name, "unavailable", error_code="RUNTIME_RESOURCES_UNAVAILABLE"
            )
        missing = [
            value
            for value in (
                self.font_path,
                self.model_path,
                self.segmenter_path,
                self.task_config_path,
            )
            if value is None or not Path(value).exists()
        ]
        if missing:
            return _result(
                self.name,
                "unavailable",
                error_code="RUNTIME_RESOURCE_MISSING",
                error=", ".join(str(value) for value in missing),
            )
        if effective_policy in {"proposed", "uncalibrated_certificate"} and (
            not self.model_hash or not self.config_hash
        ):
            return _result(
                self.name, "unavailable", error_code="PROVENANCE_ANCHOR_MISSING"
            )
        try:
            from display_project.tracking.real_sop_zh import render_real_sop_video_zh
            from display_project.risk import RiskCalibrator

            calibrator = (
                None
                if self.policy != "proposed"
                else RiskCalibrator.from_artifact(
                    self.calibration_artifact,
                    expected_partition_hash=self.expected_partition_hash,
                    expected_artifact_hash=self.expected_calibration_artifact_hash,
                )
            )
            outdir = Path(self.output_root)
            outdir.mkdir(parents=True, exist_ok=True)
            supplied = kwargs.get("attempt_artifacts")
            if supplied is not None:
                if not isinstance(supplied, Mapping):
                    return _result(
                        self.name, "crash", error_code="ARTIFACT_PATHS_INVALID"
                    )
                try:
                    output = Path(supplied["output"])
                    events = Path(supplied["events"])
                    certificate_path = Path(supplied["certificates"])
                except (KeyError, TypeError, ValueError) as exc:
                    return _result(
                        self.name,
                        "crash",
                        error_code="ARTIFACT_PATHS_INVALID",
                        error=str(exc),
                    )
            else:
                output, events, certificate_path = _artifact_paths(
                    outdir, path, self.name, seed, retry
                )
            stem = output.stem
            if output.exists() or events.exists() or certificate_path.exists():
                return _result(
                    self.name,
                    "unavailable",
                    error_code="OUTPUT_ALREADY_EXISTS",
                    error=stem,
                )
            from display_project.segmentation import Sam2BoxSegmenter

            detector = _build_detector(
                self.detector_kind,
                self.model_path,
                device=self.device,
                yolo_image_size=self.yolo_image_size,
                yolo_iou_threshold=self.yolo_iou_threshold,
            )
            segmenter = Sam2BoxSegmenter(self.segmenter_path, device=self.device)
            result = render_real_sop_video_zh(
                path,
                output,
                events,
                detector,
                segmenter,
                font_path=self.font_path,
                task_config=self.task_config_path,
                risk_calibrator=calibrator,
                calibration_version=(
                    calibrator.calibration_version if calibrator is not None else None
                ),
                risk_threshold=(
                    calibrator.risk_threshold if calibrator is not None else None
                ),
                decision_policy=effective_policy,
                raw_risk_threshold=self.raw_risk_threshold,
                model_hash=self.model_hash,
                config_hash=self.config_hash,
                **{
                    key: value
                    for key, value in kwargs.items()
                    if key not in {"attempt_artifacts", "run_context"}
                },
            )
            try:
                rendered_events = [
                    json.loads(line)
                    for line in events.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except (OSError, ValueError) as exc:
                code = (
                    "EVENTS_JSONL_MISSING"
                    if not events.is_file()
                    else "EVENTS_JSONL_INVALID"
                )
                return _normalization_failure(self.name, code, str(exc))
            frame_count = _validated_frame_count(result.get("frames", 0))
            if frame_count is None:
                return _normalization_failure(
                    self.name,
                    "FRAMES_INVALID",
                    "render result frames must be a non-negative integer",
                )
            normalized = _normalize(
                self.name,
                {
                    **result,
                    "status": "success",
                    "events": rendered_events,
                    "frames_processed": frame_count,
                    "frame_trace": _source_frame_trace(path, frame_count),
                    "authorizations": [],
                    "certificates": [],
                    "state_writes": 0,
                    "coverage": {
                        "definitive": 0,
                        "opportunities": len(rendered_events),
                        "value": 0.0,
                    },
                },
            )
            if normalized.get("status") != "success":
                return normalized
            try:
                authorizations, certificates = _parse_render_audit(
                    effective_policy,
                    cast(list[dict[str, Any]], normalized["events"]),
                    expected_input_hash=sha256_file(path),
                    expected_model_hash=self.model_hash,
                    expected_config_hash=self.config_hash,
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                return _normalization_failure(
                    self.name, "RENDER_AUDIT_INVALID", str(exc)
                )
            opportunities = len(cast(list[dict[str, Any]], normalized["events"]))
            normalized["authorizations"] = authorizations
            normalized["certificates"] = certificates
            normalized["state_writes"] = len(authorizations)
            normalized["coverage"] = {
                "definitive": len(authorizations),
                "opportunities": opportunities,
                "value": len(authorizations) / opportunities if opportunities else 0.0,
            }
            return normalized

        except (OSError, FileNotFoundError) as exc:
            return _result(
                self.name,
                "unavailable",
                error_code="RUNTIME_RESOURCES_UNAVAILABLE",
                error=str(exc),
            )
        except ValueError as exc:
            return _result(
                self.name, "unavailable", error_code="CONFIG_INVALID", error=str(exc)
            )
        except Exception as exc:
            partial = _recover_render_partial(
                kwargs.get("attempt_artifacts"),
                expected_input_hash=sha256_file(path) if path.is_file() else None,
                expected_model_hash=self.model_hash,
                expected_config_hash=self.config_hash,
            )
            if partial.get("error_code") is not None:
                partial.pop("error_code", None)
                return _result(
                    self.name,
                    "crash",
                    error_code="RECOVERY_INVALID",
                    error=f"{exc}; {partial.get('error')}",
                    **partial,
                )
            partial.pop("error_code", None)
            return _result(
                self.name, "crash", error_code="RENDER_ERROR", error=str(exc), **partial
            )


__all__ = [
    "ExplicitUnavailableAdapter",
    "VlmAuditOnlyAdapter",
    "RenderSopAdapter",
    "software_fixture_processor",
]
