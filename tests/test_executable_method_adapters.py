import pytest
import json
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from display_project.experiments.paired_runner import (
    PairedExperimentRunner,
    _normalise_child_result,
)
from display_project.provenance import sidecar_record
from display_project.risk import RiskCalibrator
from display_project.sop.task_config import load_task_config
from display_project.sop.visual_evidence import (
    ObjectObservation,
    VisualEvidenceResult,
)
from display_project.tracking.real_sop_zh import evaluate_and_commit_frame
from scripts.validate_experiment_ledger import validate

from display_project.experiments.adapters import (
    _normalize,
    ExplicitUnavailableAdapter,
    RenderSopAdapter,
    VlmAuditOnlyAdapter,
    _artifact_paths,
    _build_detector,
    _parse_render_audit,
    _validated_frame_count,
)
from display_project.detection import resolve_device


def _video(path: Path, frames: int = 3) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5, (32, 24))
    for _ in range(frames):
        writer.write(np.zeros((24, 32, 3), dtype=np.uint8))
    writer.release()


def _coverage(n: int, definitive: int = 0) -> dict[str, float | int]:
    return {
        "definitive": definitive,
        "opportunities": n,
        "value": definitive / n if n else 0.0,
    }


def test_explicit_unavailable_fails_closed() -> None:
    result = ExplicitUnavailableAdapter("hmm")("missing.mp4")
    assert result["status"] == "unavailable"
    assert result["error_code"] == "METHOD_UNAVAILABLE"


def test_runner_preserves_explicitly_unavailable_hmm() -> None:
    result = ExplicitUnavailableAdapter("hmm_or_crf")("missing.mp4")
    normalized = _normalise_child_result(
        result,
        expected=1,
        decoded=1,
        decoded_hashes=["0" * 64],
        video_hash="1" * 64,
        method="hmm_or_crf",
    )
    assert normalized[0] == "unavailable"
    assert normalized[5] == "METHOD_UNAVAILABLE"


def test_vlm_audit_emits_one_unknown_event_per_frame(tmp_path: Path) -> None:
    video = tmp_path / "x.avi"
    _video(video)
    result = VlmAuditOnlyAdapter()(video)
    assert result["status"] == "success"
    assert result["frames_processed"] == 3
    assert len(result["events"]) == 3
    assert {event["decision"] for event in result["events"]} == {"unknown"}
    assert result["state_writes"] == 0
    assert result["certificates"] == []
    assert result["coverage"] == _coverage(3)


def test_normalize_missing_or_bad_jsonl_cannot_succeed(tmp_path: Path) -> None:
    base = {
        "status": "success",
        "frames_processed": 0,
        "authorizations": [],
        "certificates": [],
        "coverage": _coverage(0),
    }
    missing = _normalize("x", {**base, "events": tmp_path / "missing.jsonl"})
    assert missing["status"] == "crash"
    assert missing["error_code"] == "EVENTS_JSONL_MISSING"
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json}\n", encoding="utf-8")
    invalid = _normalize("x", {**base, "events": bad})
    assert invalid["status"] == "crash"
    assert invalid["error_code"] == "EVENTS_JSONL_INVALID"


def test_normalize_rejects_non_list_events_and_bad_coverage() -> None:
    invalid_events = _normalize(
        "x",
        {
            "status": "success",
            "frames_processed": 0,
            "events": {"x": 1},
            "authorizations": [],
            "certificates": [],
            "coverage": _coverage(0),
        },
    )
    assert invalid_events["status"] == "crash"
    assert invalid_events["error_code"] == "EVENTS_SCHEMA_INVALID"
    invalid_coverage = _normalize(
        "x",
        {
            "status": "success",
            "frames_processed": 0,
            "events": [],
            "authorizations": [],
            "certificates": [],
            "coverage": 0.0,
        },
    )
    assert invalid_coverage["status"] == "crash"
    assert invalid_coverage["error_code"] == "COVERAGE_SCHEMA_INVALID"


def _event(
    *,
    authorized: bool,
    policy: str,
    sidecar: Path | None = None,
    cert_hash: str | None = None,
) -> dict[str, object]:
    return {
        "event_id": "e1",
        "decision": "normal" if authorized else "unknown",
        "observed_step": "pick" if authorized else None,
        "current_step": "pick" if authorized else None,
        "evidence_source": "deterministic" if authorized else "none",
        "evidence": [{"value": {"coverage_eligible": authorized}}],
        "certificate_hash": cert_hash,
        "certificate_artifact_ref": str(sidecar) if sidecar else None,
        "policy": policy,
    }


def test_fixed_policy_event_transition_and_commit_are_consistent() -> None:
    task = load_task_config("configs/tasks/meccano.yaml")
    machine = task.state_machine()
    step = task.steps[0]
    result = evaluate_and_commit_frame(
        machine=machine,
        task=task,
        visual=VisualEvidenceResult(step, step, 4, (), {}),
        observations=(ObjectObservation("metal bar", 0.95, (1, 1, 20, 20)),),
        decision_policy="fixed_streak",
    )
    assert result["committed"] is True
    assert result["certificate"] is None
    assert result["transition"].current_step == step
    assert result["event"]["current_step"] == step
    assert result["event"]["observed_step"] == step


def test_proposed_uses_loaded_calibrator_metadata_unless_expected_is_explicit() -> None:
    task = load_task_config("configs/tasks/meccano.yaml")
    step = task.steps[0]
    calibrator = RiskCalibrator(calibration_version="cal-v2", risk_threshold=0.4).fit(
        [0.0], [0.0]
    )

    def run(**expected):
        return evaluate_and_commit_frame(
            machine=task.state_machine(),
            task=task,
            visual=VisualEvidenceResult(step, step, 4, (), {}),
            observations=(ObjectObservation("metal bar", 0.95, (1, 1, 20, 20)),),
            decision_policy="proposed",
            risk_calibrator=calibrator,
            input_hash="0" * 64,
            model_hash="1" * 64,
            config_hash="2" * 64,
            **expected,
        )

    accepted = run()
    assert accepted["committed"] is True
    assert accepted["certificate"].calibration_version == "cal-v2"
    assert accepted["certificate"].risk_threshold == 0.4
    assert run(calibration_version="wrong")["committed"] is False
    assert run(risk_threshold=0.5)["committed"] is False


def _run_uncalibrated(threshold: float, *, review=None, reasons=()):
    task = load_task_config("configs/tasks/meccano.yaml")
    machine = task.state_machine()
    step = task.steps[0]
    return evaluate_and_commit_frame(
        machine=machine,
        task=task,
        visual=VisualEvidenceResult(step, step, 4, reasons, {}),
        observations=(ObjectObservation("metal bar", 0.95, (1, 1, 20, 20)),),
        decision_policy="uncalibrated_certificate",
        raw_risk_threshold=threshold,
        review=review,
        input_hash="0" * 64,
        model_hash="1" * 64,
        config_hash="2" * 64,
    )


def test_uncalibrated_raw_risk_threshold_both_sides() -> None:
    accepted = _run_uncalibrated(0.10)
    assert accepted["committed"] is True
    assert accepted["certificate_result"].valid is True
    assert accepted["certificate"].method == "NoCalibrationCertificate"
    assert accepted["certificate"].calibration_version == "UNCALIBRATED_RAW_V1"
    assert abs(accepted["certificate"].risk_score - 0.05) < 1e-12
    rejected = _run_uncalibrated(0.01)
    assert rejected["committed"] is False
    assert rejected["transition"].decision == "unknown"
    assert rejected["certificate"] is None


def test_uncalibrated_reviews_are_audit_only_but_visual_negative_abstains() -> None:
    assert _run_uncalibrated(0.10, review={"observed_action": "other"})["committed"]
    assert _run_uncalibrated(0.10, review={"annotation_used": True})["committed"]
    assert not _run_uncalibrated(0.10, reasons=("missing_component",))["committed"]


def test_fixed_authorization_is_event_derived_and_certificate_free() -> None:
    event = _event(authorized=True, policy="fixed_streak")
    authorizations, certificates = _parse_render_audit("fixed_streak", [event])
    assert authorizations == [{"event_id": "e1", "certificate_hash": None}]
    assert certificates == []


def test_proposed_authorization_requires_matching_sidecar(tmp_path: Path) -> None:
    task = load_task_config("configs/tasks/meccano.yaml")
    machine = task.state_machine()
    step = task.steps[0]
    result = evaluate_and_commit_frame(
        machine=machine,
        task=task,
        visual=VisualEvidenceResult(step, step, 4, (), {}),
        observations=(ObjectObservation("metal bar", 0.95, (1, 1, 20, 20)),),
        decision_policy="proposed",
        risk_calibrator=RiskCalibrator().fit([0.0], [0.0]),
        input_hash="0" * 64,
        model_hash="1" * 64,
        config_hash="2" * 64,
        risk_threshold=0.5,
        calibration_version="frozen-v1",
    )
    cert, graph = result["certificate"], result["graph"]
    sidecar = tmp_path / "records.jsonl"
    sidecar.write_text(json.dumps(sidecar_record(cert, graph)) + "\n", encoding="utf-8")
    event = dict(result["event"])
    event["certificate_hash"] = cert.certificate_hash
    event["graph_digest"] = cert.graph_digest
    event["certificate_artifact_ref"] = str(sidecar)
    authorizations, certificates = _parse_render_audit("proposed", [event])
    assert len(authorizations) == len(certificates) == 1
    assert _parse_render_audit(
        "proposed",
        [event],
        expected_input_hash="0" * 64,
        expected_model_hash="1" * 64,
        expected_config_hash="2" * 64,
    )[0]
    for kwargs in (
        {"expected_model_hash": "f" * 64},
        {"expected_model_hash": None},
    ):
        try:
            _parse_render_audit(
                "proposed",
                [event],
                expected_input_hash="0" * 64,
                expected_config_hash="2" * 64,
                **kwargs,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("external certificate provenance must fail closed")


def test_proposed_rejects_forged_or_extra_sidecar_record(tmp_path: Path) -> None:
    task = load_task_config("configs/tasks/meccano.yaml")
    machine = task.state_machine()
    step = task.steps[0]
    result = evaluate_and_commit_frame(
        machine=machine,
        task=task,
        visual=VisualEvidenceResult(step, step, 4, (), {}),
        observations=(ObjectObservation("metal bar", 0.95, (1, 1, 20, 20)),),
        decision_policy="proposed",
        risk_calibrator=RiskCalibrator().fit([0.0], [0.0]),
        input_hash="0" * 64,
        model_hash="1" * 64,
        config_hash="2" * 64,
        risk_threshold=0.5,
        calibration_version="frozen-v1",
    )
    cert, graph = result["certificate"], result["graph"]
    forged = sidecar_record(cert, graph)
    forged["certificate"]["certificate_hash"] = "f" * 64
    sidecar = tmp_path / "forged.jsonl"
    sidecar.write_text(json.dumps(forged) + "\n", encoding="utf-8")
    event = dict(result["event"])
    event.update(
        certificate_hash="f" * 64,
        graph_digest=cert.graph_digest,
        certificate_artifact_ref=str(sidecar),
    )
    try:
        _parse_render_audit("proposed", [event])
    except ValueError as exc:
        assert "verification failed" in str(exc)
    else:
        raise AssertionError("forged sidecar must fail closed")


def test_recovery_preparsed_certificates_allow_one_verified_trailing_orphan(
    tmp_path: Path,
) -> None:
    result = _run_uncalibrated(0.10)
    cert, graph = result["certificate"], result["graph"]
    event = dict(result["event"])
    sidecar = tmp_path / "records.jsonl"
    event.update(
        certificate_hash=cert.certificate_hash,
        graph_digest=cert.graph_digest,
        previous_step=cert.current_step,
        certificate_artifact_ref=str(sidecar),
    )
    orphan_result = _run_uncalibrated(0.20)
    orphan_cert, orphan_graph = orphan_result["certificate"], orphan_result["graph"]
    records = [sidecar_record(cert, graph), sidecar_record(orphan_cert, orphan_graph)]
    sidecar.write_text("", encoding="utf-8")
    authorizations, certificates = _parse_render_audit(
        "uncalibrated_certificate",
        [event],
        expected_input_hash="0" * 64,
        expected_model_hash="1" * 64,
        expected_config_hash="2" * 64,
        allow_single_trailing_orphan=True,
        preparsed_certificate_records={sidecar: records},
    )
    assert len(authorizations) == 1
    assert [record["certificate"]["certificate_hash"] for record in records] == [
        record["certificate"]["certificate_hash"] for record in certificates
    ]


def test_recovery_preparsed_certificates_reject_middle_or_multiple_orphans(
    tmp_path: Path,
) -> None:
    result = _run_uncalibrated(0.10)
    cert, graph = result["certificate"], result["graph"]
    event = dict(result["event"])
    sidecar = tmp_path / "records.jsonl"
    event.update(
        certificate_hash=cert.certificate_hash,
        graph_digest=cert.graph_digest,
        previous_step=cert.current_step,
        certificate_artifact_ref=str(sidecar),
    )
    first_orphan = _run_uncalibrated(0.20)
    second_orphan = _run_uncalibrated(0.30)
    records = [
        sidecar_record(first_orphan["certificate"], first_orphan["graph"]),
        sidecar_record(cert, graph),
        sidecar_record(second_orphan["certificate"], second_orphan["graph"]),
    ]
    sidecar.write_text("", encoding="utf-8")
    kwargs = dict(
        expected_input_hash="0" * 64,
        expected_model_hash="1" * 64,
        expected_config_hash="2" * 64,
        allow_single_trailing_orphan=True,
        preparsed_certificate_records={sidecar: records},
    )
    with pytest.raises(ValueError, match="non-tail extras|missing, reordered"):
        _parse_render_audit("uncalibrated_certificate", [event], **kwargs)
    with pytest.raises(ValueError, match="non-tail extras|missing, reordered"):
        _parse_render_audit(
            "uncalibrated_certificate",
            [event],
            **{
                **kwargs,
                "preparsed_certificate_records": {
                    sidecar: [records[1], records[2], records[0]]
                },
            },
        )


def test_trailing_orphan_allowance_is_recovery_only(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="recovery-only"):
        _parse_render_audit("proposed", [], allow_single_trailing_orphan=True)


def test_uncalibrated_certificate_audit_rejects_forged_method_or_version(
    tmp_path: Path,
) -> None:
    result = _run_uncalibrated(0.10)
    cert, graph = result["certificate"], result["graph"]
    for field, value in (("method", "Proposed"), ("calibration_version", "forged")):
        forged = replace(cert, **{field: value}, certificate_hash="")
        forged = replace(forged, certificate_hash=forged.digest())
        sidecar = tmp_path / f"{field}.jsonl"
        sidecar.write_text(
            json.dumps(sidecar_record(forged, graph)) + "\n", encoding="utf-8"
        )
        event = dict(result["event"])
        event.update(
            certificate_hash=forged.certificate_hash,
            graph_digest=forged.graph_digest,
            certificate_artifact_ref=str(sidecar),
        )
        try:
            _parse_render_audit("uncalibrated_certificate", [event])
        except ValueError as exc:
            assert ("method" if field == "method" else "version") in str(exc)
        else:
            raise AssertionError(f"forged {field} must fail closed")


def test_render_frame_count_validation_fails_closed() -> None:
    assert _validated_frame_count(0) == 0
    assert _validated_frame_count(3) == 3
    assert _validated_frame_count(-1) is None
    assert _validated_frame_count(True) is None
    assert _validated_frame_count("3") is None


def test_auto_device_resolves_to_available_torch_backend() -> None:
    class Cuda:
        def __init__(self, available: bool) -> None:
            self.available = available

        def is_available(self) -> bool:
            return self.available

    class Torch:
        def __init__(self, available: bool) -> None:
            self.cuda = Cuda(available)

    assert resolve_device("auto", Torch(True)) == "cuda"
    assert resolve_device("auto", Torch(False)) == "cpu"
    assert resolve_device("cpu", Torch(True)) == "cpu"


def test_spawn_importable_builtin_processor_runs_complete_video(tmp_path: Path) -> None:
    video = tmp_path / "x.avi"
    _video(video)
    adapter = RenderSopAdapter(
        policy="no_calibration",
        raw_risk_threshold=0.5,
        processor_name="display_project.experiments.adapters:software_fixture_processor",
    )
    result = adapter(video, 7, 1)
    assert result["status"] == "success"
    assert result["frames_processed"] == 3
    assert len(result["events"]) == 3
    assert result["coverage"] == _coverage(3)


def test_builtin_processor_survives_paired_runner_spawn(tmp_path: Path) -> None:
    video = tmp_path / "spawn.avi"
    _video(video)
    adapter = RenderSopAdapter(
        name="vlm_only",
        policy="no_calibration",
        raw_risk_threshold=0.5,
        processor_name="display_project.experiments.adapters:software_fixture_processor",
    )
    runner = PairedExperimentRunner(
        {"vlm_only": adapter}, timeout_s=20, frozen_schedule=("vlm_only",)
    )
    run = runner.run_video(video, expected_frames=3)
    assert run.attempts[0].status == "success"
    assert run.attempts[0].frames_processed == 3


def test_paired_runner_drains_large_spawn_result_before_join(tmp_path: Path) -> None:
    video = tmp_path / "spawn-large.avi"
    ledger = tmp_path / "spawn-large.jsonl"
    _video(video, frames=225)
    adapter = RenderSopAdapter(
        name="vlm_only",
        policy="no_calibration",
        raw_risk_threshold=0.5,
        processor_name="display_project.experiments.adapters:software_fixture_processor",
    )
    runner = PairedExperimentRunner(
        {"vlm_only": adapter},
        timeout_s=5,
        frozen_schedule=("vlm_only",),
        ledger_path=ledger,
    )

    run = runner.run_video(video, expected_frames=225)

    assert run.attempts[0].status == "success"
    assert run.attempts[0].frames_processed == 225
    assert validate(ledger, expected_methods=["vlm_only"]) == []


def test_yolo_factory_never_constructs_grounding_dino(monkeypatch) -> None:
    import display_project.detection as detection

    called = []

    class Yolo:
        def __init__(self, *args, **kwargs):
            called.append(("yolo", args, kwargs))

    class Grounding:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Grounding DINO must not be constructed for yolo")

    monkeypatch.setattr(detection, "UltralyticsYoloDetector", Yolo)
    monkeypatch.setattr(detection, "GroundingDinoDetector", Grounding)
    detector = _build_detector(
        "yolo", "model.pt", device="cpu", yolo_image_size=640, yolo_iou_threshold=0.4
    )
    assert isinstance(detector, Yolo)
    assert called[0][0] == "yolo"


def test_artifact_paths_isolate_method_seed_and_retry(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    first = _artifact_paths(tmp_path, video, "proposed", 1, 0)
    retry = _artifact_paths(tmp_path, video, "proposed", 1, 1)
    baseline = _artifact_paths(tmp_path, video, "fixed_streak", 1, 0)
    assert len({first[0], retry[0], baseline[0]}) == 3
    assert "seed-1.retry-0" in first[0].name


def test_detector_kind_is_strictly_allowlisted() -> None:
    try:
        RenderSopAdapter(policy="no_calibration", detector_kind="not-yolo")
    except ValueError as exc:
        assert "detector_kind" in str(exc)
    else:
        raise AssertionError("unsupported detector must be rejected")
