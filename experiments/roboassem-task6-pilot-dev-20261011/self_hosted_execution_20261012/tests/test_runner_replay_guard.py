"""Synthetic-only checks for Task 6 one-shot source guards."""

from __future__ import annotations

import importlib.util
import hashlib
from pathlib import Path
import tempfile
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RUNNER_SOURCE_ROOT = PACKAGE_ROOT / "runner_sources"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load synthetic test source: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunnerReplayGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.safety = _load_module(
            "task6_bundle_replay_test_safety", RUNNER_SOURCE_ROOT / "runner_safety.py"
        )
        cls.bootstrap = _load_module(
            "task6_bundle_replay_test_bootstrap",
            RUNNER_SOURCE_ROOT / "prepared-execute-pair-bootstrap-crashsafe.py",
        )
        cls.guards = (
            (cls.safety.OneShotRunGuard, cls.safety.PriorAttemptExists),
            (cls.bootstrap.BootstrapRunGuard, RuntimeError),
        )

    def test_bootstrap_pins_the_versioned_runner_and_guard_bytes(self) -> None:
        self.assertEqual(
            self.bootstrap.PREPARED_RUNNER_SHA256,
            hashlib.sha256((RUNNER_SOURCE_ROOT / "prepared-run_pair-crashsafe.py").read_bytes()).hexdigest(),
        )
        self.assertEqual(
            self.bootstrap.SAFETY_MODULE_SHA256,
            hashlib.sha256((RUNNER_SOURCE_ROOT / "runner_safety.py").read_bytes()).hexdigest(),
        )

    def test_empty_results_and_setup_evidence_allow_first_run(self) -> None:
        for guard_type, _error_type in self.guards:
            with self.subTest(guard=guard_type.__name__), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "results"
                (output / "setup-evidence" / "gpu-preflight").mkdir(parents=True)
                (output / "setup-evidence" / "gpu-preflight" / "synthetic.json").write_text(
                    '{"synthetic":true}', encoding="utf-8"
                )
                for filename in ("backend-props.json", "server-stopped.json"):
                    (output / "setup-evidence" / "gpu-preflight" / filename).write_text(
                        '{"synthetic":true}', encoding="utf-8"
                    )
                guard = guard_type(output).acquire()
                self.assertTrue(guard.acquired)
                guard.release()
                self.assertFalse(guard.acquired)

    def test_orphaned_execution_artifacts_block_both_guards_and_release_lock(self) -> None:
        artifacts = (
            ("response-F-raw.json", "synthetic response bytes"),
            ("execution-error.json", '{"synthetic":true}'),
            ("results.json", "[]"),
            ("attempt-events.jsonl", ""),
            ("failures.jsonl", ""),
        )
        for guard_type, error_type in self.guards:
            for artifact, payload in artifacts:
                with self.subTest(guard=guard_type.__name__, artifact=artifact), tempfile.TemporaryDirectory() as tmp:
                    output = Path(tmp) / "results"
                    output.mkdir(parents=True)
                    (output / artifact).write_text(payload, encoding="utf-8")
                    guard = guard_type(output)
                    with self.assertRaisesRegex(error_type, "prior execution artifacts"):
                        guard.acquire()
                    self.assertFalse(guard.acquired)

                    (output / artifact).unlink()
                    retry = guard_type(output).acquire()
                    retry.release()


if __name__ == "__main__":
    unittest.main()
