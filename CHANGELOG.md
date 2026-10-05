# Changelog

User-facing changes per release. Behaviour is documented in [docs/cli.md](docs/cli.md).

## 0.4.0 (2026-10-05)

### Breaking

- `remove` is renamed `delete`; `worktree` alone runs `list`.
- `add` takes `<branch> <worktree-path>`, in that order.
- `add` replaces an existing worktree by default; `-f` now only discards commits the main checkout lacks.
- `add` always starts the branch at each dataset's HEAD; `--no-create-branch` is gone.
- `delete` deletes the branch by default (`--keep-branch` opts out), discards uncommitted changes, and no longer prompts; `--delete-branch` and `-y` are gone.
- `--no-color` is gone; colour is off when output is not a terminal.
- Python API: `create_nested_worktrees(replace=)` is gone.

### Added

- `worktree fetch`: bring a worktree's results home, or new code and inputs into it.
- `add --follow-parent [<commit>]`: check subdatasets out at the commits their parent records.
- `add` configures container bind mounts (`--no-bindpaths` skips).
- `add` copies mtimes from the main checkout, Snakemake markers included (`--no-mtimes` skips).
- `delete -n`.

### Changed

- `add` and `delete` are all-or-nothing: every check runs first, and `-n` runs the same checks.
- `add` rolls back the worktrees it created if git fails partway.
- `list` groups by hierarchy and prunes worktrees deleted by other means.
- Pastel report colours, `delete` in pink.

### Fixed

- The main working tree is never deleted.
- Works with current DataLad (no deprecated `eval_results` import).

## 0.3.0 and earlier

See the git history.
