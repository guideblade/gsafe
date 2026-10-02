# Changelog

## Unreleased

- `gsafe unlock` no longer deletes an existing `--path-origin` that is not an unlocked remote of the same container, even with `--force`. Unlocking over a leftover remote of the same container now requires `--force`, because it may hold commits that were never locked.
- Rejects containers whose payload version is newer than this release supports instead of reading them as version 1.
- Prints a short message instead of a traceback when a prompt is interrupted with Ctrl+C or closed input.
- Removes the temporary file when writing a container fails.
- Ignores a malformed local machine ID file instead of crashing.
- Adds a test suite and a CI workflow for Linux, macOS, and Windows.

## 0.7.1 - 2026-05-03

- Fixed `gsafe init` for local-only Git LFS repositories with no configured remotes.
- Allows `gsafe lock --force` to persist intentional deleted or rewritten refs.

## 0.7.0 - 2026-05-03

- Supports creating encrypted `.gsf` containers from Git repositories.
- Supports unlocking, locking, status inspection, stale unlock recovery, and password changes.
