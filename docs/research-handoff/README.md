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
- Library materialization failed before local verification. No source bytes, PTS, frames, visual annotations, or model results are claimed. See the [access receipt](receipts/roboassem-task6-library-access-20261011.json).
- Only non-sensitive protocol/source metadata is in the repository; media and frames are not included.

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
