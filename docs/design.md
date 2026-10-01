# Design notes

## The premise: worktrees are ephemeral

A worktree exists for one run: create it, run the pipeline, fetch the results home, throw it away. Every other decision follows from this.

- **The main checkout is the only lasting place.** Results come home through `fetch`, and a new worktree starts from the main checkout's state, mtimes included both ways. Otherwise Snakemake reruns, on one side, work the other side already did.
- **Creating is re-creating.** `add` replaces an existing worktree and starts every branch at HEAD. Going back to a particular state is possible with `--follow-parent <branch>`.
- **Only unfetched commits are protected.** Uncommitted changes are discarded by `add` and `delete` alike. `-f` means "throw the commits away".
- **All-or-nothing.** `add` and `delete` check every dataset before touching any, and `-n` runs those same checks and stops.

## Containers

- **Per-worktree config.** `add` writes the bind-mount option and a `cmdexec` carrying `{{bindpaths}}` with `git config --worktree`, so machine-specific paths stay out of the main checkout and out of history.
- **One empty `bindpaths` is committed** to `.datalad/config`. `datalad run` records `{{bindpaths}}` unexpanded, so a record made in a worktree only reruns elsewhere if the name resolves there too, to nothing.
- **Only the superdataset is configured** (#27). A container registered in a subdataset gets no bind paths.

## mtimes

Snakemake decides what to rerun by mtime, but git carries content, not timestamps:

- **On `add`,** the checkout stamps every file "now", superdataset first, so every `code/` file ends up newer than the outputs derived from it.
- **On `fetch`,** a merge moves only what it rewrites. That misses the grandparent directory a `directory()` output is judged by.
- **On `datalad run` with identical outputs,** the local mtimes are correct, but since no content is shipped home upon `fetch`, the job is stale in the main checkout.

So mtimes are copied from the other working tree: main → worktree on `add`, worktree → main on `fetch`. It covers directories, for `directory()` outputs, and Snakemake's gitignored `.snakemake_timestamp` markers (#39), which Snakemake reads instead of the directory and `snakemake --touch` stamps alone.

What keeps it safe:

- **Match on blob OID, not path,** so only byte-identical content inherits a timestamp. Skip paths dirty on either side.
- **Never follow symlinks.** Annexed files point into an object store shared with the main repository.
- **Skip unlocked annexed files.** Restamping one forces a re-hash through git-annex's clean filter: stamping 10099 symlinks cost the next `git status` 0.11 s, stamping 28 unlocked files (3.71 GB) cost it **152 s**.
- **No mtimes from commit dates** (the `git-restore-mtime` approach). jj squash and rebase rewrite these repositories routinely, so every file would look new.