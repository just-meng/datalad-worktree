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

### Existing branches & worktrees

`branch` always starts at each dataset's HEAD. It is created where it is missing, reported as `(new branch)`, and reset where it exists, reported as `(existing branch reset)`. A worktree already at `<worktree-path>` is deleted, branch included, and created afresh. Uncommitted changes in it are discarded. It refuses if the worktree or the branch holds commits the main checkout lacks, unless `-f/--force`. A directory that git does not know as a worktree is never deleted. If `branch` is checked out in a worktree at another path, `add` refuses, even with `-f`: delete that worktree first, or pick another branch name.

### Pre-flight

Before changing anything, `add` checks every dataset. If any check fails, it refuses and touches nothing, not even an existing worktree. It refuses when:

- an existing worktree or branch holds commits the main checkout lacks, unless `-f`;
- a directory at `<worktree-path>` is not one git knows as a worktree;
- `branch` is checked out in a worktree at another path;
- with `--follow-parent`, the commit can't be resolved, or a subdataset lacks the commit recorded for it.

Only once every check has passed is an existing worktree deleted and the new ones created. If git still fails partway, for a reason no check can foresee (a stale lock, a full disk), `add` stops and deletes the worktrees it created. A replaced worktree stays deleted: it held nothing the main checkout lacks, or `-f` said to discard it.

### Dry run

`-n` runs the pre-flight and stops, changing nothing. It reports the refusals, or what the real run would do: the worktree it would replace, and for each dataset where its worktree would go and whether `branch` would be new or reset.

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
  --keep-branch             keep the branch, and with it any commits the
                            main checkout lacks
  -f, --force               delete despite uncommitted changes or commits
                            the main checkout lacks, discarding them
  -d, --dataset <path>      superdataset root (default: current directory)
```

Deletes the worktree in every dataset, deepest first. It does not ask for confirmation, so use `-n` to preview.

- **The branch is deleted too,** unless `--keep-branch`.
- **All-or-nothing.** Before deleting anything, `delete` checks every worktree. Unless `-f`, it refuses and deletes nothing if any worktree has:
  - uncommitted or untracked changes;
  - commits the main checkout lacks. With `--keep-branch` these are kept on the branch, so only a worktree with a detached HEAD is refused for them.
- **`-n` runs the same checks and stops,** so it never promises a deletion the real run refuses.
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
