# Design notes

## The premise: worktrees are ephemeral

A worktree exists for one run: create it, run the pipeline, fetch the results home, then throw it away. Nothing of value is meant to live only there. Every other decision follows from this.

- **The main checkout is the only lasting place.**
  - Results have to come home: commits through `fetch`, and their mtimes with them, or the main checkout reruns work the worktree already did.
  - A new worktree has to start from the main checkout's state, mtimes included, or it reruns work the main checkout already has.
- **Creating is re-creating.** `add` replaces an existing worktree by default and starts every branch at HEAD, so an old run never comes back by accident. Going back to one is explicit: `--follow-parent <branch>`.
- **Deleting is routine.** There is no prompt, and the branch goes too.
- **The only thing to protect is work that hasn't come home yet.** Every refusal guards exactly that:
  - commits the main checkout lacks, when replacing a worktree, resetting a branch, or deleting a branch;
  - uncommitted changes, when deleting a worktree.

  `-f` means "I know, throw it away".
- **A worktree that lives on goes stale by itself.** Snakemake keeps the *text* of each command in untracked `.snakemake` records. A later edit to a rule then triggers Snakemake's `code` rerun in the old worktree, but not in a fresh one.

## Containers

- **Per-worktree config, not committed config.** `add` writes the bind-mount option and a `cmdexec` carrying `{{bindpaths}}` with `git config --worktree`. The paths are machine-specific, so they stay out of the main checkout and out of history.
- **One empty `bindpaths` is committed** to `.datalad/config`. `datalad run` records the command with `{{bindpaths}}` unexpanded, so a record made in a worktree only reruns elsewhere if the name resolves there too, to nothing.
- **Only the superdataset is configured** (#27). Committing that placeholder inside a subdataset would put the subdataset one commit past what the superdataset records for it, so every new worktree would start with the subdataset showing as modified. The cost: a container registered in a subdataset gets no bind paths.

## mtimes

**The tension.** Snakemake decides what to rerun by comparing mtimes, but git and DataLad carry content, not timestamps. That breaks in both directions:

- **Creating a worktree.** The checkout stamps every file "now", and the superdataset goes before its subdatasets. Every `code/` file then ends up newer than every output derived from it, so everything looks stale.
- **Bringing results home.** `datalad run` in the worktree updates mtimes only there. A merge moves only what it rewrites, which misses two cases:
  - A changed output moves its file and parent directory, but never the grandparent that a `directory()` output is judged by.
  - A byte-identical output makes no commit, so nothing moves at all.

  Without transporting mtimes, the main checkout reruns work the worktree already did.

**What we do:**

- **Copy mtimes from the other working tree:** main → worktree on `add`, worktree → main on `fetch`. It runs last, once every worktree exists, because the ordering being repaired crosses datasets.
- **Directories too,** because Snakemake judges a `directory()` output by the directory's own mtime.
- **Snakemake's `.snakemake_timestamp` markers too** (#39). When a marker exists, Snakemake reads it instead of the directory, and `snakemake --touch` stamps only the marker. Markers are gitignored, so without this a touched output looked stale in every fresh worktree. The marker is created before its directory is stamped, because creating a file moves its directory's mtime.

**What keeps it safe:**

- **Match on blob OID, not path,** so only byte-identical content inherits a timestamp. Skip paths that are dirty on either side.
- **Never follow symlinks.** Annexed files point into an object store *shared* with the main repository.
- **Skip unlocked annexed files.** git's index is a stat cache, so restamping one forces a re-hash through git-annex's clean filter. Stamping 10099 symlinks cost the next `git status` 0.11 s; stamping 28 unlocked files totalling 3.71 GB cost it **152 s**.
- **No mtimes reconstructed from commit dates** (the `git-restore-mtime` approach). These repositories are routinely rewritten by jj squash and rebase, which would make every file look new.

## `add`

- **An existing branch always starts at HEAD,** as the premise requires.
  - Checking an existing branch out where it sat resurrected an earlier run's code and outputs, in the datasets that still had it.
  - An older rule excepted a branch present in *every* dataset, as a state the hierarchy had recorded. That made the result depend on history the user could not see. Going back to a recorded state is now explicit: `--follow-parent <branch-or-commit>`.
  - Resetting moves a pointer, so a branch holding unfetched commits refuses.
- **Replace by default** (#28). A worktree that is merged or behind holds nothing the main checkout lacks. The branch goes too, so the new worktree starts from the main checkout, not from the old run.
- **All-or-nothing pre-flight.** A half-created hierarchy is harder to clean up than a refusal. `-n` runs the same checks: a dry run that promises what the real run refuses is worse than none.
  - The replaced worktree is deleted only after every check. Deleting it first spared the checks from having to ignore it, but a later refusal then left the old worktree gone and nothing in its place. The checks are told which worktree is being replaced instead.
- **`--follow-parent`** (#29). A subdataset's branch tip and the commit its parent records are different things. Checking out the tip is how a fresh worktree was born with a modified gitlink. The recorded commits are checked for existence before anything is created; otherwise git fails partway, after the superdataset worktree already exists.
- **Discovery reads `.gitmodules` with `configparser`,** with no DataLad call and no gitpython, so DataLad stays optional.

## `fetch`

- **Deepest first,** so a gitlink never arrives before the commit it names.
- **Merge, never rebase, where both sides moved.** A superdataset records subdataset states *by hash*, and a rebase replaces those hashes.
  - Verified: after rebasing a subdataset, the commit the superdataset recorded is on no branch and no longer an ancestor of the tip. After a merge it stays one.
  - Neither conflict handling nor a dirty tree decides this: both operations stop in a conflicted tree, and `rebase --autostash` handles a dirty one.
- **Divergence is judged, not refused.** Recording a subdataset's new state is itself a superdataset commit, so both sides almost always have commits. `git merge-tree --write-tree` merges in memory: different paths merge cleanly, while the same gitlink moved on both sides refuses, because re-saving the superdataset would not resolve it.
- **The pre-flight checks collisions, not cleanliness.** Refusing on any dirty file would block the workflow the command exists for: developing in main while the worktree runs.
- **`behind` is "nothing to ship"**, not a conflict. It is the normal state of a `code/` subdataset the worktree only consumed.

## `delete`

- **The main working tree is never a target.**
  - `git worktree list` reports it beside the linked worktrees, so a branch lookup landed on it and the `rmtree` fallback destroyed the dataset. Annexed ones survived only because `rmtree` trips on mode-555 annex directories.
  - Mainness is asked of git per path (`--git-dir` equals `--git-common-dir`). Inferring it from the resolved dataset made the real main checkout look linked when run from inside a worktree.
  - A `.git`-is-a-directory test is wrong both ways and fails four tests.
- **The `rmtree` fallback never overrules git's refusal of a dirty worktree.** `git worktree remove` fails on every DataLad worktree, whose `.git` is not a gitlink file, so the fallback ran every time. It deleted uncommitted and untracked work without `-f`. It now runs only on a clean worktree. A subdataset directory the command itself just deleted does not count as a change.
- **All-or-nothing on dirty worktrees, like `add`.** A dirty worktree is refused, and so is every dataset above it, since deleting a parent deletes the child. Deleting the clean rest would leave a half-deleted hierarchy. The unmerged-branch refusal stays per branch: the worktree goes, the branch and its commits stay.
  - `-n` runs this same check before predicting. It used to have its own copy that refused per worktree, so with one dirty subdataset it promised to delete the clean ones, and the real run then deleted nothing.
- **The branch goes by default.** A kept branch is one the next `add` has to reset. `git branch -d` still refuses an unmerged one.
- **No confirmation prompt.** The refusals protect work that hasn't come home yet, so a prompt only added friction. It also blocked non-interactive callers such as scripts and agents. `-n` previews.

## `list`

- **Prune first,** so a directory removed with `rm -rf` does not linger as a stale entry.
- **Group by hierarchy, not by branch** (#13). Subdatasets routinely sit on a detached HEAD, and grouping by branch split one hierarchy across two headings.

## Across commands

- **Generators yield one `WorktreeReport` per dataset,** so the CLI and the DataLad interface render the same stream.
- **All git access is `subprocess.run(..., capture_output=True, text=True)`.**
- **A failed subdataset never aborts the rest;** only a superdataset failure is fatal.
