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

## Process evidence and current-state contrast — 2026-10-12

- [Cloud-only contrast and evidence limits](progress/20261012-process-current-state-contrast.md) rechecks the public cached-answer comparison rooted at `64e62dbff44e766551479f080e2a7ba60c613e83` and the recovered-record support tests. The cache distinguishes a direct endpoint record, a current-frame unknown with conditional history, and a later endpoint record that does not relabel the hidden interval.
- The offline package and its existing [Mac restore manifest](../../experiments/process-visible-state-dev-20261011/MANIFEST.json) remain unchanged. Six process-state tests and five recovered-cache support tests passed; a fresh no-model result matched the archived result byte-for-byte. This cloud verification does not mean the package was saved on Mac.
- The q31/q68/q92 answers are related prefixes of one development event, not independent samples or human ground truth. Synthetic occlusion/removal/identity-change cases are logic controls only; none is a real negative or metric denominator. No accuracy, general gain, physical truth, or process-step accuracy is supported.
- [Append-only verification receipt](receipts/process-current-state-contrast-20261012.json). The user-reported Mac archive (403 files, about 34 MB) is separate from this public-code manifest; the private video was not uploaded. The original IndEgo 119–128 s dismantling frame manifest and H/F/HF JSON remain missing.

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
