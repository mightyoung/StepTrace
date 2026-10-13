# RoboAssem Task 6 self-hosted package update — 2026-10-13

This is a code/protocol handoff, not an execution report. The new package is at [`self_hosted_execution_20261012`](../../../experiments/roboassem-task6-pilot-dev-20261011/self_hosted_execution_20261012/README_zh.md). It adds standard-library CPU input/rights preflight, pinned runtime evidence capture, a separately invoked local GPU preflight that checks `/props` and F/HF token bounds without generation then stops the server, request/response journal audit, stop-state attestation checks, output manifests/schema, and a Chinese operator guide for an operator-selected Linux CUDA server.

## Evidence and scope

- The exact input contract copy is 19,714 bytes, SHA-256 `2bb0306715bb33a40ff829c0ef2c1f2d786fe9f1d6b8c2ff4788ed7993a97ce8`.
- The package manifest records the four Mac-reported masked frame identities, model revision/hash identities, pinned llama.cpp commit, resource thresholds, F/HF ordering, and bounded no-retry request budget. Those media/model bytes are not present in this cloud workspace; the copied identities are not a new rehash.
- The official [pinned GGUF repository page](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/tree/3885219b6810b007914f3a7950a8d1b469d598a5) displays an Apache-2.0 label for the model repository. No video/PNG download URL or file-level data license was verified. Model licensing does not resolve source-media rights.
- Exact private video and PNG source paths are unknown here. Do not claim this cloud path represents files saved on the Mac. No need to unlock the Mac is assumed; the operator needs an owner-authorized source and evidence for inference/transfer rights. Missing or mismatched exact inputs block.
- Environment thresholds remain candidate-only: Linux x86_64, Python 3.12.14, CUDA 12.5.1, driver ≥555.42.06, compute capability ≥7.5, VRAM ≥16 GiB, RAM ≥32 GiB, free disk ≥20 GiB. No host/provider was selected; no GPU capacity, cost, actual backend binary or schema compatibility was measured.
- F receives only k04. HF receives k01–k03 as past and the same k04 as current, in a separate context. Past observation is not evidence of a present connection. A future result remains a visual estimate, not SOP/lock/force truth.
- Synthetic data exists only in unit fixtures and the explicitly named synthetic output sample. No real positive or negative example, human ground truth, accuracy, generality, or method-gain claim is added.

## Implementation and verification

`task6_bundle.py` never downloads or sends a generation request. The CPU commands check rights bindings, exact input/model/source hashes, attempt state, and pinned runtime/tool/hardware identity. The opt-in `gpu-preflight` starts only the pinned local server, sends only no-generation loopback endpoint requests (`/health`, `/props`, `/apply-template`, `/tokenize`), writes F/HF text-token bounds, and stops the server; it is not run in this cloud workspace. The result tools check F/HF image identity and independent contexts, attempt count/order/replay safety, raw-response byte receipts, schema validation, and operator-supplied shutdown evidence. Return summaries default to `publication_allowed=false`; private inputs, base64 requests, outputs and logs remain private.

The runtime recorder captures the pinned llama.cpp commit, `llama-server --version` and required help flags, binary size/SHA, CUDA driver/toolkit, GPU0, RAM/disk, OS release, CMake/compiler, and Python package versions without starting the server or loading model weights. Candidate runtime status is still unverified until this runs on an authorized target host.

CPU-only tests, all using synthetic fixtures and mocked local HTTP/processes: **26/26 passed** in `self_hosted_execution_20261012/tests`; existing Task6 offline tests **24/24 passed**; existing v5 crash-safety tests **24/24 passed** under normal Python and **24/24** under `python -O` (mocked sender only). The source v5 runner and earlier admission package are not modified by this increment. A CLI smoke check against missing private assets exited 2 as expected with `execution_ready=false`, `network_calls=0`, `generation_calls=0`. No real GPU build, model download, real inference call, private media read, provider session, or cost was incurred.

## Still blocked

1. Owner-confirmed source and effective rights evidence for the exact video/PNG inputs, including transfer and inference on the named provider.
2. The exact four authorized masked PNG files, video provenance file, and pinned v5 source staging; no regenerating missing PNGs.
3. Model artifact bytes in the private run root with recorded size/SHA.
4. Operator-selected host plus budget authorization, CUDA/backend build, runtime verification, `/props`, and no-generation F/HF token/schema checks.
5. Only after future authorization and successful run: private results, independent provider-stop evidence, and an owner-reviewed redacted summary.

The append-only receipt is [`roboassem-task6-self-hosted-execution-20261013.json`](../receipts/roboassem-task6-self-hosted-execution-20261013.json). Earlier admission and failed-access receipts stay unchanged.
