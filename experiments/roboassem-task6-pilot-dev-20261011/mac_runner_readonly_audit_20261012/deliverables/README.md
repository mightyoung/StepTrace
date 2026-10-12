# Task 6 one-shot runner patch bundle

This is an **uncommitted, unfrozen review package**. It is not a Kaggle deployment artifact and has not been run against Kaggle, llama-server, or a real model.

## Scope

- Resolve `execution-input-contract.json` and frame files from `TASK6_INPUT_ROOT` (defaulting to the private Kaggle input mount), independently of the copied runner's `__file__` directory.
- Verify pinned source hashes before bootstrap copies the prepared runner and helper into the working directory.
- Use a local `fcntl` run lock, create durable request bytes and a durable `send_intent` journal row before each sender callback, and refuse any replay once an intent journal exists.
- Keep F then HF, separate message contexts, serial execution, one try per arm, 60-second timeout, and no retry. A transport exception is recorded as `response_unknown`; this is not a server receipt or exactly-once guarantee.

## Contents

- `sources/prepared-run_pair-crashsafe.py`: prepared runner with the input mount fix and one-shot guard.
- `sources/runner_safety.py`: durable journal, lock, UUID, and no-replay logic.
- `sources/prepared-execute-pair-bootstrap-crashsafe.py`: hash-checking, durable copy bootstrap.
- `tests/test_prepared_runner_crash_safety.py`: eight synthetic-only offline tests.
- `baseline/bundle-manifest.json`: unchanged manifest for the 13 archived audit files. Those source snapshot files are not copied or edited by this patch.
- `patch-manifest.json`: hashes and provenance for this package, plus verification and test coverage.

The archive contains no private frames, model weights, credentials, or actual model responses.

## Verified baseline

The preserved audit snapshot is associated with source commit `1f9b501f6cef873d38bf5ec8b077e6aa99ea1448`. All 13 entries in the original bundle manifest were checked against their byte counts and SHA256 values before packaging; the manifest itself has SHA256 `aa15316eaaf5a9bbd760e78d448b4f561830766daafe9304c7317cdd780f8779`. The original audit status records actual F/HF requests as 0/0.

## Offline verification already run

- New crash-safety suite: 8/8 passed.
- Existing offline preflight suite: 24/24 passed.
- Python AST parse: all three new/updated source files passed.
- The integration test mocks `subprocess.Popen`, `subprocess.check_output`, and every `urllib.request.urlopen` call; all URLs are localhost fixtures. No network/model/Kaggle operation was performed.

## Test coverage

1. Working-copy path resolution uses the input mount.
2. Shared message context is rejected before a send.
3. F succeeds and HF times out; both have one durable send intent and no retry, and replay is blocked.
4. A synthetic `KeyboardInterrupt` after the durable intent and before mocked transport leaves replay blocked.
5. HTTP error response bytes and hash are retained.
6. A second local runner is blocked while the lock is held.
7. Bootstrap pins and copies the source without running it.
8. Full prepared working-copy path uses generated PNGs and synthetic contracts with only mocked endpoints, and sends mock F then mock HF.

## Uncovered crash windows and limits

- No `SIGKILL`, kernel crash, power-loss, or disk fault was injected. Tests do not establish that Kaggle's actual filesystem honors file and directory `fsync` across abrupt loss.
- No process was killed after the real server accepted or executed a request but before the client received and durably stored the response. The conservative behavior is to leave the intent and block replay; delivery and server execution remain unknown, and the pair may be incomplete.
- No test kills the process after F response persistence but before HF starts. The existing journal blocks restarting the pair; there is no resume/reconcile flow, so HF can remain unsent.
- Partial/torn journal writes and filesystem-full errors were not fault-injected. If an intent path exists after interruption, the guard blocks replay, but journal recovery and server-side receipt reconciliation are not implemented.
- The lock test is local to one host/filesystem. Cross-host or network-mounted lock semantics were not checked.
- Real model/backend binary and weight hashes, effective backend schema support, deployment notebook state, and model output quality remain outside this patch and unverified. No accuracy or research-gain claim follows from these tests.
