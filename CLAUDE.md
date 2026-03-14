# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`datalad-worktree` is a Python tool and DataLad extension that creates nested git worktrees for DataLad dataset hierarchies. Given a superdataset with nested subdatasets, it mirrors the entire structure under a new worktree root, with each dataset (super and sub) getting its own git worktree on a specified branch.

## Repository Structure

```
datalad-worktree/
├── pyproject.toml         # Build config, dependencies, entry points
├── README.md              # User-facing documentation
├── CLAUDE.md              # This file
└── datalad_worktree/
    ├── __init__.py        # Package init + DataLad extension registration (command_suite)
    ├── __main__.py        # Entry point for `python -m datalad_worktree`
    ├── cli.py             # Standalone CLI (argparse, colored output)
    ├── core.py            # Core logic: validation, worktree creation, result types
    ├── discovery.py       # Subdataset discovery (DataLad API + .gitmodules fallback)
    └── dl_command.py      # DataLad Interface subclass for `datalad worktree-create`
```

## Build and Run

```bash
# Install as a system tool (editable, isolated environment)
uv tool install -e .

# Run (three equivalent entry points)
datalad-worktree /tmp/wt name branch
datalad worktree-create /tmp/wt name branch   # requires datalad
python -m datalad_worktree /tmp/wt name branch

# Run tests
uv run --extra dev pytest
```

## Architecture

### Call Flow

1. **CLI entry** (`cli.py:main`) parses args, calls `core.py:create_nested_worktrees()`
2. **DataLad entry** (`dl_command.py:WorktreeCreate.__call__`) uses `require_dataset()`, calls the same `create_nested_worktrees()`, yields DataLad result dicts
3. **`create_nested_worktrees()`** calls `validate_superds()`, then `discover_subdatasets()`, then iterates over all datasets calling `_prepare_destination()` + `_git_worktree_add()` for each
4. **Discovery** (`discovery.py`) tries `discover_via_datalad()`, catches ImportError/exceptions, falls back to `discover_via_gitmodules()`

### Key Design Decisions

- **Two discovery backends**: `discover_via_datalad()` uses the DataLad Python API. `discover_via_gitmodules()` recursively parses `.gitmodules` with `configparser`. Auto-selects; override with `--no-datalad`.
- **Sorted by path**: Subdatasets are always sorted so parents are processed before children.
- **Gitlink cleanup**: When the superds worktree is created, git places gitlink files at submodule mount points. `_prepare_destination()` in `core.py` detects and removes these before creating each subdataset worktree. It handles three cases: gitlink file, empty directory, directory containing only `.git`.
- **Failure isolation**: A failed subdataset does not abort remaining ones. Only a superds failure is fatal. All results are collected into `WorktreeCreateResult`.
- **Branch logic is per-dataset**: If branch exists, checkout. If not, create with `-b`. Evaluated independently so some datasets can already have the branch.
- **All git interactions** go through `subprocess.run()` with `capture_output=True, text=True`. No gitpython dependency.

### Result Types

- `WorktreeResult` (enum): `CREATED`, `CREATED_NEW_BRANCH`, `SKIPPED_NOT_INSTALLED`, `SKIPPED_NOT_GIT_REPO`, `SKIPPED_DRY_RUN`, `FAILED`
- `WorktreeReport` (dataclass): one per dataset, holds source, destination, result, message
- `WorktreeCreateResult` (dataclass): aggregates all reports, provides `.succeeded`, `.skipped`, `.failed`, `.all_ok`, `.summary()`
- `SubDataset` (dataclass in `discovery.py`): holds `rel_path`, `abs_path`, `installed`, `depth`

### DataLad Extension Registration

- `__init__.py` exports `command_suite` tuple
- `dl_command.py` defines `WorktreeCreate(Interface)` with `_params_` dict and `@build_doc`
- `pyproject.toml` registers under `[project.entry-points."datalad.extensions"]`
- `dl_command.py` gracefully degrades to a stub class when DataLad is not installed

## Code Conventions

- Type hints throughout, `from __future__ import annotations` for forward refs
- Logging via `logging.getLogger(__name__)` in every module
- ANSI colors in CLI only; disabled when stdout is not a TTY or `--no-color` is passed
- Enums for result states, dataclasses for structured data

## Common Modifications

**Add a CLI flag**: add to `cli.py:build_parser()`, pass through to `create_nested_worktrees()` in `core.py`, add corresponding `Parameter` in `dl_command.py:WorktreeCreate._params_`

**Add a discovery backend**: add function in `discovery.py`, integrate into `discover_subdatasets()` selection logic

**Change worktree creation behavior**: modify `_git_worktree_add()` and/or `_prepare_destination()` in `core.py`

**Change result reporting**: modify `WorktreeResult` enum and `WorktreeReport` dataclass in `core.py`, update CLI rendering in `cli.py:main()`, update DataLad result mapping in `dl_command.py`

## Testing Considerations

- Tests need git repos with nested submodules to simulate DataLad dataset hierarchies
- The tool shells out to git via `subprocess.run()` — use real temp repos with `tmp_path` fixture
- Discovery has two code paths that should both be tested; force `.gitmodules` path with `prefer_datalad=False`
- `_prepare_destination()` handles three filesystem states (gitlink file, empty dir, dir with only `.git`) — each needs a test case
- `_branch_exists()` and `_git_worktree_add()` can be tested against real temp repos
- Dry run mode should produce only `SKIPPED_DRY_RUN` results and create no files

## Dependencies

- **Required**: Python >= 3.8, git on PATH
- **Optional**: DataLad (enables API discovery and `datalad worktree-create` command)
- **No other Python runtime dependencies** beyond the standard library when running without DataLad
