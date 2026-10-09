from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from collections.abc import Mapping


@dataclass(frozen=True)
class CalibrationReport:
    brier: float
    ece: float
    n: int


@dataclass(frozen=True)
class RiskEvaluation:
    risk_score: float
    coverage_eligible: bool
    calibration_version: str
    risk_threshold: float


class RiskCalibrator:
    ARTIFACT_SCHEMA = "display_project.risk_calibrator"
    ARTIFACT_SCHEMA_VERSION = 1
    CALIBRATION_SPLIT_ROLE = "CALIBRATION"

    def __init__(self, bins=10, *, calibration_version="frozen-v1", risk_threshold=0.5):
        if not isinstance(bins, int) or isinstance(bins, bool) or bins <= 0:
            raise ValueError("bins must be a positive integer")
        self.bins = bins
        if not isinstance(calibration_version, str) or not calibration_version.strip():
            raise ValueError("calibration_version must be non-empty")
        if (
            isinstance(risk_threshold, bool)
            or not isinstance(risk_threshold, (int, float))
            or not math.isfinite(float(risk_threshold))
            or not 0.0 <= float(risk_threshold) <= 1.0
        ):
            raise ValueError("risk_threshold must be in [0,1]")
        self.calibration_version = calibration_version.strip()
        self.risk_threshold = float(risk_threshold)
        self._offset = 0.0
        self.fitted = False
        self._frozen = False
        self.split_role = self.CALIBRATION_SPLIT_ROLE

    def fit(self, probabilities, labels, *, split="calibration"):
        if str(split).lower() not in {"calibration", "cal", "held_out_calibration"}:
            raise ValueError("risk calibration is calibration-split-only")
        p = list(probabilities)
        y = list(labels)
        if not p or len(p) != len(y):
            raise ValueError("calibration data must be non-empty and aligned")
        if any(
            isinstance(x, bool)
            or not isinstance(x, (int, float))
            or not math.isfinite(float(x))
            or not 0 <= float(x) <= 1
            for x in p
        ):
            raise ValueError("probabilities must be in [0,1]")
        if any(
            isinstance(x, bool)
            or not isinstance(x, (int, float))
            or not math.isfinite(float(x))
            or float(x) not in (0.0, 1.0)
            for x in y
        ):
            raise ValueError("labels must be binary")
        if self._frozen:
            raise RuntimeError("calibration metadata is frozen")
        self._offset = sum(y) / len(y) - sum(p) / len(p)
        self.fitted = True
        self._frozen = True
        return self

    @staticmethod
    def _partition_hash(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError(
                "calibration_partition_hash must be 64 lowercase hexadecimal characters"
            )
        return value

    @staticmethod
    def _digest(value, name):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError(f"{name} must be 64 lowercase hexadecimal characters")
        return value

    @staticmethod
    def _canonical_json(payload):
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    @classmethod
    def _artifact_hash(cls, payload):
        return hashlib.sha256(cls._canonical_json(payload).encode("utf-8")).hexdigest()

    def to_artifact(self, calibration_partition_hash):
        """Return a deterministic, label-free frozen calibration artifact."""
        partition_hash = self._partition_hash(calibration_partition_hash)
        if not self.fitted or not self._frozen:
            raise RuntimeError("only a fitted and frozen calibrator can be exported")
        if self.split_role != self.CALIBRATION_SPLIT_ROLE:
            raise ValueError("split_role must be CALIBRATION")
        if (
            not isinstance(self.bins, int)
            or isinstance(self.bins, bool)
            or self.bins <= 0
        ):
            raise ValueError("bins must be a positive integer")
        if (
            not math.isfinite(self.risk_threshold)
            or not 0.0 <= self.risk_threshold <= 1.0
        ):
            raise ValueError("risk_threshold must be in [0,1]")
        if not math.isfinite(self._offset) or not -1.0 <= self._offset <= 1.0:
            raise ValueError("offset must be in [-1,1]")
        payload = {
            "schema": self.ARTIFACT_SCHEMA,
            "schema_version": self.ARTIFACT_SCHEMA_VERSION,
            "calibration_version": self.calibration_version,
            "bins": self.bins,
            "risk_threshold": self.risk_threshold,
            "offset": self._offset,
            "fitted": True,
            "frozen": True,
            "split_role": self.CALIBRATION_SPLIT_ROLE,
            "calibration_partition_hash": partition_hash,
        }
        return {**payload, "artifact_hash": self._artifact_hash(payload)}

    def write_artifact(self, path, calibration_partition_hash):
        artifact = self.to_artifact(calibration_partition_hash)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)

        def existing_idempotent():
            try:
                with destination.open("r", encoding="utf-8") as handle:
                    existing = json.load(handle)
                if (
                    not isinstance(existing, Mapping)
                    or existing.get("artifact_hash") != artifact["artifact_hash"]
                ):
                    raise FileExistsError(
                        f"refusing to overwrite existing artifact: {destination}"
                    )
                self.from_artifact(
                    existing, expected_partition_hash=calibration_partition_hash
                )
            except FileExistsError:
                raise
            except Exception as exc:
                raise FileExistsError(
                    f"refusing to overwrite existing artifact: {destination}"
                ) from exc
            return artifact

        data = (self._canonical_json(artifact) + "\n").encode("utf-8")
        fd, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                return existing_idempotent()
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
            try:
                directory_fd = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        return artifact

    @classmethod
    def from_artifact(
        cls, path_or_mapping, expected_partition_hash=None, expected_artifact_hash=None
    ):
        if isinstance(path_or_mapping, Mapping):
            artifact = dict(path_or_mapping)
        else:
            try:
                with Path(path_or_mapping).open("r", encoding="utf-8") as handle:
                    artifact = json.load(handle)
            except (OSError, ValueError, TypeError) as exc:
                raise ValueError("invalid calibration artifact") from exc
        if not isinstance(artifact, dict):
            raise ValueError("invalid calibration artifact")
        required = {
            "schema",
            "schema_version",
            "calibration_version",
            "bins",
            "risk_threshold",
            "offset",
            "fitted",
            "frozen",
            "split_role",
            "calibration_partition_hash",
            "artifact_hash",
        }
        if set(artifact) != required:
            raise ValueError("calibration artifact schema mismatch")
        payload = {key: artifact[key] for key in required if key != "artifact_hash"}
        if (
            artifact["schema"] != cls.ARTIFACT_SCHEMA
            or artifact["schema_version"] != cls.ARTIFACT_SCHEMA_VERSION
        ):
            raise ValueError("unsupported calibration artifact schema")
        if artifact["split_role"] != cls.CALIBRATION_SPLIT_ROLE:
            raise ValueError("split_role must be CALIBRATION")
        partition_hash = cls._partition_hash(artifact["calibration_partition_hash"])
        if (
            expected_partition_hash is not None
            and partition_hash != cls._partition_hash(expected_partition_hash)
        ):
            raise ValueError("calibration partition hash mismatch")
        if expected_artifact_hash is not None:
            expected_artifact_hash = cls._digest(
                expected_artifact_hash, "expected_artifact_hash"
            )
            if artifact["artifact_hash"] != expected_artifact_hash:
                raise ValueError("calibration artifact hash mismatch")
        if artifact["fitted"] is not True or artifact["frozen"] is not True:
            raise ValueError("artifact must be fitted and frozen")
        if (
            not isinstance(artifact["bins"], int)
            or isinstance(artifact["bins"], bool)
            or artifact["bins"] <= 0
        ):
            raise ValueError("bins must be a positive integer")
        threshold, offset = artifact["risk_threshold"], artifact["offset"]
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(float(threshold))
            or not 0.0 <= float(threshold) <= 1.0
        ):
            raise ValueError("risk_threshold must be in [0,1]")
        if (
            isinstance(offset, bool)
            or not isinstance(offset, (int, float))
            or not math.isfinite(float(offset))
            or not -1.0 <= float(offset) <= 1.0
        ):
            raise ValueError("offset must be in [-1,1]")
        if (
            not isinstance(artifact["calibration_version"], str)
            or not artifact["calibration_version"].strip()
            or artifact["calibration_version"]
            != artifact["calibration_version"].strip()
        ):
            raise ValueError("calibration_version must be non-empty")
        if artifact["artifact_hash"] != cls._artifact_hash(payload):
            raise ValueError("calibration artifact hash mismatch")
        calibrator = cls(
            artifact["bins"],
            calibration_version=artifact["calibration_version"],
            risk_threshold=float(threshold),
        )
        calibrator._offset, calibrator.fitted, calibrator._frozen = (
            float(offset),
            True,
            True,
        )
        calibrator.split_role = cls.CALIBRATION_SPLIT_ROLE
        return calibrator

    def evaluate(self, probability):
        score = self.calibrate(probability)
        return RiskEvaluation(
            score,
            score <= self.risk_threshold,
            self.calibration_version,
            self.risk_threshold,
        )

    def calibrate(self, probability):
        if not self.fitted:
            raise RuntimeError("calibrator is not fitted")
        if (
            isinstance(probability, bool)
            or not isinstance(probability, (int, float))
            or not math.isfinite(float(probability))
            or not 0 <= float(probability) <= 1
        ):
            raise ValueError("probability must be in [0,1]")
        return min(1.0, max(0.0, float(probability) + self._offset))

    @staticmethod
    def brier_score(probabilities, labels):
        p = list(probabilities)
        y = list(labels)
        if not p or len(p) != len(y):
            raise ValueError("aligned non-empty inputs required")
        if any(
            isinstance(a, bool)
            or not isinstance(a, (int, float))
            or not math.isfinite(float(a))
            or not 0 <= float(a) <= 1
            for a in p
        ):
            raise ValueError("probabilities must be in [0,1]")
        if any(
            isinstance(b, bool)
            or not isinstance(b, (int, float))
            or not math.isfinite(float(b))
            or float(b) not in (0.0, 1.0)
            for b in y
        ):
            raise ValueError("labels must be binary")
        return sum((float(a) - float(b)) ** 2 for a, b in zip(p, y)) / len(p)

    def ece(self, probabilities, labels):
        p = list(probabilities)
        y = list(labels)
        if not p or len(p) != len(y):
            raise ValueError("aligned non-empty inputs required")
        if any(
            isinstance(a, bool)
            or not isinstance(a, (int, float))
            or not math.isfinite(float(a))
            or not 0 <= float(a) <= 1
            for a in p
        ):
            raise ValueError("probabilities must be in [0,1]")
        if any(
            isinstance(b, bool)
            or not isinstance(b, (int, float))
            or not math.isfinite(float(b))
            or float(b) not in (0.0, 1.0)
            for b in y
        ):
            raise ValueError("labels must be binary")
        total = 0.0
        for i in range(self.bins):
            vals = [
                (a, b)
                for a, b in zip(p, y)
                if i / self.bins <= a < (i + 1) / self.bins
                or (i == self.bins - 1 and a == 1)
            ]
            if vals:
                total += (
                    len(vals)
                    / len(p)
                    * abs(
                        sum(a for a, _ in vals) / len(vals)
                        - sum(b for _, b in vals) / len(vals)
                    )
                )
        return total

    def report(self, probabilities, labels):
        p = list(probabilities)
        y = list(labels)
        return CalibrationReport(self.brier_score(p, y), self.ece(p, y), len(p))
