# datalad-worktree

Create nested git worktrees for [DataLad](https://www.datalad.org/) dataset hierarchies.

When working with DataLad superdatasets that contain nested subdatasets, you sometimes need to work on a feature branch across the entire hierarchy. Manually creating `git worktree` for each dataset is tedious and error-prone. This tool automates that: point it at a superdataset, give it a branch name, and it mirrors the entire nested structure under a new worktree root.

**Python 3.11+, no runtime dependencies** (DataLad optional for `datalad worktree-*` commands).

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

# Create nested worktrees
worktree add /tmp/worktrees/my-feature feature/new-analysis

# List all worktrees across the hierarchy
worktree list

# Remove worktrees by branch name
worktree remove feature/new-analysis
```

Creating worktrees discovers all subdatasets and produces:

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
# Create worktrees (run from superdataset root)
worktree add <worktree-path> <branch>

# Dry run -- see what would happen without changing anything
worktree add --dry-run /tmp/wt dev/experiment

# Force overwrite existing worktrees
worktree add --force /tmp/wt feature/x

# Only checkout existing branches, don't create new ones
worktree add --no-create-branch /tmp/wt release/1.0

# Specify superdataset path explicitly (instead of using cwd)
worktree add -d /data/my-superdataset /tmp/wt feature/x

# List all worktrees across the hierarchy
worktree list
worktree list -d /data/my-superdataset

# Remove worktrees by branch name
worktree remove feature/x

# Remove worktrees by path
worktree remove /tmp/wt

# Remove worktrees and delete the branch
worktree remove --delete-branch feature/x

# Force removal (uncommitted changes + force-delete branch)
worktree remove --force --delete-branch feature/x
```

### DataLad Commands

If DataLad is installed, the tool registers as a DataLad extension with three commands:

```bash
# Create worktrees
datalad worktree-add /tmp/wt feature/new-analysis
datalad worktree-add --dry-run /tmp/wt dev/experiment

# List worktrees
datalad worktree-list
datalad worktree-list -d /data/my-superdataset

# Remove worktrees
datalad worktree-remove feature/x
datalad worktree-remove --delete-branch feature/x
```

### Python API

```python
from pathlib import Path
from datalad_worktree.add import create_nested_worktrees
from datalad_worktree.core import collect_worktree_reports

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
```

### As a Python Module

```bash
python -m datalad_worktree add /tmp/wt feature/x
```

## CLI Reference

### `worktree add`

```
worktree add [-h] [-n] [-f] [--no-create-branch] [-d DATASET]
             worktree_path branch

  worktree_path             Path for the superdataset worktree
  branch                    Branch name to create/checkout in every worktree
  -n, --dry-run             Show what would be done without doing it
  -f, --force               Pass --force to git worktree add
  --no-create-branch        Don't create new branches; only checkout existing ones
  -d, --dataset DATASET     Path to superdataset root (default: current directory)
```

### `worktree list`

```
worktree list [-h] [-d DATASET]

  -d, --dataset DATASET     Path to superdataset root (default: current directory)
```

### `worktree remove`

```
worktree remove [-h] [--delete-branch] [-f] [-d DATASET] target

  target                    Worktree path or branch name to remove
  --delete-branch           Also delete the branch (safe delete; refuses if unmerged)
  -f, --force               Force removal even with uncommitted changes; force-delete branch
  -d, --dataset DATASET     Path to superdataset root (default: current directory)
```

## How It Works

### Add

1. **Validate** that the current directory (or `--dataset` path) is a git repository root.
2. **Discover** all subdatasets by recursively parsing `.gitmodules` files.
3. **Pre-flight check**: verify that all worktrees can be created (no branch conflicts, no existing paths without `--force`). If any would fail, abort before creating anything.
4. **Create the superdataset worktree** via `git worktree add`.
5. **For each subdataset** (sorted by path so parents come before children):
   - Clean up any gitlink file or placeholder directory left by the parent worktree at the subdataset mount point.
   - Run `git worktree add` against the subdataset's original repository.
6. **Report** results as each worktree is created, with a summary at the end.

Subdatasets that are not installed (no `.git` present) are skipped. A failed subdataset does not abort the remaining ones.

### Remove

1. **Determine** if the target is a path or branch name.
2. **Process datasets deepest-first** so children are removed before parents.
3. **Remove each worktree** via `git worktree remove`, with a fallback for DataLad repos (delete + prune).
4. Optionally **delete the branch** (`git branch -d`, or `-D` with `--force`).

### List

Shows all datasets that have additional worktrees beyond their main working directory, with the branch checked out in each.

## Requirements

- Python >= 3.11
- git (on PATH)
- [DataLad](https://www.datalad.org/) (optional; enables `datalad worktree-*` commands)

## License

MIT
