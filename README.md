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

# Bring a finished run's results home, mtimes included
worktree fetch my-feature

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

## Recommended Workflow: Treat Worktrees as Disposable

Create a worktree for one run, ship its results home, delete it. Do not keep a
worktree around and run in it repeatedly.

```bash
cd /data/my-superdataset
worktree add runs-2026-09-24 /tmp/worktrees/runs-2026-09-24

cd /tmp/worktrees/runs-2026-09-24
snakemake -c 1                      # the long run

cd /data/my-superdataset
worktree fetch runs-2026-09-24      # results home, mtimes included
worktree delete runs-2026-09-24     # and dispose of it
```

The reason is that a long-lived worktree accumulates its own pipeline state,
and that state goes stale in a way nothing here can repair. Snakemake records
provenance per output under the untracked `.snakemake/metadata` — including the
**text of the shell command** that produced it. Edit a rule afterwards, even
cosmetically, and every output recorded in that worktree trips Snakemake's
`code` rerun trigger.

The effect is counter-intuitive: a worktree that has *never* run is cleaner
than one that has. With no records at all, Snakemake reports "missing
provenance" and falls back to mtimes — which `add` and `fetch` keep correct. A
worktree carrying 178 stale records once turned a 25-job dry run into 78 jobs,
purely because a `-J 16` had been added to a rule since it last ran.

If you do reuse a path, `add -f` replaces the worktree *and* its branch, so the
result is equivalent to a fresh one:

```bash
worktree add -f runs /tmp/worktrees/runs   # refuses if it holds unmerged work
```

`.snakemake/` is gitignored, so it never travels into a worktree in the first
place — deleting the worktree is what stops it going stale.

## Usage

All commands are run from the superdataset root (or pass `-d <path>` to specify it).

### Standalone CLI

```bash
# Create worktrees
worktree add <branch> <worktree-path>
worktree add --dry-run experiment /tmp/wt
worktree add --no-create-branch v1.0 /tmp/wt

# Replace an existing worktree (refuses if it still holds unmerged work)
worktree add -f runs /tmp/worktrees/runs
worktree add -F runs /tmp/worktrees/runs    # discard the unmerged work too

# List worktrees (grouped by branch); also the default with no subcommand
worktree list
worktree

# Bring a worktree's results into this checkout, mtimes included
worktree fetch my-feature                # by branch name
worktree fetch /tmp/wt                   # by path
worktree fetch -n my-feature             # show what would happen

# Run from inside a worktree with no argument to go the other way:
# bring the checkout it came from into this worktree, mtimes included
worktree fetch

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
datalad worktree-fetch my-feature
```

## CLI Reference

### `worktree add`

```
worktree add [-h] [-n] [-f] [-F] [--no-create-branch] [--no-bindpaths]
             [--no-mtimes] [-d DATASET] branch worktree_path

  -n, --dry-run             Show what would be done without doing it
  -f, --force               Replace worktrees already at the destination,
                            deleting their branches too. Refuses if any still
                            holds commits the main checkout lacks
  -F, --force-unmerged      Replace them even then, discarding that work
  --no-create-branch        Only checkout existing branches, don't create new ones
  --no-bindpaths            Don't configure container bind mounts
  --no-mtimes               Don't copy file mtimes from the source working trees
```

### `worktree list`

```
worktree list [-h] [-d DATASET]
```

Also the default when no subcommand is given (`worktree` alone).

### `worktree fetch`

```
worktree fetch [-h] [-n] [--no-mtimes] [-d DATASET] [target]

  -n, --dry-run             Show what would be done without doing it
  --no-mtimes               Don't refresh mtimes from the source afterwards
  -d, --dataset DATASET     Checkout to fetch into (default: current directory)
```

Brings `target`'s commits into the checkout you are standing in, then refreshes
mtimes *from* `target`. Run it from the main checkout after a long run finishes
in the worktree.

`target` is a worktree path **or** a branch name, resolved the same way
`worktree delete` resolves its target. Omit it inside a worktree and it
defaults to the checkout that worktree was created from, which is how you go
the other way -- bringing new code and inputs into a worktree, mtimes
included.

The point of the mtime step: a plain merge moves only what git rewrote, so an
output that came out byte-identical produces no commit and nothing moves, and
even a changed output never moves its *grandparent* directory -- which is what
Snakemake's `directory()` output reads. Without the refresh, freshly computed
results look stale and rerun.

Unrelated uncommitted work does not block it: only paths the fetch would
actually overwrite are refused, by name. A dataset the worktree merely
consumed (your `code/` subdataset, say) is reported as having nothing to ship
and skipped.

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

Only paths git reports as worktrees are replaced. A directory that merely
happens to sit at the destination is never deleted — you get the usual
"worktree root already exists" refusal instead.

Because `-f` deletes the branch as well, the replacement starts from the main
checkout's current state rather than inheriting the old branch's commits. That
is what makes a replaced worktree equivalent to a brand new one.

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

Three properties keep it safe:

- **Matching is on blob OID, not path.** A worktree created from a different ref only inherits mtimes for content that is byte-identical; anything that genuinely differs keeps its checkout time. Files that are modified but uncommitted on either side are skipped for the same reason — their mtime describes content the other side does not have.
- **Symlinks are never followed.** Annexed files are symlinks into `.git/annex/objects`, an object store *shared* with the main repository. Writing through the link would rewrite the source dataset's own (mode 444) objects, and would be a no-op for the consumer anyway: Snakemake reads the symlink's own mtime.
- **Unlocked annexed files are skipped entirely.** git-annex can track a file unlocked — committed as a regular file whose content is a one-line `/annex/objects/…` pointer, with the real bytes in the working tree. Git's index is a *stat cache*: it records the mtime at which each path's content was last verified, and `git status` skips hashing while that still matches. Rewriting a symlink's mtime is free to re-verify, because git only re-reads the link target; rewriting an unlocked annexed file's mtime forces git to re-read and re-hash the whole file through git-annex's clean filter. Measured on one real dataset: stamping 10099 symlinks cost the next `git status` 0.11 s, while stamping 28 unlocked files totalling 3.71 GB cost it **152 s**. Under `annex.thin` it is also the one case where stamping would reach the shared object store, since an unlocked file is then a hardlink to its annex object.

  The practical consequence: outputs your dataset tracks unlocked keep their checkout time rather than inheriting one. If that matters, the cause is usually a repo-global `git annex config --set annex.addunlocked <glob>`, which is inherited by every clone and does not appear in `.git/config`.

Reconstructing mtimes from commit dates (the `git-restore-mtime` approach) is deliberately not used — routine history rewriting (`jj squash`, rebase) would make every file look new.

Pass `--no-mtimes` to skip the step. To put mtimes back after something rewrites files in an existing worktree — a branch switch, say — run `worktree fetch` from inside the worktree, which brings the checkout it came from up to date and refreshes the timestamps with it.

To check the effect on a Snakemake pipeline, diff the dry runs:

```bash
SMK="snakemake -s code/Snakefile -c 1 -k -n"
$SMK | sed -n '/^job/,/^total/p' > /tmp/main.jobs
worktree add runs /mnt/Data/worktrees/2p-runs
(cd /mnt/Data/worktrees/2p-runs && $SMK | sed -n '/^job/,/^total/p') > /tmp/wt.jobs
diff /tmp/main.jobs /tmp/wt.jobs && echo "PASS: worktree inherits main's staleness"
```

A non-empty diff *only* for rules whose staleness comes from a `params`/`code`/`software-env` rerun trigger is expected: those are recorded in the untracked `.snakemake/metadata`, which is not a worktree's concern and is not copied.

### Fetch

The counterpart of `add`: run the long pipeline in a worktree, then bring the
results into the checkout you are standing in.

1. **Pair up** every dataset on both sides, superdataset first.
2. **Pre-flight** all of them. Refuses — touching nothing — if a dataset has
   diverged in a way `git merge-tree` reports as conflicting, or if the
   incoming paths overlap paths you have modified locally.
3. **Transport** deepest-first, so a submodule gitlink never arrives before the
   commit it names. Fast-forward where possible, a real merge where the sides
   have diverged cleanly.
4. **Refresh mtimes** from the source, across the whole hierarchy.

Step 4 is the point. Git moves only what it rewrites, so a changed output moves
its file and its immediate parent but never the *grandparent* directory — which
is what Snakemake's `directory()` output reads — and an output that came out
byte-identical produces no commit at all, so nothing moves. Either way the work
is done and the pipeline would run it again.

Direction follows from where you run it, because the data always lands in the
tree you are standing in:

```bash
cd /data/my-superdataset
worktree fetch runs        # ship the run's results home

cd /tmp/worktrees/runs
worktree fetch             # the other way: bring new code and inputs in
```

Fast-forward is preferred over rebase deliberately. `git rebase` demands a clean
tree unconditionally, even for files it will not touch, while a fast-forward
rewrites only the paths that differ — so results can land in a checkout where
development is still going on, and unrelated work in progress is left alone. A
dataset the worktree merely consumed is strictly *behind*, which is "nothing to
ship" rather than a conflict, and is skipped.

### List

Shows worktrees grouped by the hierarchy each one belongs to. The main checkout comes first under the superdataset's branch, then one section per extra superdataset worktree. Each dataset is pruned first, so a worktree directory removed some other way (e.g. `rm -rf` instead of `worktree delete`) drops out of the listing instead of lingering as a stale entry.

Grouping follows the *worktree a dataset sits under*, not the dataset's own branch. That matters for subdatasets: recording a subdataset commit that is no branch's tip leaves it on a detached HEAD, and grouping by branch would file it under a separate `(detached)` heading, splitting one hierarchy across two places. Instead it is listed with its siblings and annotated:

```
runs
  .           /mnt/Data/worktrees/2p-runs
  code        /mnt/Data/worktrees/2p-runs/code (detached)
  inputs/raw  /mnt/Data/worktrees/2p-runs/inputs/raw
```

### Delete

The main checkout is never a target: `git worktree list` reports it alongside the
linked worktrees, but it is the dataset rather than a worktree of it. Naming it
by path or by its branch is reported as "not a worktree" and changes nothing.

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
