from __future__ import annotations

import argparse
import json

from display_project.gate_audit import (
    build_joint_gate_decision,
    load_json,
    write_decision,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the joint G1/G2 gate")
    parser.add_argument(
        "--preflight", default="outputs/paired-development-example.preflight.json"
    )
    parser.add_argument(
        "--dataset-qa", default="data/yolo/meccano-small-objects/dataset-qa.json"
    )
    parser.add_argument(
        "--candidate-manifest",
        default="research/audits/meccano-yolo-candidate-split-manifest.json",
    )
    parser.add_argument(
        "--industreal-capability",
        default="research/audits/industreal-capability.json",
    )
    parser.add_argument(
        "--cvat-audit", default="research/audits/cvat-meccano-batch-001.json"
    )
    parser.add_argument(
        "--calibration-artifact",
        default="models/calibration/frozen-calibrator.json",
    )
    parser.add_argument("--hmm-artifact", default="models/hmm-or-crf/fitted.json")
    parser.add_argument("--yolo-artifact", default="models/yolo-small-objects/best.pt")
    parser.add_argument("--output", default="research/audits/g1-g2-joint-gate.json")
    args = parser.parse_args()

    decision = build_joint_gate_decision(
        preflight=load_json(args.preflight),
        dataset_qa=load_json(args.dataset_qa),
        candidate_manifest=load_json(args.candidate_manifest),
        preflight_path=args.preflight,
        dataset_qa_path=args.dataset_qa,
        candidate_manifest_path=args.candidate_manifest,
        industreal_capability=load_json(args.industreal_capability),
        industreal_capability_path=args.industreal_capability,
        cvat_audit=load_json(args.cvat_audit),
        cvat_audit_path=args.cvat_audit,
        calibration_artifact=args.calibration_artifact,
        hmm_artifact=args.hmm_artifact,
        yolo_artifact=args.yolo_artifact,
    )
    write_decision(args.output, decision)
    print(
        json.dumps(
            {
                "status": decision["status"],
                "action_state": decision["action_state"],
                "g1": decision["g1"]["status"],
                "g2": decision["g2"]["status"],
                "execute_g3_method_panel_allowed": decision[
                    "execute_g3_method_panel_allowed"
                ],
                "decision_sha256": decision["decision_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0 if decision["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
