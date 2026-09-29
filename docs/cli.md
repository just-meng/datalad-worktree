# CLI reference

```bash
worktree add runs /tmp/worktrees/runs           # standalone CLI
datalad worktree-add runs /tmp/worktrees/runs   # DataLad extension, same arguments
python -m datalad_worktree add runs /tmp/worktrees/runs
```

Commands run from the superdataset root, or take `-d <path>` to name it.

## `worktree add`

```
worktree add <branch> <worktree-path> [options]

  <branch>                  branch to check out in every worktree
  <worktree-path>           path for the superdataset worktree

  -n, --dry-run             show what would be done, refusals included
  -f, --force               replace an existing worktree even if it holds
                            commits the main checkout lacks, discarding them
  --follow-parent [<commit>]
                            check each subdataset out at the commit its parent
                            records; with a commit or tag, the superdataset too
  --no-bindpaths            don't configure container bind mounts
  --no-mtimes               don't copy mtimes (Snakemake markers included)
                            from the main checkout
  -d, --dataset <path>      superdataset root (default: current directory)
```

Creates a worktree for the superdataset and every installed subdataset, all on `branch`.

```bash
worktree add runs /tmp/worktrees/runs      # replaces the worktree if one is already there
worktree add -f runs /tmp/worktrees/runs   # ... even if it holds commits never fetched
```

### Existing branches

| `branch` exists in | Each dataset's worktree | Reported as |
|---|---|---|
| no dataset | new branch at that dataset's HEAD | `(new branch)` |
| every dataset | branch checked out as it stands | `(existing branch)` |
| some datasets | branch reset to that dataset's HEAD | `(leftover branch reset)` |

Resetting refuses if the branch holds commits its checkout lacks. `worktree fetch` it first, or `-f` to discard them.

### Replacing an existing worktree

A worktree already at `<worktree-path>` is deleted, branch included, and created afresh. Uncommitted changes in it are discarded. It refuses if the worktree holds commits the main checkout lacks, unless `-f`. A directory there that git does not know as a worktree is never deleted.

### Pre-flight

Before creating anything, `add` checks every dataset. It verifies the branch isn't checked out elsewhere, the superdataset's destination is free, and no leftover branch holds commits its checkout lacks. If any check fails, nothing is created. `-n` runs the same checks.

- **Replacement runs before these checks,** so if a check then fails, the old worktree is already gone.
- **Once creation starts,** a failing subdataset doesn't stop the others. A failing superdataset stops everything.

### `--follow-parent`

By default each dataset checks out `branch` on its own, so a subdataset lands on its own branch tip. That may not be the commit the superdataset records for it. `--follow-parent` checks each subdataset out at the commit its parent records instead.

```bash
worktree add runs /tmp/wt --follow-parent           # as the superdataset records it now
worktree add rerun /tmp/wt --follow-parent v1.0     # as commit or tag v1.0 recorded it
```

With a commit or tag:

- **The superdataset is checked out there too.**
- **The subdatasets are the ones that commit recorded.** One added later is left out. One recorded then is included even if the current checkout no longer lists it, and it is skipped as not installed if you don't have it.
- **`branch` is created, or moved, in every dataset** at its recorded commit, so you can work and commit there.
- **It refuses, creating nothing,** if the commit can't be resolved, or if a subdataset lacks the commit recorded for it (typically: never fetched there).

### Containers and mtimes

The last two steps run over all the worktrees at once:

- **Container bind mounts** are configured for containers registered in the superdataset. Containers registered in a subdataset are not configured. `--no-bindpaths` skips this step.
- **mtimes are copied from the main checkout,** Snakemake's `.snakemake_timestamp` markers included. `--no-mtimes` skips this step.

## `worktree fetch`

```
worktree fetch [target] [options]

  [target]                  worktree path or branch name to fetch from
                            (default: the checkout this worktree came from)

  -n, --dry-run             show what would be merged, refusals included
  --no-mtimes               don't copy mtimes (Snakemake markers included)
                            from the target
  -d, --dataset <path>      checkout to fetch into (default: current directory)
```

Brings `target`'s commits into the checkout you are standing in, then copies mtimes from `target`.

```bash
cd /data/my-project
worktree fetch runs        # results home

cd /tmp/worktrees/runs
worktree fetch             # new code and inputs in
```

- **No `target` from a main checkout is an error.** There is no "checkout it came from", so name one.
- **How commits come in:**
  - Where your side has no commits of its own, the fetch fast-forwards.
  - Where both sides have commits, it merges (git >= 2.38; older git refuses).
  - Where the merge would conflict, it refuses and names the paths. The usual case is both sides having moved the same subdataset.
- **Uncommitted changes block it only where the fetch would overwrite them,** and those paths are named.
- **A dataset the worktree never changed is skipped.** Your side is ahead there, so there is nothing to bring in.
- **A detached HEAD in any of the worktree's datasets refuses the whole fetch.**

## `worktree delete`

```
worktree delete <target> [options]

  <target>                  worktree path or branch name

  -n, --dry-run             show what would be deleted, refusals included
  --keep-branch             keep the branch
  -f, --force               delete despite uncommitted changes, and delete an
                            unmerged branch
  -d, --dataset <path>      superdataset root (default: current directory)
```

Deletes the worktree in every dataset, deepest first. It does not ask for confirmation, so use `-n` to preview.

- **The branch is deleted too,** unless `--keep-branch`. A branch holding commits its checkout lacks is kept and reported as an error, unless `-f`.
- **A worktree with uncommitted or untracked changes is refused, unless `-f`.** So is every dataset above it, since deleting a parent would delete it too.
- **The main working tree is never deleted.**
  - Named by path, it is reported as "the main working tree, not a worktree".
  - Named by branch, it is passed over: "no worktree on branch".

## `worktree list`

```
worktree list [options]

  -d, --dataset <path>      superdataset root (default: current directory)
```

`worktree` with no subcommand runs `list`, in the standalone CLI only. The listing shows datasets that have worktrees beyond the main checkout, grouped by the hierarchy each belongs to, with the main checkout's group first:

```
master
  .           /mnt/Data/et_psychedelics/processed/2p
runs
  .           /mnt/Data/worktrees/2p-runs
  code        /mnt/Data/worktrees/2p-runs/code (detached)
  inputs/raw  /mnt/Data/worktrees/2p-runs/inputs/raw
```

A worktree directory deleted by other means (`rm -rf`) is pruned first, so it doesn't appear.
