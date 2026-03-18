# datalad-worktree

Create nested git worktrees for [DataLad](https://www.datalad.org/) dataset hierarchies.

When working with DataLad superdatasets that contain nested subdatasets, you sometimes need to work on a feature branch across the entire hierarchy. Manually creating `git worktree` for each dataset is tedious and error-prone. This tool automates that: point it at a superdataset, give it a branch name, and it mirrors the entire nested structure under a new worktree root.

**Python 3.11+, no runtime dependencies** (DataLad optional for `datalad worktree` command).

## Installation

As a DataLad extension (recommended), alongside other extensions:

```bash
uv tool install datalad \
  --with datalad-next \
  --with datalad-container \
  --with datalad-worktree@git+https://github.com/just-meng/datalad-worktree.git \
  --force
```

For development:

```bash
cd datalad-worktree
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Quick Start

```bash
cd /data/my-superdataset
worktree /tmp/worktrees/my-feature feature/new-analysis
```

This discovers all subdatasets, then creates:

```
/tmp/worktrees/my-feature/              <- superdataset worktree (branch: feature/new-analysis)
├── sub-01/                             <- subdataset worktree
│   └── derivatives/                    <- nested subdataset worktree
├── sub-02/
│   └── derivatives/
└── code/shared-library/                <- subdataset worktree at any depth
```

Each directory is a proper git worktree checked out on `feature/new-analysis`. If the branch doesn't exist in a given dataset, it is created automatically.

## Usage

### Standalone CLI

```bash
# Basic usage (run from superdataset root)
worktree <worktree-path> <branch>

# Dry run -- see what would happen without changing anything
worktree --dry-run /tmp/wt dev/experiment

# Force overwrite existing worktrees
worktree --force /tmp/wt feature/x

# Only checkout existing branches, don't create new ones
worktree --no-create-branch /tmp/wt release/1.0

# Specify superdataset path explicitly (instead of using cwd)
worktree -d /data/my-superdataset /tmp/wt feature/x
```

### DataLad Command

If DataLad is installed, the tool registers as a DataLad extension:

```bash
datalad worktree /tmp/wt feature/new-analysis
datalad worktree --dry-run /tmp/wt dev/experiment
datalad worktree -d /data/my-superdataset /tmp/wt dev/exp
```

### Python API

```python
from pathlib import Path
from datalad_worktree.core import create_nested_worktrees, collect_worktree_reports

# create_nested_worktrees yields reports as each worktree is processed
worktree_path = Path("/tmp/worktrees/my-feature")
branch = "feature/new-analysis"

result = collect_worktree_reports(
    create_nested_worktrees(
        superds_path=Path("/data/my-superdataset"),
        worktree_path=worktree_path,
        branch=branch,
    ),
    worktree_root=worktree_path.resolve(),
    branch=branch,
)

print(result.summary())

if result.all_ok:
    print(f"Ready at: {result.worktree_root}")
else:
    for report in result.failed:
        print(f"FAILED: {report.dataset_path}: {report.message}")
```

### As a Python Module

```bash
python -m datalad_worktree /tmp/wt feature/x
```

## CLI Reference

### Standalone CLI (`worktree`)

```
worktree [-h] [-n] [-f] [--no-create-branch] [-d DATASET] [--no-color]
         worktree_path branch

  -n, --dry-run         Show what would be done without doing it
  -f, --force           Pass --force to git worktree add
  --no-create-branch    Don't create new branches; only checkout existing ones
  -d, --dataset DATASET Path to superdataset root (default: current directory)
  --no-color            Disable colored output
```

### DataLad command (`datalad worktree`)

```
datalad worktree [-h] [-d DATASET] [--no-create-branch] [-f] [-n]
                 worktree_path branch

  -n, --dry-run         Show what would be done without doing it
  -f, --force           Pass --force to git worktree add
  --no-create-branch    Fail if the branch doesn't exist
  -d, --dataset DATASET Path to superdataset (default: current directory)
```

## How It Works

1. **Validate** that the current directory (or `--dataset` path) is a git repository root.
2. **Discover** all subdatasets by recursively parsing `.gitmodules` files.
3. **Create the superdataset worktree** via `git worktree add`.
4. **For each subdataset** (sorted by path so parents come before children):
   - Clean up any gitlink file or placeholder directory left by the parent worktree at the subdataset mount point.
   - Run `git worktree add` against the subdataset's original repository.
5. **Report** results as each worktree is created, with a summary at the end.

Subdatasets that are not installed (no `.git` present) are skipped. A failed subdataset does not abort the remaining ones.

## Requirements

- Python >= 3.11
- git (on PATH)
- [DataLad](https://www.datalad.org/) (optional; enables `datalad worktree` command)

## License

MIT
