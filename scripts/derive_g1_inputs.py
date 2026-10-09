"""Derive development HMM and calibration inputs from frozen exports."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from display_project.experiments.g1_derivation import atomic_write, derive_inputs


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--actions", type=Path, required=True)
    p.add_argument("--trace", type=Path, required=True)
    p.add_argument("--action-state-map", type=Path, required=True)
    p.add_argument("--calibration-output", type=Path, required=True)
    p.add_argument("--hmm-output", type=Path, required=True)
    p.add_argument("--sidecar-output", type=Path, required=True)
    p.add_argument("--code-hash", required=True)
    p.add_argument("--model-hash", required=True)
    p.add_argument("--config-hash", required=True)
    a = p.parse_args()
    result = derive_inputs(
        a.manifest,
        a.actions,
        a.trace,
        action_state_map=a.action_state_map,
        code_hash=a.code_hash,
        model_hash=a.model_hash,
        config_hash=a.config_hash,
    )
    atomic_write(a.calibration_output, result["calibration"])
    # HMM fitter consumes one exact JSON object per line.
    a.hmm_output.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.hmm_output.with_name(a.hmm_output.name + ".tmp")
    tmp.write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for x in result["hmm_records"]), encoding="utf-8")
    tmp.replace(a.hmm_output)
    atomic_write(a.sidecar_output, result["sidecar"])
    print(json.dumps(result["sidecar"], ensure_ascii=False, sort_keys=True))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
