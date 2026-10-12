# Task 6 one-shot runner patch, version 3

This is a versioned, uncommitted review package. It is not a Kaggle deployment artifact and has not been run against Kaggle, a real llama-server, or a real model. The v1 and v2 ZIPs remain unchanged.

## Changes in v3

1. **Invalid F output stops the pair.** Full offline schema and seat checks run after F; any violation is persisted as `processing_failed` and aborts before HF.
2. **A 60-second per-request wall deadline cancels the local sender.** The deadline and scope are recorded with the durable send intent. Production sends through a child process. On timeout the runner kills and reaps that process, records `response_unknown` with `delivery_unknown=true`, and never retries or starts the next arm. The local llama-server may already have accepted the request, so this does not establish whether the server executed it.
3. **Bootstrap copies the exact bytes it verified.** Each source is read once, that byte buffer is hashed and written, and the destination is read back and checked against the pin.
4. **Bootstrap and runner share one run lock.** `main()` acquires the results lock before copying either source and holds it through runner startup. The runner consumes that same guard, so there is no copy-to-run lock gap. A standalone `prepare_runner()` acquires the lock for its verification/copy/readback operation only. Runner result files, including `execution-config.json`, are written only after the run guard is held; a lock loser leaves prior result evidence unchanged.
5. **Safety checks survive `python -O`.** Runtime commit, input bytes and hashes, dimensions, frame order, request separation, backend context, prompt bound, finish reason, token bound, and truncation checks use explicit errors rather than `assert`.

## Verification

- Synthetic runner safety suite: 21/21 passed in normal Python and 21/21 passed with `python -O`.
- Existing offline preflight suite: 24/24 passed.
- The timeout integration test injects a hung mocked sender process and verifies the runner calls kill, waits for termination, persists unknown delivery, and does not start HF. All subprocess and HTTP operations in the suite are mocked; no external request or model inference was made.
- The eight review regressions for sender cancellation, lock handoff and evidence preservation, and optimized-mode safety checks were observed failing before their fixes and passing afterward.
- Bootstrap pins match the packaged runner and safety module. The 13 original audit files match the unchanged baseline manifest.

## Limits

- No real Kaggle session, llama-server, model, external network request, or inference was run. The OS-level child process kill behavior is exercised through a mocked process object.
- Killing the local sender does not establish whether llama-server accepted or executed the request before the cutoff. Delivery is unknown, replay stays blocked, and no server receipt or idempotency mechanism exists.
- A process stop after F is persisted and before HF begins has no resume path; the existing intent journal blocks replay.
- SIGKILL, machine or power loss, disk-full behavior, torn JSONL, partial request writes, and filesystem `fsync` faults were not injected.
- `flock` was tested on one local host/filesystem, not across network mounts or hosts.
- Bootstrap readback does not prevent an external actor from changing the working copy after verification and before `runpy` opens it; there is no immutable mount or file-descriptor execution path.
- Real backend/model artifact hashes, backend schema behavior, deployment state, and output quality remain unverified. These tests support no accuracy or research-gain claim.

## Contents

- `sources/prepared-run_pair-crashsafe.py`
- `sources/runner_safety.py`
- `sources/prepared-execute-pair-bootstrap-crashsafe.py`
- `tests/test_prepared_runner_crash_safety.py`
- `baseline/bundle-manifest.json` (unchanged manifest for the original 13 audit files)
- `patch-manifest.json` (hashes, regression evidence, test results, and limits)

No private frames, model weights, credentials, or real model responses are included. Original snapshot commit: `1f9b501f6cef873d38bf5ec8b077e6aa99ea1448`.
