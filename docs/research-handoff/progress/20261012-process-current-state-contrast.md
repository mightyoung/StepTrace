# StepTrace process evidence and current-state contrast — 2026-10-12

This is a cloud-only continuation of the public cached-evidence work rooted at `64e62dbff44e766551479f080e2a7ba60c613e83`. It does not replace or modify the source cache, private Mac materials, or earlier experiment results. The working branch is `task/process-visible-state-evidence-20261012`, based on the reviewed contrast branch at `525a76cf33ca8b1626949a2c2c719cbe870af89d`.

## What the available records support

| Record | Available evidence | Supported statement | Not supported |
|---|---|---|---|
| Public cached answers q31/q68/q92, source commit `955d2bae7f047c5e699caa90ac094b74b9b738e3`, public manifest SHA-256 `4c7638d6dd90119915513af511f19c3b8890e32106a9b911b7a850c9bfebc38c` | Three related cached answer prefixes, six repeated observation rows over three frame IDs, all from one development event | q31 records a direct endpoint relation at f1743; q68 records direct current state as occluded/unknown while retaining the old relation as conditional history; q92 records a later endpoint relation at f1752 while retaining f1746 as unknown | Independent sample count, image-level re-review in this run, physical truth, installation-step completion, safety, or general model performance |
| Recovered MECCANO-0009 cached detector pair in `experiments/recovered-real-cache-dev-20261011/results.json` | Existing result fixture identifies frames 5/6 with the same retained `current_step`; frame 5 has a cached normal confirmation and frame 6 is unknown with no new confirmation | The cache fields distinguish historical progress from a new per-frame confirmation; the lossy `current_step` projection alone collides | A production defect, physical removal, independent human truth, formal-test evidence, or a new visual inference from this cloud rerun |
| Synthetic logic controls | Three declared synthetic sequences for occlusion, separation/removal, and target identity change; a separate synthetic unknown-plus-confirmation mutation test | The code can retain historical facts while leaving current state unknown/conditional, revoke current applicability after explicit separation, and avoid transferring history to a changed identity | Real negative examples, real occlusion/removal/identity-change observations, training/evaluation data, or metric counts |

The cache output labels say `missedprocess`, which is interpreted as the sampled input not supplying process evidence. It does not mean a detector missed an installation action. For all three q records, process-step completion remains `unknown` and no step mapping is supplied. q68's `external_connection_supported` value is an estimate on a `conditional_history` basis; its direct current-frame record is `occluded_or_unknown`. That old connection must not be promoted to an unconditional current connection. q92 supports only its own later endpoint cache record; it does not revise the intervening f1746 state.

The lossy relation-plus-active-history projection collides for q31 and q68. Adding the direct frame record, evidence basis, applicability label, and assumptions separates the cached claims. This is an information-representation result on saved records, not evidence of recognition gain or of a production bug.

The MECCANO-0009 result fixture is a distinct recovered-record comparison and is not pooled with q31/q68/q92. Its five tests inspect saved result fields; this rerun did not open the private video or event log. Both record groups are treated as model/detector outputs, not human or physical ground truth.

## Verification performed in this cloud workspace

Commands run without installing dependencies or accessing a model:

```sh
cd experiments/process-visible-state-dev-20261011
python3 -B -m unittest -v test_process_visible_state.py
python3 -B process_visible_state.py --output /tmp/steptrace-process-visible-results-20261012.json

cd ../recovered-real-cache-dev-20261011
python3 -B -m unittest -v test_progress_support.py
```

Results: 6/6 process/current-state tests and 5/5 recovered-record support tests passed. The fresh process/current-state output matched the checked-in `results.json` byte-for-byte (SHA-256 `a1a80e2bc0cbdff4ba1cfd222340e489e91415b04a8210a0ba83dade0207ac4e`). The process bundle manifest's six artifacts and three cited cached-answer JSON hashes were checked against bytes in this workspace; its SHA-256 is `c02074ee247a03385b78f98dcafd50f3de9be09b5a09143e783b7535e6932347`. The detailed [receipt](../receipts/process-current-state-contrast-20261012.json) records commands and boundaries.

No model calls, training, media downloads, dependency installs, production changes, or scoring runs occurred. The prior test-first record in `experiments/process-visible-state-dev-20261011/verification.json` reports the six contract tests first failed because the CLI was absent, then passed after implementation. This continuation only re-ran the green tests; it did not rewrite source code or alter saved results.

## Data and research limits

- User-reported Mac status: the original Mac author completed the preparation and 403 files (about 34 MB) persist in the Mac research archive. This report did not access or independently hash that archive. The private source video was not uploaded to this cloud workspace.
- The source cache q31/q68/q92 consists of three correlated prefixes from one development event. It supplies cached model-answer content and provenance hashes, not independent ground truth.
- The available 64e recovery bundle includes a two-row cached-record pair from the MECCANO-0009 development artifact, but this cloud rerun only tested its saved result fixture. It does not imply local source-video availability here.
- The original IndEgo 119–128 second dismantling packet remains unavailable: the original frame manifest and matching H/F/HF first-answer JSON have not been recovered. Do not reconstruct those records from reports or descriptions.
- Accuracy, physical false-revocation rate, process-step accuracy, identity-change performance, and method gain remain null/unmeasured. The formal experiment remains unrun.

## Mac restore

Use the existing [process contrast manifest](../../../experiments/process-visible-state-dev-20261011/MANIFEST.json) to verify the public bundle by byte count and SHA-256 after copying. It reports `Mac_backup: pending`; this cloud path and this verification do not establish that anything has been saved on Mac. The user's 403-file Mac archive is a separate set and is not represented by this manifest.

The smallest input needed to resume the missing real dismantling comparison is an accessible path or exact Library filename for the existing IndEgo 119–128 s packet containing its original frame manifest and the corresponding H/F/HF first-answer JSON. No new capture, labeling, upload, model call, or download is needed to identify it.
