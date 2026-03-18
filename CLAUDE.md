# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`datalad-worktree` is a Python tool and DataLad extension that manages nested git worktrees for DataLad dataset hierarchies. It provides three subcommands: `add` (create), `list`, and `remove` for worktrees across a superdataset and all its subdatasets.

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
        ├── remove.py      # Remove command: remove worktrees by path or branch
        ├── discovery.py   # Subdataset discovery via recursive .gitmodules parsing
        └── dl_command.py  # DataLad Interface classes: WorktreeAdd, WorktreeList, WorktreeRemove
```

## Build and Run

```bash
# Install as a system tool (editable, isolated environment)
uv tool install -e .

# Run (three equivalent entry points)
worktree add /tmp/wt branch
datalad worktree-add /tmp/wt branch   # requires datalad
python -m datalad_worktree add /tmp/wt branch

# Run tests
uv run --extra dev pytest
```

## Architecture

### Subcommands

- **`add`**: Creates worktrees for superdataset + all installed subdatasets. Runs pre-flight check first — if any would fail, none are created.
- **`list`**: Shows all worktrees across the hierarchy (only datasets with extra worktrees beyond main).
- **`remove`**: Removes worktrees by path or branch name. Processes deepest-first. Optional `--delete-branch`.

### Call Flow (add)

1. **CLI entry** (`cli.py:_cmd_add`) parses args, iterates `add.py:create_nested_worktrees()` generator, renders each report
2. **DataLad entry** (`dl_command.py:WorktreeAdd.__call__`) uses `require_dataset()`, iterates the same generator, yields DataLad result dicts
3. **`create_nested_worktrees()`** runs `_preflight_check()`, then creates worktrees and yields `WorktreeReport` for each
4. **Discovery** (`discovery.py`) recursively parses `.gitmodules` files with `configparser`

### Key Design Decisions

- **Discovery via .gitmodules**: Recursively parses `.gitmodules` with `configparser`. Fast, no dependencies.
- **Generator-based**: `create_nested_worktrees()` and `remove_nested_worktrees()` yield `WorktreeReport` objects one at a time, enabling real-time progress display.
- **Pre-flight validation (add)**: Before creating anything, checks for branch conflicts (`git_branch_checked_out_at`) and existing paths. All-or-nothing: if any non-skipped dataset would fail, no worktrees are created.
- **Deepest-first removal**: `remove` processes subdatasets in reverse order so children are removed before parents.
- **Sorted by path (add)**: Subdatasets are sorted so parents are processed before children.
- **Gitlink cleanup**: When the superds worktree is created, git places gitlink files at submodule mount points. `_prepare_destination()` in `add.py` removes these before creating each subdataset worktree.
- **Failure isolation**: A failed subdataset does not abort remaining ones. Only a superds failure is fatal.
- **Branch logic is per-dataset**: If branch exists, checkout. If not, create with `-b`. Evaluated independently.
- **All git interactions** go through `subprocess.run()` with `capture_output=True, text=True`. No gitpython dependency.
- **Remove fallback**: `git worktree remove` may fail on DataLad repos where `.git` is a directory instead of a gitlink file. Falls back to `shutil.rmtree` + `git worktree prune`.

### Result Types

- `WorktreeResult` (enum): `CREATED`, `CREATED_NEW_BRANCH`, `SKIPPED_NOT_INSTALLED`, `SKIPPED_NOT_GIT_REPO`, `SKIPPED_DRY_RUN`, `SKIPPED_NO_WORKTREE`, `REMOVED`, `REMOVED_BRANCH`, `FAILED`
- `WorktreeReport` (dataclass): one per dataset, holds source, destination, result, branch, message
- `WorktreeCreateResult` (dataclass): aggregates reports, provides `.succeeded`, `.skipped`, `.failed`, `.all_ok`, `.summary()`
- `SubDataset` (dataclass in `discovery.py`): holds `rel_path`, `abs_path`, `installed`, `depth`
- `GitWorktreeEntry` (dataclass in `core.py`): parsed from `git worktree list --porcelain`
- `DatasetWorktrees` (dataclass in `list_cmd.py`): groups worktree entries by dataset

### DataLad Extension Registration

- `__init__.py` exports `command_suite` tuple with three commands
- `dl_command.py` defines `WorktreeAdd`, `WorktreeList`, `WorktreeRemove` (all `Interface` subclasses)
- DataLad command names: `worktree-add`, `worktree-list`, `worktree-remove`
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
- Remove tests: verify deepest-first ordering, path vs branch resolution, `--delete-branch` behavior

## Dependencies

- **Required**: Python >= 3.11, git on PATH
- **Optional**: DataLad (enables `datalad worktree-*` commands)
- **No other Python runtime dependencies** beyond the standard library when running without DataLad
