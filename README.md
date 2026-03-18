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
uv sync --extra dev

# Run from any directory using local code
uv run --project ~/path/to/datalad-worktree worktree list
uv run --project ~/path/to/datalad-worktree datalad worktree-list
```

## Quick Start

```bash
cd /data/my-superdataset

# Create nested worktrees
worktree add /tmp/worktrees/my-feature my-feature

# List all worktrees across the hierarchy
worktree list

# Remove worktrees by branch name
worktree remove my-feature
```

Creating worktrees discovers all subdatasets and produces:

```
/tmp/worktrees/my-feature/              <- superdataset worktree (branch: my-feature)
├── sub-01/                             <- subdataset worktree
│   └── derivatives/                    <- nested subdataset worktree
├── sub-02/
│   └── derivatives/
└── code/shared-library/                <- subdataset worktree at any depth
```

Each directory is a proper git worktree checked out on `my-feature`. If the branch doesn't exist in a given dataset, it is created automatically.

## Usage

All commands are run from the superdataset root (or pass `-d <path>` to specify it).

### Standalone CLI

```bash
# Create worktrees
worktree add <worktree-path> <branch>
worktree add --dry-run /tmp/wt experiment
worktree add --force /tmp/wt my-feature
worktree add --no-create-branch /tmp/wt v1.0

# List worktrees (grouped by branch)
worktree list

# Remove worktrees (prompts for confirmation)
worktree remove my-feature
worktree remove /tmp/wt
worktree remove --yes my-feature              # skip prompt
worktree remove --delete-branch my-feature    # also delete the branch
worktree remove --force --delete-branch my-feature
```

### DataLad Commands

If DataLad is installed, the tool registers as a DataLad extension:

```bash
datalad worktree-add /tmp/wt my-feature
datalad worktree-list
datalad worktree-remove my-feature
```

## CLI Reference

### `worktree add`

```
worktree add [-h] [-n] [-f] [--no-create-branch] [-d DATASET]
             worktree_path branch

  -n, --dry-run             Show what would be done without doing it
  -f, --force               Pass --force to git worktree add
  --no-create-branch        Only checkout existing branches, don't create new ones
```

### `worktree list`

```
worktree list [-h] [-d DATASET]
```

### `worktree remove`

```
worktree remove [-h] [--delete-branch] [-f] [-y] [-d DATASET] target

  --delete-branch           Also delete the branch (safe delete; refuses if unmerged)
  -f, --force               Force removal even with uncommitted changes; force-delete branch
  -y, --yes                 Skip confirmation prompt
```

## How It Works

### Add

1. **Discover** all subdatasets by recursively parsing `.gitmodules` files.
2. **Pre-flight check**: verify all worktrees can be created (no branch conflicts, no existing paths without `--force`). If any would fail, abort before creating anything.
3. **Create worktrees** for the superdataset and each subdataset, with real-time progress.

Subdatasets that are not installed (no `.git` present) are skipped. A failed subdataset does not abort the remaining ones.

### List

Shows worktrees grouped by branch. Main worktrees are listed first under the superdataset's branch, followed by each extra branch as a separate section.

### Remove

1. **Resolve** which worktrees match the target (path or branch name).
2. **Preview** the directories that will be deleted and ask for confirmation (`--yes` to skip).
3. **Remove** deepest-first so children are removed before parents. Falls back to manual deletion for DataLad repos where `git worktree remove` fails.
4. Optionally **delete the branch** (`git branch -d`, or `-D` with `--force`).

## Requirements

- Python >= 3.11
- git (on PATH)
- [DataLad](https://www.datalad.org/) (optional; enables `datalad worktree-*` commands)

## License

MIT
