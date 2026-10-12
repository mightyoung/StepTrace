# Task 6 one-shot runner patch, version 4

This is a versioned, uncommitted review package. It is not a Kaggle deployment artifact and has not been run against Kaggle, a real llama-server, or a real model. Versions v1, v2, and v3 remain unchanged.

## Changes in v4

1. **Spawn and cancellation are synchronized.** The sender records an explicit spawn phase before calling `Popen`. If cancellation arrives while `Popen` is pending, it waits only through the persisted cancellation reserve. If the spawn has not resolved by the deadline, the failure is recorded with `sender_termination_status="pending_spawn"` and `sender_terminated=false`.
2. **Termination evidence reflects confirmed reaping.** `sender_terminated=true` is written only after `kill()` was requested and `wait()`/`poll()` confirmed the process exited. If process termination cannot be confirmed before the deadline, the record stays pending. When a pending spawn later returns, the sender kills and reaps it before calling `communicate`, so request bytes are never handed to that child.
3. **The one-shot boundary stays fail-closed.** Every timeout persists `response_unknown`; the pending-spawn regression verifies that only F is recorded, HF is not started, and a later replay is blocked by the attempt ledger.
4. **A small part of each deadline is reserved for cancellation.** For the normal 60-second limit, cancellation starts 50 ms before the hard deadline so the runner can confirm kill/reap before time expires. The attempt record stores the reserve and cancel-start epoch.

The v3 F-schema, wall-clock deadline, exact-byte bootstrap, shared lock, and `python -O` safety checks are retained.

## Verification

- Updated synthetic runner safety suite: 22/22 passed in normal Python and 22/22 passed with `python -O`.
- `test_deadline_pending_spawn_is_marked_pending_then_killed_before_request_write` uses a deterministic barrier around mocked `Popen`. It observed RED before the fix, then verified pending status at timeout, kill/reap after the barrier opens, no `communicate` call, F-only intent, and blocked replay.
- All test HTTP and subprocess operations are mocked. No model, external network, or Kaggle was used. The existing offline preflight suite was not rerun for v4 and is not included in these results.
- Bootstrap pins match the packaged runner and safety module. The 13 original audit files match the unchanged baseline manifest.

## Limits

- The OS-level process-kill path was not run against a real child process or Kaggle. The spawn/cancel race test uses a mocked process object; the synchronization and persisted status are covered deterministically.
- Killing the local sender does not establish whether llama-server accepted or executed a request before cancellation. Delivery can remain unknown; replay stays blocked, and there is no server receipt or idempotency mechanism.
- A process stop after F response persistence and before HF begins has no resume path.
- SIGKILL, machine or power loss, disk-full behavior, torn JSONL, partial request writes, and filesystem `fsync` faults were not injected.
- `flock` was tested on one local host/filesystem, not across network mounts or hosts.
- Bootstrap readback does not prevent an external actor from changing the working copy after verification and before `runpy` opens it.
- Real backend/model artifact hashes, schema behavior, deployment state, and output quality remain unverified. These tests support no accuracy or research-gain claim.

## Contents

- `sources/prepared-run_pair-crashsafe.py`
- `sources/runner_safety.py`
- `sources/prepared-execute-pair-bootstrap-crashsafe.py`
- `tests/test_prepared_runner_crash_safety.py`
- `baseline/bundle-manifest.json` (unchanged manifest for the original 13 audit files)
- `patch-manifest.json` (hashes, review regression evidence, test results, and limits)

No private frames, model weights, credentials, or real model responses are included. Original snapshot commit: `1f9b501f6cef873d38bf5ec8b077e6aa99ea1448`.
