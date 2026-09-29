# CLI reference

Three entry points:

```bash
worktree add runs /tmp/worktrees/runs           # standalone CLI
datalad worktree-add runs /tmp/worktrees/runs   # DataLad extension
python -m datalad_worktree add runs /tmp/worktrees/runs
```

All commands run from the superdataset root, or take `-d <path>` to name it. The `datalad worktree-*` commands take the same arguments, except that bare `worktree` defaults to `list`. Output is colored only when stdout is a terminal.

This is a behaviour reference: what each command and flag does. For *why* — the annex/container problem, the mtime problem, merge vs rebase, the delete guards — see [design.md](design.md).

## `worktree add`

```
worktree add <branch> <worktree-path> [options]

  <branch>                  branch to create or checkout in every worktree
  <worktree-path>           path for the superdataset worktree

  -n, --dry-run             show what would be done without doing it
  -f, --force               replace an existing worktree even if it holds
                            commits the main checkout lacks, discarding them
  --follow-parent [<commit>]
                            take each subdataset's state from the commit its
                            parent records, not from the branch name; with a
                            commit, mirror the whole state it recorded
  --no-bindpaths            don't configure container bind mounts
  --no-mtimes               don't copy file mtimes from the source working trees
  -d, --dataset <path>      superdataset root (default: current directory)
```

Creates a worktree for the superdataset and every installed subdataset, on `branch`. Subdatasets that are not installed are skipped.

Where the branch does not exist it is created from that dataset's current HEAD. Where it exists, what happens depends on whether it exists *everywhere*, and each line says which happened:

- **In every dataset** — checked out as it stands, reported as `(existing branch)`.
- **In only some datasets** — a leftover, e.g. from `worktree delete --keep-branch` or a branch delete refused as unmerged. Reset to that dataset's current HEAD, reported as `(leftover branch reset)`. Refused instead, changing nothing, if that branch holds commits its checkout lacks: `worktree fetch` it first, give the branch to every dataset, or `-f` to discard them.
- **Nowhere** — created, reported as `(new branch)`.

### `--follow-parent`: one recorded state instead of one branch name

By default each dataset resolves `branch` for itself, so a subdataset ends up at *its* branch tip — not necessarily the commit the superdataset records for it. `--follow-parent` takes each subdataset's state from what its parent records instead:

```bash
worktree add runs /tmp/wt --follow-parent            # follow the superdataset as it is now
worktree add rerun /tmp/wt --follow-parent 4f2a91c   # mirror the project as that commit recorded it
```

With a commit — a `datalad run` record, say — the commit also defines the *set* of datasets: one added since is absent, one recorded then is included even if the checkout has moved on, and one the checkout does not have is reported as not installed. `branch` is still created in every dataset, at the resolved commit, so the snapshot is something you can work and commit in. It refuses, creating nothing, if the commit cannot be resolved or if a recorded commit is not an object the subdataset actually has (never fetched there).

All-or-nothing: a pre-flight runs first, and if any dataset would fail (branch already checked out elsewhere, superdataset destination occupied) nothing is created. A subdataset that fails during creation does not abort the rest; a superdataset failure does.

Two steps run at the end, over all the worktrees at once: container bind-mount configuration and mtime copying. Skip them with `--no-bindpaths` / `--no-mtimes`. Only the **superdataset's** containers are configured, so a container registered in a subdataset needs its bind paths set up by hand.

```bash
worktree add experiment /tmp/wt
worktree add -n experiment /tmp/wt                  # dry run
worktree add runs /tmp/worktrees/runs               # replaces an existing worktree
worktree add -f runs /tmp/worktrees/runs            # ... even if it holds unfetched work
```

A worktree already at the destination is **replaced by default** (issue #28), branch included, so the new one starts from the main checkout's current state rather than inheriting the old branch's commits. Uncommitted changes there are discarded. Two things still refuse, and `-f` lifts only the first:

- the worktree holds commits the main checkout lacks — a run whose results were never fetched. `worktree fetch` it first, or `-f` to discard them.
- the destination is a directory git does not report as a worktree. Never deleted, flag or no flag; you get "worktree root already exists".

## `worktree fetch`

```
worktree fetch [target] [options]

  [target]                  worktree path or branch name to fetch from
                            (default: the working tree this worktree came from)

  -n, --dry-run             show what would be done without doing it
  --no-mtimes               don't refresh mtimes from the source afterwards
  -d, --dataset <path>      checkout to fetch into (default: current directory)
```

Brings `target`'s commits into the checkout you are standing in, then refreshes mtimes *from* `target`. `target` is a worktree path or a branch name. With no `target`, the source is the working tree this worktree was created from, so from a main checkout you must name one.

Data always lands in the tree you are standing in, so direction follows from where you run it:

```bash
cd /data/my-project
worktree fetch runs        # ship a finished run's results home

cd /tmp/worktrees/runs
worktree fetch             # the other way: bring new code and inputs in
```

Unrelated uncommitted work does not block it: only paths the fetch would actually overwrite are refused, and they are named. A dataset the worktree merely consumed (your `code/` subdataset, say) is strictly behind and is skipped. A dataset whose worktree is on a detached HEAD refuses the whole fetch: there is no branch to bring in, so check one out there first.

## `worktree delete`

```
worktree delete <target> [options]

  <target>                  worktree path or branch name to delete

  -n, --dry-run             show what would be deleted, and what would be refused
  --keep-branch             keep the branch (by default it is deleted too;
                            safe delete, refuses if unmerged)
  -f, --force               force deletion even with uncommitted changes;
                            force-delete the branch
  -d, --dataset <path>      superdataset root (default: current directory)
```

Deletes deepest-first, so children go before parents. Does not ask for confirmation. The branch is deleted too unless `--keep-branch`, and the output says so. The branch delete is the safe one: a branch holding commits its checkout lacks (results never fetched) is refused and kept, unless `-f`. The main working tree is never a target. Named by path, it is reported as "the main working tree, not a worktree". Named by branch, it is passed over, so a branch checked out only there reports "no worktree on branch". Either way nothing changes.

```bash
worktree delete my-feature
worktree delete /tmp/wt
worktree delete --keep-branch my-feature
worktree delete --force my-feature
```

## `worktree list`

```
worktree list [options]

  -d, --dataset <path>      superdataset root (default: current directory)
```

Also the default when no subcommand is given (`worktree` alone). Shows only datasets with worktrees beyond the main one, grouped by the hierarchy each belongs to, with the main checkout's group first:

```
master
  .           /mnt/Data/et_psychedelics/processed/2p
runs
  .           /mnt/Data/worktrees/2p-runs
  code        /mnt/Data/worktrees/2p-runs/code (detached)
  inputs/raw  /mnt/Data/worktrees/2p-runs/inputs/raw
```

Every dataset is pruned first, so a worktree directory removed some other way (`rm -rf` instead of `worktree delete`) drops out of the listing instead of lingering as a stale entry.
