# RoboAssem task 6 — Mac input-preparation report

This is a cross-environment status update, not a claim that this cloud environment opened the video. The earlier cloud Library materialize failure remains in its original [receipt](../receipts/roboassem-task6-library-access-20261011.json). A later Mac executor report says the exact file was saved through Library and reports a matching size/SHA, H.264, 640×480, 30 fps, 547 frames, and time base `1/30`. The Mac report also says 27 related files passed its hash checks. No media, local path, Library identifier, or per-file hash list is included here.

The reported input mapping is:

| Arm | Input | Reported video time | PTS | Time base |
|---|---|---:|---:|---|
| F | Current frame | 17 s | 510 | `1/30` |
| HF | History frame | 0 s | 0 | `1/30` |
| HF | History frame | 10 s | 300 | `1/30` |
| HF | History frame | 15 s | 450 | `1/30` |
| HF | Same current frame as F | 17 s | 510 | `1/30` |

These PTS and hash checks are attributed to the Mac executor report; this cloud environment did not independently inspect pixels or media bytes. Model calls remain **0**. The protocol is still a draft and is not frozen. H remains a last-known-only diagnostic; no H run or outcome exists. See the [updated protocol bundle](../../experiments/roboassem-task6-pilot-dev-20261011/README_zh.md) and the [append-only report receipt](../receipts/roboassem-task6-mac-input-report-20261011.json).

## Mac request-contract addendum (2026-10-11)

A later Mac report supplied the exact system prompt, common question, partial generation parameters, four masked PNG identities, and separate F/HF schema source hashes. These values are bound into the [offline preflight plan](../../experiments/roboassem-task6-pilot-dev-20261011/offline_runner/request_plan.json). The system prompt and question SHA256 values were independently recomputed from the supplied UTF-8 text and match. The Mac-reported pure-text contract file hash `2bb0306715bb33a40ff829c0ef2c1f2d786fe9f1d6b8c2ff4788ed7993a97ce8` is recorded as reported, not re-computed, because that source file's exact bytes were not separately provided. The masked PNG sizes and hashes are copied from the Mac report and format-checked, but their bytes are not present here, so this cloud environment has not rehashed them. The shared k04 descriptor is identical in F and HF.

The original schema file hashes are preserved only as source byte identities; Mac did not provide those source file bytes. The package has separate local Draft 2020-12 validation schemas with their own hashes. That local v2 profile adds exactly one relation for each blue seat; it does not claim byte identity with the original Mac v1 schema files. The inference backend's support for strict schema plus `contains/minContains/maxContains` remains unverified.

Mac reports the previous Qwen service unavailable. `top_p/top_k`, backend name/version, existing model/cost and compute authorization remain unresolved. Kaggle upload, model download, and inference each have independent `not_authorized` status. The task-specific report has not authorized any of these actions; model calls, uploads, and downloads remain **0**.
