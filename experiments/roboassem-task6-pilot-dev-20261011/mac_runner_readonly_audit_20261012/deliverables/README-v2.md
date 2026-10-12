# Task 6 one-shot runner patch, version 2

This is a versioned, uncommitted review package. It is not a Kaggle deployment artifact and has not been run against Kaggle, a real llama-server, or a real model. The earlier v1 ZIP remains unchanged.

## Changes

1. **Invalid F output stops the pair.** The response parser now raises on any offline schema/seat validation errors. `execute_pair` records `processing_failed` in `attempt-events.jsonl` and `failures.jsonl`; the runner does not send HF or publish the invalid record as a normal result.
2. **Per-request wall-clock deadline.** The deadline is 60 seconds per request, measured from the persisted `started_at` timestamp in that request's durable send-intent record. The `send_deadline_epoch`, timeout mode/scope, and duration are persisted. A daemon worker bounds how long the pair controller waits even if the sender callback ignores its socket timeout. When the deadline expires, the run records `response_unknown`, `deadline_exceeded=true`, and `delivery_unknown=true`, then stops without retrying or starting the next arm. This is a per-request limit, not a 60-second limit for the full F+HF pair.
3. **Bootstrap copies verified bytes.** It hashes each source byte buffer once, writes that exact buffer atomically/durably, then reads the destination back and checks its pinned SHA256 before returning the runner path.

A timed-out Python sender thread cannot be forcibly cancelled safely. The worker may remain blocked until the underlying transport/socket returns or the process exits. The runner records the outcome as unknown, never retries it, and never starts HF after F's deadline; this does not provide exactly-once server execution or receipt confirmation.

## Package contents

- `sources/prepared-run_pair-crashsafe.py`
- `sources/runner_safety.py`
- `sources/prepared-execute-pair-bootstrap-crashsafe.py`
- `tests/test_prepared_runner_crash_safety.py` (12 synthetic-only tests)
- `baseline/bundle-manifest.json` (unchanged manifest for the original 13 audit files)
- `patch-manifest.json` (hashes, provenance, test results, and limitations)

No private frames, model weights, credentials, or real model responses are included. The original 13 audit files were reverified against their byte counts and SHA256 values; none were edited. Original snapshot commit: `1f9b501f6cef873d38bf5ec8b077e6aa99ea1448`.

## Verification

- Version 2 safety suite: 12/12 passed. This includes the original eight tests and four new regressions.
- Existing offline preflight suite: 24/24 passed.
- Bootstrap source pins match the packaged runner and helper hashes.
- The virtual 61-second sender test advances a fake monotonic clock; it does not wait in real time.
- The full working-copy integration test generates synthetic PNGs and mocks subprocess and localhost HTTP calls. No external network, Kaggle, or model was used.
- RED evidence: the original v1 helper failed the 61-second virtual deadline test; the pre-fix runner failed the invalid-F integration negative; the pre-fix bootstrap failed both source-mutation and destination-readback negatives. Each new failure was observed before its corresponding implementation change.

## Crash windows and limitations still open

- No SIGKILL, kernel crash, power-loss, disk-full, or actual Kaggle filesystem fsync fault was injected.
- If the server accepts/executes a request but the client times out before saving the response, delivery remains unknown. There is no server-side receipt reconciliation or idempotency key; replay remains blocked.
- A timed-out daemon sender is not cancelled by Python. It can remain active until the transport returns or the process exits; no subsequent arm is started.
- Process loss after F response persistence but before HF starts leaves HF unsent; there is no resume path because any existing send-intent journal blocks the whole pair.
- Torn JSONL writes, partial request-file writes, filesystem-full recovery, and the crash boundary between request artifact persistence and send-intent persistence were not fault-injected.
- `flock` was tested on one local host/filesystem only, not across network mounts or hosts.
- Bootstrap readback closes the reviewed source-to-copy gap, but post-readback mutation of the working copy before `runpy` is not defended by a separate immutable mount or file-descriptor execution path.
- Real model/backend executable and weight hashes, effective backend schema support, deployment notebook state, and model output quality remain unverified. No accuracy or research-gain claim follows from these tests.
