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
uv sync --dev

# Run from any directory using local code
uv run --project ~/path/to/datalad-worktree worktree list
uv run --project ~/path/to/datalad-worktree datalad worktree-list
```

## Quick Start

```bash
cd /data/my-superdataset

# Create nested worktrees
worktree add my-feature /tmp/worktrees/my-feature

# List all worktrees across the hierarchy (also the default with no subcommand)
worktree list

# Delete worktrees by branch name
worktree delete my-feature
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
worktree add <branch> <worktree-path>
worktree add --dry-run experiment /tmp/wt
worktree add --force my-feature /tmp/wt
worktree add --no-create-branch v1.0 /tmp/wt

# List worktrees (grouped by branch); also the default with no subcommand
worktree list
worktree

# Restore file mtimes in an existing worktree (after a get, merge, or checkout)
worktree sync-mtimes
worktree sync-mtimes --from /data/my-superdataset /tmp/wt

# Delete worktrees (prompts for confirmation)
worktree delete my-feature
worktree delete /tmp/wt
worktree delete --yes my-feature              # skip prompt
worktree delete --delete-branch my-feature    # also delete the branch
worktree delete --force --delete-branch my-feature
```

### DataLad Commands

If DataLad is installed, the tool registers as a DataLad extension:

```bash
datalad worktree-add my-feature /tmp/wt
datalad worktree-list
datalad worktree-delete my-feature
datalad worktree-sync-mtimes
```

## CLI Reference

### `worktree add`

```
worktree add [-h] [-n] [-f] [--no-create-branch] [--no-bindpaths] [--no-mtimes]
             [-d DATASET] branch worktree_path

  -n, --dry-run             Show what would be done without doing it
  -f, --force               Pass --force to git worktree add
  --no-create-branch        Only checkout existing branches, don't create new ones
  --no-bindpaths            Don't configure container bind mounts
  --no-mtimes               Don't copy file mtimes from the source working trees
```

### `worktree list`

```
worktree list [-h] [-d DATASET]
```

Also the default when no subcommand is given (`worktree` alone).

### `worktree sync-mtimes`

```
worktree sync-mtimes [-h] [--from REFERENCE] [worktree_path]

  --from REFERENCE          Working tree to copy from (default: the one this
                            worktree was created from)
```

`worktree_path` defaults to the current directory.

### `worktree delete`

```
worktree delete [-h] [--delete-branch] [-f] [-y] [-d DATASET] target

  --delete-branch           Also delete the branch (safe delete; refuses if unmerged)
  -f, --force               Force deletion even with uncommitted changes; force-delete branch
  -y, --yes                 Skip confirmation prompt
```

## How It Works

### Add

1. **Discover** all subdatasets by recursively parsing `.gitmodules` files.
2. **Pre-flight check**: verify all worktrees can be created (no branch conflicts, no existing paths without `--force`). If any would fail, abort before creating anything.
3. **Create worktrees** for the superdataset and each subdataset, with real-time progress.
4. **Configure containers** and **copy mtimes** across every worktree just created (see below).

Subdatasets that are not installed (no `.git` present) are skipped. A failed subdataset does not abort the remaining ones.

### Containers

`datalad containers-run` cannot read annexed files inside a worktree: in a worktree `.git` is a symlink into the main repository, so git-annex object symlinks resolve to paths outside the worktree directory, which a container does not see. The result is `FileNotFoundError` on every annexed input ([datalad-container#288](https://github.com/datalad/datalad-container/issues/288)).

`worktree add` fixes this for any dataset that registers a container with a `cmdexec`. Per worktree it writes, using `git config --worktree` so nothing leaks into the main checkout:

- `datalad.run.substitutions.bindpaths` — the `-B <superdataset>:<superdataset>:ro` option
- `datalad.containers.<name>.cmdexec` — your `cmdexec` with `{{bindpaths}}` inserted before `{img}`

It also commits one line to the tracked `.datalad/config`, on the worktree branch: an empty `datalad.run.substitutions.bindpaths`. `run` records commands with substitutions unexpanded, so without that fallback a run record made in a worktree cannot be rerun anywhere else.

Containers whose `cmdexec` has no `{img}` to anchor the insertion are skipped with a warning, as are containers registered without a `cmdexec` at all. Pass `--no-bindpaths` to skip the whole step.

### Mtimes

Git records content, not timestamps, so every file in a fresh worktree gets the time it was checked out. For a make-style pipeline (Snakemake, Make, redo) the mtime *ordering* between inputs and outputs **is** the up-to-date state, so a new worktree looks arbitrarily stale and reruns work that is already done.

Worse, the ordering is not merely lost but systematically inverted. `worktree add` checks the superdataset out first and each subdataset after, so every file in a `code/` subdataset ends up newer than every output derived from it — exactly the "all your code changed" signal. (Same root cause as [this DataLad blog post](https://blog.datalad.org/posts/snakemake-datalad-worktree/), whose advice is to keep all pipeline steps in one worktree; this removes the need for that rule at creation time.)

As its final step, `worktree add` copies each file's mtime from the working tree the worktree was created from — across all datasets at once, since the ordering being repaired is the cross-dataset one. Directories get the same treatment, because Snakemake's `directory()` outputs read staleness off the directory's own mtime.

Two properties keep it safe:

- **Matching is on blob OID, not path.** A worktree created from a different ref only inherits mtimes for content that is byte-identical; anything that genuinely differs keeps its checkout time. Files that are modified but uncommitted in the reference are skipped for the same reason — their mtime describes content the worktree does not have.
- **Symlinks are never followed.** Annexed files are symlinks into `.git/annex/objects`, an object store *shared* with the main repository. Writing through the link would rewrite the source dataset's own (mode 444) objects, and would be a no-op for the consumer anyway: Snakemake reads the symlink's own mtime.

Reconstructing mtimes from commit dates (the `git-restore-mtime` approach) is deliberately not used — routine history rewriting (`jj squash`, rebase) would make every file look new.

Pass `--no-mtimes` to skip the step. To put mtimes back after something rewrites files in an existing worktree — a `datalad get`, a merge, a `git checkout` — run `worktree sync-mtimes`.

To check the effect on a Snakemake pipeline, diff the dry runs:

```bash
SMK="snakemake -s code/Snakefile -c 1 -k -n"
$SMK | sed -n '/^job/,/^total/p' > /tmp/main.jobs
worktree add runs /mnt/Data/worktrees/2p-runs
(cd /mnt/Data/worktrees/2p-runs && $SMK | sed -n '/^job/,/^total/p') > /tmp/wt.jobs
diff /tmp/main.jobs /tmp/wt.jobs && echo "PASS: worktree inherits main's staleness"
```

A non-empty diff *only* for rules whose staleness comes from a `params`/`code`/`software-env` rerun trigger is expected: those are recorded in the untracked `.snakemake/metadata`, which is not a worktree's concern and is not copied.

### List

Shows worktrees grouped by branch. Main worktrees are listed first under the superdataset's branch, followed by each extra branch as a separate section. Each dataset is pruned first, so a worktree directory removed some other way (e.g. `rm -rf` instead of `worktree delete`) drops out of the listing instead of lingering as a stale entry.

### Delete

1. **Resolve** which worktrees match the target (path or branch name).
2. **Preview** the directories that will be deleted and ask for confirmation (`--yes` to skip).
3. **Delete** deepest-first so children are deleted before parents. Falls back to manual deletion for DataLad repos where `git worktree remove` fails.
4. Optionally **delete the branch** (`git branch -d`, or `-D` with `--force`).

## Requirements

- Python >= 3.11
- git (on PATH)
- [DataLad](https://www.datalad.org/) (optional; enables `datalad worktree-*` commands)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
