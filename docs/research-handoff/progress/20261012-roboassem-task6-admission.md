# RoboAssem Task6 execution admission — 2026-10-12

This is a separate Task6 execution-preparation record pinned to repository commit `030b186f252d9ac78a2ed6e2e3978c5e2c9f41b8`. It records the v5 crash-safe sender candidate, the existing Mac/Kaggle input handoff, and precise run gates. It does not authorize a Kaggle upload, build, session, download, or model call.

## Candidate and input identity

- Qwen candidate: `unsloth/Qwen3.5-9B-GGUF` revision `3885219b6810b007914f3a7950a8d1b469d598a5`; main GGUF 5,680,522,464 bytes / SHA-256 `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8`; BF16 projector 921,705,024 bytes / SHA-256 `853698ce7aa6c7ba732478bad280240969ddf7b0fcbf93900046f63903a83383`. Earlier Kaggle receipts report both rehashed after restoration. No weights are present in this cloud repo, and no re-download or current-session recheck was done.
- Backend source candidate: llama.cpp commit `8345f333951c661d166b00e6f9362e553768f292`, CUDA arch 75/Release build. The expected binary version and SHA are still null because build success is unconfirmed. The exact missing runtime artifact is `runtime-verification.json`; the latest transcribed notebook status is `Build 0`, `build_success_confirmed=false`.
- Four masked PNG identities, source frame hashes, PTS, dimensions and mask are in the [admission package](../../../experiments/roboassem-task6-pilot-dev-20261011/admission_20261012/EXECUTION_ADMISSION_20261012_zh.md) and machine-readable [manifest](../../../experiments/roboassem-task6-pilot-dev-20261011/admission_20261012/execution-admission-20261012.json). The video and images remain absent from this cloud repo. Prior Mac/Kaggle hash checks are reported, not a current mount verification.

## Request contract and limits

F is a fresh one-image context with k04 only. HF is a separate fresh context with k01–k03 as past and the same k04 as current; no history is carried between the two HTTP requests. System/question/schema identities are SHA-bound in the package. v5 candidate parameters are temperature 0, top_p 0.95, top_k 40, seed 1, max_tokens 2048, stream false, enable_thinking false, cache_prompt false. The top_p/top_k values were omitted from the earlier text contract but are present in both the uploaded sender source and v5 runner; this admission freezes the v5 values and requires runtime config readback.

Maximum two serial generation requests, F then HF, zero retries, 60-second wall-clock per request, 50 ms cancellation reserve, 2,048 output tokens per request (4,096 pair ceiling). Any F send/parse/schema failure aborts HF in v5. This differs from the earlier prospective plan's statement that schema errors do not abort HF; v5 source behavior governs. Any timeout remains delivery-unknown and must not be replayed.

The v5 archive SHA is `7bd01775520cc4b555f83b2edd1211430c51983cf1f07fe65475bab477de2f5c` (30,926 bytes). Independent review recommended approve for push after two mocked suites passed 24/24 each; it did not validate Kaggle, a real model, backend version, schema support, or real 60-second requests. The v5 prepared files are recorded as not uploaded or executed in Kaggle.

## Kaggle and shutdown status

The existing audit is a manual transcription of prior UI receipts, not a notebook export. It reports prior model/image rehashes, no F/HF requests, no raw responses, an unsuccessful/unconfirmed CUDA build, and a user-reported manual GPU stop with no independent stop verification. No current session status, GPU type, quota, or session state is available in this cloud task. The current Kaggle copies of `execution-input-contract.json` and `download-verification.json` are not present in the repo; the old transcript is the only evidence those files were checked in that earlier session.

Before any future run, root must inspect the current session/quota, read-only verify existing GGUF and PNG bytes (stop if absent or mismatched; do not run download cells), read back v5 sources by SHA, establish a successful pinned backend binary/version record, verify context and non-generating token bounds, review schema/profile behavior, and confirm each compute/inference authorization. The v5 runner's `finally` terminates/kills and reaps the local server, stops the monitor, writes `server-stopped.json`, and creates `task6-results.zip`; this does not stop the Kaggle session. Results include base64 private requests and raw responses, so keep the archive private, hash it after persistence, explicitly stop the Kaggle session in UI, and verify its stopped state separately.

Current status remains `preparation_only_blocked_for_execution`. This Task6 record does not use materials or blockers from another experiment.
