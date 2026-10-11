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
