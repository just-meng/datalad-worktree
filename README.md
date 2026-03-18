# datalad-worktree

Create nested git worktrees for [DataLad](https://www.datalad.org/) dataset hierarchies.

When working with DataLad superdatasets that contain nested subdatasets, you sometimes need to work on a feature branch across the entire hierarchy. Manually creating `git worktree` for each dataset is tedious and error-prone. This tool automates that: point it at a superdataset, give it a branch name, and it mirrors the entire nested structure under a new worktree root.

**Python 3.11+, no runtime dependencies** (DataLad optional for enhanced discovery).

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

# Verbose output
worktree -v /tmp/wt feature/x

# Force overwrite existing worktrees
worktree --force /tmp/wt feature/x

# Only checkout existing branches, don't create new ones
worktree --no-create-branch /tmp/wt release/1.0

# Specify superdataset path explicitly (instead of using cwd)
worktree -d /data/my-superdataset /tmp/wt feature/x

# Skip DataLad API, use .gitmodules parsing only
worktree --no-datalad /tmp/wt feature/x
```

### DataLad Command

If DataLad is installed, the tool registers as a DataLad extension:

```bash
datalad worktree /tmp/wt feature/new-analysis
datalad worktree -d /data/my-superdataset /tmp/wt dev/exp
```

### Python API

```python
from pathlib import Path
from datalad_worktree.core import create_nested_worktrees

result = create_nested_worktrees(
    superds_path=Path("/data/my-superdataset"),
    worktree_path=Path("/tmp/worktrees/my-feature"),
    branch="feature/new-analysis",
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

```
usage: worktree [-h] [-n] [-f] [-v] [--no-create-branch]
                [--no-datalad] [-d DATASET] [--no-color]
                worktree_path branch

positional arguments:
  worktree_path         Path for the superdataset worktree
  branch                Branch name to create/checkout in every worktree

options:
  -h, --help            Show help message and exit
  -n, --dry-run         Show what would be done without doing it
  -f, --force           Pass --force to git worktree add
  -v, --verbose         Verbose output
  --no-create-branch    Don't create new branches; only checkout existing ones
  --no-datalad          Skip DataLad API; use .gitmodules parsing only
  -d, --dataset DATASET Path to superdataset root (default: current directory)
  --no-color            Disable colored output
```

## How It Works

1. **Validate** that the current directory (or `--dataset` path) is a git repository root.
2. **Discover** all subdatasets recursively. Prefers the DataLad Python API for robustness; falls back to recursive `.gitmodules` parsing if DataLad is unavailable.
3. **Create the superdataset worktree** via `git worktree add`.
4. **For each subdataset** (sorted by path so parents come before children):
   - Clean up any gitlink file or placeholder directory left by the parent worktree at the subdataset mount point.
   - Run `git worktree add` against the subdataset's original repository.
5. **Report** results for every dataset: created, skipped (not installed), or failed.

Subdatasets that are not installed (no `.git` present) are skipped with a warning. A failed subdataset does not abort the remaining ones.

## Subdataset Discovery

The tool uses two strategies to find nested subdatasets:

| Strategy | When used | How it works |
|---|---|---|
| **DataLad API** | Default when DataLad is installed | Calls `datalad subdatasets --recursive` via Python API |
| **`.gitmodules` parsing** | Fallback, or when `--no-datalad` is passed | Recursively reads `.gitmodules` files using `configparser` |

Both strategies return subdatasets sorted by path depth, ensuring parent datasets are always processed before their children.

## Requirements

- Python >= 3.11
- git (on PATH)
- [DataLad](https://www.datalad.org/) (optional but recommended; enables robust discovery and `datalad worktree` command)

## License

MIT
