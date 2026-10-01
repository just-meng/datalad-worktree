# Design notes

## The premise: worktrees are ephemeral

A worktree exists for one run: create it, run the pipeline, fetch the results home, throw it away. Every other decision follows from this.

- **The main checkout is the only lasting place.** Results come home through `fetch`, and a new worktree starts from the main checkout's state, mtimes included both ways. Otherwise one side reruns work the other already did.
- **Creating is re-creating.** `add` replaces an existing worktree and starts every branch at HEAD, so an old run never comes back by accident. Going back to one is explicit: `--follow-parent <branch>`.
- **Only unfetched commits are protected.** Commits are the only work `fetch` can bring home, so every refusal guards them. Uncommitted changes are discarded by `add` and `delete` alike. `-f` means "throw the commits away".
- **All-or-nothing.** Every command checks every dataset before touching any, and `-n` runs those same checks and stops. A half-done hierarchy is harder to clean up than a refusal, and a dry run that promises what the real run refuses is worse than none.
- **A worktree that lives on goes stale by itself.** Snakemake keeps each command's text in untracked `.snakemake` records, so a later rule edit triggers a `code` rerun in the old worktree but not in a fresh one.

## Containers

- **Per-worktree config.** `add` writes the bind-mount option and a `cmdexec` carrying `{{bindpaths}}` with `git config --worktree`, so machine-specific paths stay out of the main checkout and out of history.
- **One empty `bindpaths` is committed** to `.datalad/config`. `datalad run` records `{{bindpaths}}` unexpanded, so a record made in a worktree only reruns elsewhere if the name resolves there too, to nothing.
- **Only the superdataset is configured** (#27). The placeholder committed in a subdataset would put it one commit past what the superdataset records, so every new worktree would start with a modified subdataset. The cost: a container registered in a subdataset gets no bind paths.

## mtimes

Snakemake decides what to rerun by mtime, but git carries content, not timestamps:

- **On `add`,** the checkout stamps every file "now", superdataset first, so every `code/` file ends up newer than the outputs derived from it.
- **On `fetch`,** a merge moves only what it rewrites. That misses the grandparent directory a `directory()` output is judged by, and byte-identical outputs, which make no commit.

So mtimes are copied from the other working tree: main → worktree on `add`, worktree → main on `fetch`. This runs last, across all datasets at once, because the broken ordering crosses datasets. It covers directories, for `directory()` outputs, and Snakemake's gitignored `.snakemake_timestamp` markers (#39), which Snakemake reads instead of the directory and `snakemake --touch` stamps alone. A marker is created before its directory is stamped, since creating it moves the directory's mtime.

What keeps it safe:

- **Match on blob OID, not path,** so only byte-identical content inherits a timestamp. Skip paths dirty on either side.
- **Never follow symlinks.** Annexed files point into an object store shared with the main repository.
- **Skip unlocked annexed files.** Restamping one forces a re-hash through git-annex's clean filter: stamping 10099 symlinks cost the next `git status` 0.11 s, stamping 28 unlocked files (3.71 GB) cost it **152 s**.
- **No mtimes from commit dates** (the `git-restore-mtime` approach). jj squash and rebase rewrite these repositories routinely, so every file would look new.

## `add`

- **An existing branch is reset to HEAD.** Checking it out where it sat resurrected an earlier run's code and outputs. An older exception for a branch present in every dataset made the result depend on history the user could not see. A branch holding unfetched commits refuses.
- **Replace by default** (#28), branch included, so the new worktree starts from the main checkout, not from the old run.
- **The replaced worktree is deleted only after every check.** Deleting it first spared the checks from having to ignore it, but a later refusal then left the old worktree gone and nothing in its place.
- **A failure no check foresaw rolls back** what was created. Carrying on with the other subdatasets left a hierarchy with holes in it. Test: a stale `refs/heads/<branch>.lock` in one subdataset.
- **`--follow-parent`** (#29). A subdataset's branch tip is not the commit its parent records, and checking out the tip gave fresh worktrees a modified gitlink. The recorded commits are checked for existence first; otherwise git fails after the superdataset worktree already exists.
- **Discovery reads `.gitmodules` with `configparser`,** so DataLad stays optional.

## `fetch`

- **Deepest first,** so a gitlink never arrives before the commit it names.
- **Merge, never rebase, where both sides moved.** A superdataset records subdataset states by hash. Verified: after rebasing a subdataset, the recorded commit is on no branch; after a merge it stays an ancestor of the tip. Conflicts and dirty trees don't decide this, since both operations handle them.
- **Divergence is judged, not refused.** Recording a subdataset's new state is itself a superdataset commit, so both sides almost always moved. `git merge-tree --write-tree` predicts the merge: different paths merge, the same gitlink moved on both sides refuses.
- **Collisions, not cleanliness.** Refusing on any dirty file would block the workflow the command exists for: developing in main while the worktree runs.
- **`behind` is "nothing to ship",** the normal state of a `code/` subdataset the worktree only consumed.
- **A git failure partway stops, without undoing** the merges already done in deeper datasets. They are real progress, a re-run continues from them, and undoing them would mean resetting branches in the main checkout.

## `delete`

- **The main working tree is never a target.** `git worktree list` reports it beside the linked worktrees, so a branch lookup once landed on it and the `rmtree` fallback destroyed the dataset. Mainness is asked of git per path (`--git-dir` equals `--git-common-dir`). Inferring it from the resolved dataset misjudged it when run from inside a worktree, and a `.git`-is-a-directory test is wrong both ways.
- **The `rmtree` fallback runs on every DataLad worktree,** because `git worktree remove` fails where `.git` is not a gitlink file. It is safe only because of the rule above.
- **Unfetched commits refuse up front,** with the same test as `add`'s replacement. Refusing per branch afterwards, as `git branch -d` did, deleted the rest of the hierarchy around the refusal. `--keep-branch` keeps the commits, so only a detached worktree refuses then.
- **`-n` shares the real pre-flight.** A separate copy drifted from it and promised deletions the real run refused.
- **A git failure partway doesn't stop the rest.** A deletion can't be undone, and finishing it is closer to what was asked.
- **The branch goes by default.** A kept one is only something the next `add` has to reset.
- **No confirmation prompt.** The refusals protect what matters; a prompt added friction and blocked scripts and agents.

## `list`

- **Prune first,** so a worktree removed with `rm -rf` does not linger.
- **Group by hierarchy, not by branch** (#13). Subdatasets often sit on a detached HEAD, and grouping by branch split one hierarchy across two headings.

## Across commands

- **Generators yield one `WorktreeReport` per dataset,** so the CLI and the DataLad interface render the same stream.
- **All git access is `subprocess.run(..., capture_output=True, text=True)`.**
