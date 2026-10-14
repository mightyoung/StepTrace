# Research handoff v1

This directory exchanges reviewed text and artifact identities across research environments. It does not grant data access, publication rights, network exceptions, or execution authority.

- Pin source repository and commit; independently verify artifact SHA-256 and byte length before use. A metadata entry is not proof that bytes arrived.
- One named task owner writes each task record. Changes use reviewable commits/PRs; immutable execution receipts belong under receipts/<attempt_id>.json. Never overwrite earlier failures or frozen research evidence.
- Separate claimed, executed and independently verified states. Null command/exit/output means no execution receipt is supplied here, not successful execution.
- Record schema/coordinate/time conventions in artifact provenance or linked public contracts. Preserve split, origin, transformations and exposure. AI-assisted human review is not independent blind ground truth.
- Public records must omit private conversation IDs, private asset links, host paths, credentials, raw media and unapproved datasets. A private artifact hash does not publish its contents or authorize fetching it.
- Do not use repository transfers to evade denied network access. LFS and Release availability must be verified separately; neither is required by this protocol.
- No project or third-party license is created or changed here. Unknown redistribution rights remain unknown.
- This initial batch contains status snapshots and metadata only, except the explicitly identified synthetic-smoke text inputs in PCG. Publication approval is not execution approval.

## Latest evidence addendum — 2026-10-11

- [IndEgo candidate review and disposition](progress/20261011-indego-candidate-review.md) records Parent-reported source-byte verification, Mac-reported frame/PTS/hash checks, and the user's suitability decision.
- Status: `EXCLUDED_FROM_PRIMARY_VALIDATION` because the relevant component/interface identity cannot be reliably confirmed. This is not an algorithm failure or ground truth; the frozen H/F/HF experiment remains `NOT_RUN`.
- [Append-only receipt](receipts/indego-candidate-disposition-20261011.json). Raw media, frames, private identifiers, and absolute paths are not included.

## Separate RoboAssem development candidate — 2026-10-11

- User-approved task 6 is prepared as a separate development candidate in [its protocol bundle](../../experiments/roboassem-task6-pilot-dev-20261011/README_zh.md); it does not replace the excluded IndEgo candidate or the frozen experiment.
- The cloud Library materialize attempt failed; a later Mac executor report says the media and F/HF input mapping were saved and hash-checked there. This cloud environment did not inspect pixels or bytes. See the [Mac report](progress/20261011-roboassem-task6-mac-inputs.md) and its [append-only receipt](receipts/roboassem-task6-mac-input-report-20261011.json); the earlier [cloud failure receipt](receipts/roboassem-task6-library-access-20261011.json) remains unchanged.
- Protocol status remains draft/unfrozen; model calls and experiment runs remain at zero.
- Only non-sensitive protocol/source metadata is in the repository; media and frames are not included.

## RoboAssem task 6 execution admission — 2026-10-12

- [Task6 admission package](../../experiments/roboassem-task6-pilot-dev-20261011/admission_20261012/EXECUTION_ADMISSION_20261012_zh.md) pins the Qwen GGUF/projector candidate, the four Mac-reported masked frame identities, independent F/HF contexts, output schema behavior, one-shot budget, shutdown, and private result retention.
- Status is `preparation_only_blocked_for_execution`: the earlier Kaggle record reports successful model/image hash rechecks but the current session and quota are unverified; CUDA build success and `runtime-verification.json` with backend version/binary SHA are absent; v5 source readback in Kaggle is absent. No weight redownload or real inference occurred.
- This is a separate RoboAssem Task6 record. Earlier Mac input and runner receipts remain unchanged; the new [admission receipt](receipts/roboassem-task6-admission-20261012.json) identifies exactly which runtime records are still required.

## RoboAssem task 6 self-hosted package — 2026-10-13

- [Self-hosted execution guide](../../experiments/roboassem-task6-pilot-dev-20261011/self_hosted_execution_20261012/README_zh.md) adds a CPU-only preflight/result audit package, an opt-in local GPU no-generation preflight, and a documented path for a user-selected Linux CUDA server. It reuses the reviewed v5 runner source without modifying it.
- Status remains `preparation_only_blocked_for_execution`: no public video/PNG URL or file-level dataset license was verified; the cloud workspace has none of the real video/PNG/GGUF bytes; rights to use and transfer those inputs remain unverified. The Qwen repository's Apache-2.0 label does not establish media rights.
- Local package fixtures and mocked loopback/server responses are synthetic software tests only. They do not count as real positive/negative examples, ground truth, model output, accuracy or method gain. No model, server, GPU build, GPU preflight or inference ran in this increment.
- See the new [append-only self-hosted package receipt](receipts/roboassem-task6-self-hosted-execution-20261013.json) and [progress note](progress/20261013-roboassem-task6-self-hosted.md). The prior admission receipt and earlier research records remain unchanged.

## RoboAssem task 6 source and rights audit — 2026-10-14

- The [independent source and rights audit](audits/roboassem-task6-source-rights-audit-20261014.md) is a byte-for-byte copy of the Library report, verified at 12,926 bytes with SHA-256 `d84fecde1dc6b7b8d40f3883c01db0d5e8287bedfc3797ec67b4ed9eb60d509d`.
- The audit identifies a fixed first-party video source candidate and matching size metadata, but the video body/hash remain unverified; the inspected fixed source tree contains no original k01–k04 PNGs. Website media-rights scope remains unresolved. Model license and public hash metadata do not establish media rights, and no model weights were downloaded.
- Task status remains `preparation_only_blocked_for_execution`. Exact private inputs/acquisition records, media and derivative-frame rights evidence, and the actual host/provider remain unresolved. This update adds no execution or research-result claim and does not change the admission gate.

## Current stage - 2026-10-10 15:05 UTC

- [Current progress, actual results and remaining evidence gaps](progress/20261010T1505Z-current-stage.md)
- [Current task snapshot](tasks/status-20261010T1505Z.json)
- [Real interface occlusion/reappearance development bundle](../../experiments/real-reappearance-dev-20261010/README_zh.md): three original model JSON outputs, source/frame hashes, independent AI review summary, same-input reversible-FSM replay, code and tests
- Current claim: scoped evidence handling works on this case; both state representations remain equivalent. Hidden-period physical truth and real cross-gap identity recovery remain unverified. Native backend/parameters unknown; no algorithmic gain, accuracy or safety claim
- This increment publishes explicitly authorized model-generated text/data and deterministic software, not images/video, raw tool logs or human GT. Earlier metadata-only restrictions described their earlier batches

## Historical snapshots - retained unchanged

### 2026-10-10 05:43 UTC (superseded current-stage pointer)

- [Current progress and next empirical step](progress/20261010T0543Z-current-stage.md): 24 core first answers plus six separate real/synthetic probes; C03 count and C04 connection/identity references remain the priority
- [Descriptive findings](../experiments/steptrace/development-findings-2026-10-10.md) and [protocol clarification proposal](../experiments/steptrace/protocol-clarifications-proposed.md)
- [Current task snapshot](tasks/status-20261010T0543Z.json): local full-contract revision passed limited software review, but execution_gate remains false; no new model round or human-GT claim


- [2026-10-10 verified progress summary](progress/20261010-progress.md): 12 saved development first answers; two time-semantics defects; unresolved C03 count disagreement; draft contract fixtures only; cross-domain removal screening with no independent human GT; site batch remains 3/56 present.
- [2026-10-10 task state](tasks/status-20261010.json) and [de-identified evidence index](progress/evidence-20261010.json).
- [2026-10-09 historical handoff state](tasks/status-20261009.json) remains unchanged.
