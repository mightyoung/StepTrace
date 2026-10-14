# Versioned Task 6 runner sources

These copies retain the prepared F/HF request flow from the read-only Mac audit snapshot and add a fail-closed one-shot artifact guard to the runner, bootstrap, and runner safety module. The original snapshot under `mac_runner_readonly_audit_20261012/sources/` remains unchanged.

The guard allows an empty `results/` root and staged `results/setup-evidence/`. It rejects other existing root entries and known run artifacts placed inside setup evidence, including empty, malformed, or partial journals and orphan responses/errors. File sizes and SHA-256 values are pinned in the adjacent `experiment-manifest.json`; the bootstrap also pins the runner and safety module.

The package tests exercise only synthetic files and lock acquire/release. These sources have not been used for a real model or GPU run.
