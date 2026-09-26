# CLI reference

Three equivalent entry points:

```bash
worktree add runs /tmp/worktrees/runs           # standalone CLI
datalad worktree-add runs /tmp/worktrees/runs   # DataLad extension
python -m datalad_worktree add runs /tmp/worktrees/runs
```

All commands run from the superdataset root, or take `-d <path>` to name it. Output is colored when stdout is a TTY; `--no-color` disables it.

See [design.md](design.md) for why each command behaves the way it does.

## `worktree add`

```
worktree add <branch> <worktree-path> [options]

  <branch>                  branch to create or checkout in every worktree
  <worktree-path>           path for the superdataset worktree

  -n, --dry-run             show what would be done without doing it
  -f, --force               replace worktrees already at the destination,
                            deleting their branches too; refuses if any still
                            holds commits the main checkout lacks
  -F, --force-unmerged      replace them even then, discarding that work
                            (implies --force)
  --no-create-branch        only checkout existing branches, don't create new ones
  --no-bindpaths            don't configure container bind mounts
  --no-mtimes               don't copy file mtimes from the source working trees
  -d, --dataset <path>      superdataset root (default: current directory)
```

Creates a worktree for the superdataset and every installed subdataset, on `branch`. Subdatasets that are not installed are skipped.

Where the branch does not exist it is created from that dataset's current HEAD. Where it exists, what happens depends on whether it exists *everywhere*:

- **In only some datasets** — a leftover, since `worktree delete` keeps branches by default. It is reset to that dataset's current HEAD, reported as `(leftover branch reset)`, so the worktree is a fresh start rather than a hybrid of the last run and the present. Refused instead, changing nothing, if that branch holds commits its checkout lacks — a finished run whose results were never fetched, or a branch you created in one dataset on purpose. Then either `worktree fetch` it first, give the branch to every dataset, or `-F` to reset it and discard those commits.
- **In every dataset** — a state the hierarchy once recorded, since the superdataset commit names the subdataset commits belonging with it. Checked out as it stands, reported as `(existing branch)`.

So each line says which happened: `(new branch)`, `(existing branch)`, or `(leftover branch reset)`.

`--no-create-branch` asks for the branch as it stands, so it never resets and fails on datasets that lack it.

All-or-nothing: a pre-flight check runs first, and if any dataset would fail (branch already checked out elsewhere, destination path occupied) nothing is created. A subdataset that fails during creation does not abort the rest; a superdataset failure does.

Two steps run at the end, over all the worktrees at once: container bind-mount configuration and mtime copying. Skip them with `--no-bindpaths` / `--no-mtimes`.

```bash
worktree add experiment /tmp/wt
worktree add -n experiment /tmp/wt                  # dry run
worktree add --no-create-branch v1.0 /tmp/wt        # refuse unless the branch exists
worktree add -f runs /tmp/worktrees/runs            # replace an existing worktree
worktree add -F runs /tmp/worktrees/runs            # ... discarding unmerged work
```

`-f` replaces the worktree *and* deletes its branch, so the replacement starts from the main checkout's current state instead of inheriting the old branch's commits — equivalent to a brand new worktree. Only paths git reports as worktrees are replaced; a plain directory sitting at the destination gets the usual "worktree root already exists" refusal.

## `worktree fetch`

```
worktree fetch [target] [options]

  [target]                  worktree path or branch name to fetch from
                            (default: the working tree this worktree came from)

  -n, --dry-run             show what would be done without doing it
  --no-mtimes               don't refresh mtimes from the source afterwards
  -d, --dataset <path>      checkout to fetch into (default: current directory)
```

Brings `target`'s commits into the checkout you are standing in, then refreshes mtimes *from* `target`. `target` is a worktree path or a branch name, resolved the same way `worktree delete` resolves its target.

Data always lands in the tree you are standing in, so direction follows from where you run it:

```bash
cd /data/my-project
worktree fetch runs        # ship a finished run's results home

cd /tmp/worktrees/runs
worktree fetch             # the other way: bring new code and inputs in
```

Unrelated uncommitted work does not block it: only paths the fetch would actually overwrite are refused, and they are named. A dataset the worktree merely consumed (your `code/` subdataset, say) is strictly behind, which is "nothing to ship" rather than a conflict, and is skipped.

## `worktree delete`

```
worktree delete <target> [options]

  <target>                  worktree path or branch name to delete

  --delete-branch           also delete the branch (safe delete; refuses if unmerged)
  -f, --force               force deletion even with uncommitted changes;
                            force-delete the branch
  -y, --yes                 skip the confirmation prompt
  -d, --dataset <path>      superdataset root (default: current directory)
```

Deletes deepest-first, so children go before parents. Previews the directories and asks for confirmation unless `-y`. The main working tree is never a target, by path or by branch — it is reported as "not a worktree" and nothing changes.

```bash
worktree delete my-feature
worktree delete /tmp/wt
worktree delete --yes my-feature
worktree delete --delete-branch my-feature
worktree delete --force --delete-branch my-feature
```

## `worktree list`

```
worktree list [options]
```

Also the default when no subcommand is given (`worktree` alone). Shows only datasets with worktrees beyond the main one, grouped by the hierarchy each belongs to:

```
runs
  .           /mnt/Data/worktrees/2p-runs
  code        /mnt/Data/worktrees/2p-runs/code (detached)
  inputs/raw  /mnt/Data/worktrees/2p-runs/inputs/raw
```

Every dataset is pruned first, so a worktree directory removed some other way (`rm -rf` instead of `worktree delete`) drops out of the listing instead of lingering as a stale entry.
