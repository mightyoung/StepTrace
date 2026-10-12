# Task 6 one-shot runner patch, version 5

This is a versioned, uncommitted review package. It is not a Kaggle deployment artifact and has not been run against Kaggle, a real llama-server, or a real model. Versions v1 through v4 remain unchanged.

## Changes in v5

1. The inline generation worker now rejects empty stdin, invalid JSON, non-object JSON, or a request without a nonempty `messages` array before calling `urlopen`. If a pending child later sees its stdin pipe close at parent shutdown, EOF cannot become an empty POST.
2. `sender_terminated=true` is now derived only from both a requested `kill()` and observed `wait()`/`poll()` reaping. An already-exited process without a kill request is reported as completed or pending, never as terminated. Each cleanup path carries both signals into the shared predicate.
3. v4 spawn/cancel synchronization and the pending-spawn behavior remain: the test uses a deterministic barrier around mocked `Popen`, records `pending_spawn` at the deadline, then verifies the late child is killed before `communicate` can write bytes. F/HF ordering, unknown delivery, and replay blocking remain in place.

The v4 independent review identified the first two issues above. v5 addresses them and is being submitted to the same reviewer; the independent v5 review is pending.

## Verification

- The current runner safety suite passed 24/24 in normal Python and 24/24 with `python -O`.
- `test_empty_or_malformed_worker_stdin_never_calls_urlopen` was added before the guard and observed RED for empty and malformed data. It now verifies five invalid input shapes exit without invoking mocked `urlopen`.
- `test_termination_confirmation_requires_kill_request_and_reap` verifies the required kill-plus-reap predicate, including both failure conditions.
- The pending-spawn test continues to verify status `pending_spawn`, `sender_terminated=false`, kill/reap after releasing the spawn barrier, no `communicate`, F-only intent, and blocked replay.
- All HTTP and subprocess operations in these tests are mocked. No model, external network, or Kaggle was used. The separate older offline preflight suite was not rerun and is not part of this claim.
- Bootstrap pins match the packaged runner and safety module. All 13 original audit file hashes match the unchanged baseline manifest.

## Limits

- The OS-level process-kill path was not run against a real child process or Kaggle. Spawn and cancel behavior is tested with mocked process objects; the worker EOF guard is executed locally with mocked `urlopen`.
- Killing the local sender does not establish whether llama-server accepted or executed a request before cancellation. Delivery can remain unknown; replay stays blocked, and there is no server receipt or idempotency mechanism.
- If kill/reap is not confirmed before the deadline, the status remains pending. A late spawned process is killed before request bytes are written, but a `Popen` call that stalls indefinitely cannot be forcibly cancelled until it returns.
- Process loss after F response persistence and before HF begins has no resume path; the attempt journal blocks replay.
- SIGKILL, machine or power loss, disk-full behavior, torn JSONL, partial request writes, fsync faults, and cross-host `flock` were not tested.
- Bootstrap readback does not prevent an external actor from changing the working copy after verification and before `runpy` opens it.
- Real backend/model artifact hashes, schema support, deployment state, and output quality remain unverified. These tests support no accuracy or research-gain claim.

## Contents

- `sources/prepared-run_pair-crashsafe.py`
- `sources/runner_safety.py`
- `sources/prepared-execute-pair-bootstrap-crashsafe.py`
- `tests/test_prepared_runner_crash_safety.py`
- `baseline/bundle-manifest.json` (unchanged manifest for the original 13 audit files)
- `patch-manifest.json` (hashes, test results, corrections, and limits)

No private frames, model weights, credentials, or real model responses are included. Original snapshot commit: `1f9b501f6cef873d38bf5ec8b077e6aa99ea1448`.
