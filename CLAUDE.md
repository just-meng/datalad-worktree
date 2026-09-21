# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`datalad-worktree` is a Python tool and DataLad extension that manages nested git worktrees for DataLad dataset hierarchies. It provides four subcommands: `add` (create), `list`, `delete`, and `sync-mtimes` for worktrees across a superdataset and all its subdatasets. Running `worktree` with no subcommand defaults to `list`.

## Repository Structure

```
datalad-worktree/
├── pyproject.toml         # Build config, dependencies, entry points
├── README.md              # User-facing documentation
├── CLAUDE.md              # This file
└── src/
    └── datalad_worktree/
        ├── __init__.py    # Package init + DataLad extension registration (command_suite)
        ├── __main__.py    # Entry point for `python -m datalad_worktree`
        ├── cli.py         # Standalone CLI (argparse subcommands, colored output)
        ├── core.py        # Shared types (WorktreeResult, WorktreeReport), validation, git helpers
        ├── add.py         # Add command: create nested worktrees with pre-flight check
        ├── list_cmd.py    # List command: show worktrees across hierarchy
        ├── delete.py      # Delete command: delete worktrees by path or branch
        ├── container.py   # Container bind-mount config for created worktrees
        ├── mtimes.py      # mtime preservation: add's final step + sync-mtimes
        ├── discovery.py   # Subdataset discovery via recursive .gitmodules parsing
        └── dl_command.py  # DataLad Interface classes: WorktreeAdd, WorktreeList,
                           #   WorktreeDelete, WorktreeSyncMtimes
```

## Build and Run

```bash
# Install as a system tool (editable, isolated environment)
uv tool install -e .

# Run (three equivalent entry points)
worktree add branch /tmp/wt
datalad worktree-add branch /tmp/wt   # requires datalad
python -m datalad_worktree add branch /tmp/wt

# Run tests
uv run --dev pytest
```

## Architecture

### Subcommands

- **`add`**: Creates worktrees for superdataset + all installed subdatasets. Runs pre-flight check first — if any would fail, none are created.
- **`list`**: Shows all worktrees across the hierarchy (only datasets with extra worktrees beyond main). Also the default when no subcommand is given.
- **`delete`**: Deletes worktrees by path or branch name. Processes deepest-first. Optional `--delete-branch`.
- **`sync-mtimes`**: Re-copies file mtimes into an existing worktree hierarchy from the working trees it was created from. Same machinery `add` runs at creation; exists because `datalad get`, a merge, or a `git checkout` all restore files with fresh mtimes. Takes a worktree path **or** a branch name (`resolve_worktree_target()`), the same target shape `delete` accepts.

### Call Flow (add)

1. **CLI entry** (`cli.py:_cmd_add`) parses args, iterates `add.py:create_nested_worktrees()` generator, renders each report
2. **DataLad entry** (`dl_command.py:WorktreeAdd.__call__`) uses `require_dataset()`, iterates the same generator, yields DataLad result dicts
3. **`create_nested_worktrees()`** runs `_preflight_check()`, then creates worktrees and yields `WorktreeReport` for each
4. **Discovery** (`discovery.py`) recursively parses `.gitmodules` files with `configparser`

### Key Design Decisions

- **Discovery via .gitmodules**: Recursively parses `.gitmodules` with `configparser`. Fast, no dependencies.
- **Generator-based**: `create_nested_worktrees()` and `delete_nested_worktrees()` yield `WorktreeReport` objects one at a time, enabling real-time progress display.
- **Pre-flight validation (add)**: Before creating anything, checks for branch conflicts (`git_branch_checked_out_at`) and existing paths. All-or-nothing: if any non-skipped dataset would fail, no worktrees are created.
- **Deepest-first deletion**: `delete` processes subdatasets in reverse order so children are deleted before parents.
- **Prune before listing**: `list_nested_worktrees()` runs `git_worktree_prune()` on every dataset before reading its worktrees, so a directory removed outside the tool (`rm -rf`) doesn't linger as a stale entry.
- **Sorted by path (add)**: Subdatasets are sorted so parents are processed before children.
- **Gitlink cleanup**: When the superds worktree is created, git places gitlink files at submodule mount points. `_prepare_destination()` in `add.py` removes these before creating each subdataset worktree.
- **Container bind mounts (add)**: After the worktrees exist, `container.py` configures any dataset registering a container with a `cmdexec`, so `datalad containers-run` can reach the annex objects that live in the main repo outside the worktree (datalad-container#288). Two `git config --worktree` writes (the `bindpaths` substitution and a `cmdexec` carrying `{{bindpaths}}`) keep machine-specific values out of the main checkout and out of git history; one committed empty `bindpaths` in `.datalad/config` keeps run records rerunnable, since `run` records substitutions unexpanded. Skipped via `--no-bindpaths`.
- **mtime preservation (add)**: Runs last, after every worktree exists, because the ordering it repairs is the *cross-dataset* one — checking the superds out before its subdatasets leaves every `code/` file newer than every output derived from it, which reads to Snakemake/Make as "all your code changed". `mtimes.py` copies each path's mtime from the working tree the worktree came from (`WorktreeReport.source`), matching on **blob OID** so divergent content is never stamped fresh, skipping paths dirty in the reference, and using `follow_symlinks=False` because the annex object store is a shared inode with the main repo. Skipped via `--no-mtimes`; re-runnable via `worktree sync-mtimes`.
- **Repo root vs. inside a repo**: `is_git_repo()` answers "can git find a repository from here", and git walks *up* — so it returns True for an empty submodule mount point, reporting the enclosing superds. Use `is_git_repo_root()` (`discovery.py`) anywhere one dataset is resolved against another.
- **Failure isolation**: A failed subdataset does not abort remaining ones. Only a superds failure is fatal.
- **Branch logic is per-dataset**: If branch exists, checkout. If not, create with `-b`. Evaluated independently.
- **All git interactions** go through `subprocess.run()` with `capture_output=True, text=True`. No gitpython dependency.
- **Delete fallback**: `git worktree remove` may fail on DataLad repos where `.git` is a directory instead of a gitlink file. Falls back to `shutil.rmtree` + `git worktree prune`.

### Result Types

- `WorktreeResult` (enum): `CREATED`, `CREATED_NEW_BRANCH`, `SKIPPED_NOT_INSTALLED`, `SKIPPED_NOT_GIT_REPO`, `SKIPPED_DRY_RUN`, `SKIPPED_NO_WORKTREE`, `SKIPPED_CONTAINER`, `CONFIGURED`, `MTIMES_SYNCED`, `DELETED`, `DELETED_BRANCH`, `FAILED`
- `WorktreeReport` (dataclass): one per dataset, holds source, destination, result, branch, message
- `SubDataset` (dataclass in `discovery.py`): holds `rel_path`, `abs_path`, `installed`, `depth`
- `GitWorktreeEntry` (dataclass in `core.py`): parsed from `git worktree list --porcelain`
- `DatasetWorktrees` (dataclass in `list_cmd.py`): groups worktree entries by dataset

### DataLad Extension Registration

- `__init__.py` exports `command_suite` tuple with four commands
- `dl_command.py` defines `WorktreeAdd`, `WorktreeList`, `WorktreeDelete`, `WorktreeSyncMtimes` (all `Interface` subclasses)
- DataLad command names: `worktree-add`, `worktree-list`, `worktree-delete`, `worktree-sync-mtimes`
- `pyproject.toml` registers under `[project.entry-points."datalad.extensions"]`
- `dl_command.py` gracefully degrades to stub classes when DataLad is not installed

## Code Conventions

- Type hints throughout, `from __future__ import annotations` for forward refs
- Logging via `logging.getLogger(__name__)` in every module
- ANSI colors in CLI only; disabled when stdout is not a TTY or `--no-color` is passed
- Enums for result states, dataclasses for structured data

## Common Modifications

**Add a CLI flag**: add to `cli.py:build_parser()` under the relevant subcommand, pass through in `_cmd_*()`, add corresponding `Parameter` in `dl_command.py`

**Change worktree creation behavior**: modify `_git_worktree_add()` and/or `_prepare_destination()` in `add.py`

**Change result reporting**: modify `WorktreeResult` enum and `WorktreeReport` dataclass in `core.py`, update CLI rendering in `cli.py:_render_report()`, update DataLad result mapping in `dl_command.py`

**Add a new subcommand**: add parser in `cli.py:build_parser()`, add `_cmd_*()` handler, add dispatch in `main()`, create module for logic, add DataLad Interface in `dl_command.py`, register in `__init__.py:command_suite`

## Testing Considerations

- Tests need git repos with nested submodules to simulate DataLad dataset hierarchies
- The tool shells out to git via `subprocess.run()` — use real temp repos with `tmp_path` fixture
- `_prepare_destination()` handles three filesystem states (gitlink file, empty dir, dir with only `.git`) — each needs a test case
- `create_nested_worktrees()` is a generator — wrap in `list()` when testing with `pytest.raises`
- Pre-flight check tests: verify that nothing is created when a branch conflict exists
- Delete tests: verify deepest-first ordering, path vs branch resolution, `--delete-branch` behavior
- Mtime tests: `tests/test_mtimes.py` uses a `pipeline_ds` fixture with fixed timestamps where the superdataset's "output" is deliberately newer than the `code/` subdataset's "script". Assert *invariants* (ordering, equality, untouched-ness), not pinned times. Each safety property has a test that fails when the property is removed — verified by mutation: `follow_symlinks=True` breaks 5 tests including the annex-objects guard, path-only matching breaks `test_differing_blob_keeps_its_checkout_mtime`, dropping the dirty filter breaks `test_dirty_reference_file_is_skipped`. Keep it that way.
- Tests that consume `create_nested_worktrees()` must not assert on the *total* report count or on "every report is a CREATED" — the generator also yields container and mtime reports. Filter to the result type under test, or assert no `FAILED`.
- Container tests: `tests/test_containers_run.py` runs `datalad containers-run` for real against `tests/fake_container_runtime.py`, a stand-in for `singularity exec` that parses `-B` and executes under `unshare -Urm`, masking paths with tmpfs. Needs `datalad-container` (dev dependency) and unprivileged user/mount namespaces; both are skip-guarded. `test_fails_without_bindpaths` must keep failing-without-the-fix, otherwise the positive test proves nothing.

## Dependencies

- **Required**: Python >= 3.11, git on PATH
- **Optional**: DataLad (enables `datalad worktree-*` commands)
- **No other Python runtime dependencies** beyond the standard library when running without DataLad
