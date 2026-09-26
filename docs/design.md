# Design notes

Why the commands behave the way they do. For flags and invocation, see [cli.md](cli.md).

## Discovery

Subdatasets are found by recursively parsing `.gitmodules` with `configparser` — no DataLad call, no gitpython, no `git submodule` invocation per level. Fast enough to be unconditional, and it works when DataLad is not installed.

Subdatasets are sorted by path so parents are processed before children (`add`) and reversed so children go first (`delete`, `fetch` transport).

`is_git_repo()` answers "can git find a repository from here" — and git walks *up*, so it returns True for an empty submodule mount point, reporting the enclosing superdataset. Anywhere one dataset is resolved against another, use `is_git_repo_root()`.

## Add

1. **Discover** all subdatasets.
2. **Pre-flight**: verify every worktree can be created — no branch checked out elsewhere (`git_branch_checked_out_at`), no occupied destination. All-or-nothing: if any non-skipped dataset would fail, nothing is created.
3. **Create** worktrees, superdataset first, yielding a report per dataset as it goes.
4. **Configure containers**, then **copy mtimes**, across every worktree just created.

Branch logic is per-dataset and independent: if the branch exists, check it out; if not, create it with `-b`. Subdatasets that are not installed are skipped, and a failed subdataset does not abort the rest — only a superdataset failure is fatal.

**Gitlink cleanup.** Creating the superdataset worktree makes git place gitlink files at every submodule mount point. `_prepare_destination()` removes these before each subdataset worktree is created, handling three filesystem states: a gitlink file, an empty directory, and a directory containing only `.git`.

**Replacement (`-f` / `-F`).** `existing_worktrees()` lists only destinations git reports as worktrees, so a stray directory is never deleted. `unmerged_worktrees()` reuses `fetch.fast_forward_state()`: merged means `up-to-date` or `behind` — the state `worktree fetch` leaves behind — and a detached HEAD counts as unmerged. Replacement runs *before* the pre-flight, so the path and branch conflicts it would otherwise report are already gone; `_preflight_check(replaced_root=...)` additionally ignores conflicts pointing at the worktree being replaced, which is what makes `--dry-run` truthful. The branch is deleted too, so the recreate starts from the main checkout's HEAD instead of inheriting stale commits. `-F` discards uncommitted changes, matching `worktree delete --force`.

## Containers

`datalad containers-run` cannot read annexed files inside a worktree: `.git` there is a link into the main repository, so git-annex object symlinks resolve to paths outside the worktree directory, which the container does not bind-mount. The result is `FileNotFoundError` on every annexed input ([datalad-container#288](https://github.com/datalad/datalad-container/issues/288)).

For every dataset registering a container with a `cmdexec`, `worktree add` writes two values with `git config --worktree`, so nothing machine-specific leaks into the main checkout or into git history:

- `datalad.run.substitutions.bindpaths` — the `-B <superdataset>:<superdataset>:ro` option
- `datalad.containers.<name>.cmdexec` — the existing `cmdexec` with `{{bindpaths}}` inserted before `{img}`

It also commits one line to the tracked `.datalad/config` on the worktree branch: an empty `datalad.run.substitutions.bindpaths`. `run` records commands with substitutions *unexpanded*, so without that fallback a run record made in a worktree could not be rerun anywhere else.

Containers whose `cmdexec` has no `{img}` to anchor the insertion are skipped with a warning, as are containers registered without a `cmdexec`. `--no-bindpaths` skips the step.

## Mtimes

Git records content, not timestamps, so every file in a fresh worktree gets the time it was checked out. For a make-style pipeline (Snakemake, Make, redo) the mtime *ordering* between inputs and outputs **is** the up-to-date state, so a new worktree looks arbitrarily stale and reruns work that is already done.

Worse, the ordering is not merely lost but systematically inverted. `worktree add` checks the superdataset out first and each subdataset after, so every file in a `code/` subdataset ends up newer than every output derived from it — exactly the "all your code changed" signal. (Same root cause as [this DataLad blog post](https://blog.datalad.org/posts/snakemake-datalad-worktree/), whose advice is to keep all pipeline steps in one worktree; this removes the need for that rule at creation time.)

So mtime copying runs *last*, after every worktree exists — the ordering being repaired is the cross-dataset one, and repairing it dataset-by-dataset during creation would not fix it. `mtimes.py` copies each path's mtime from the working tree the worktree came from (`WorktreeReport.source`). Directories get the same treatment, because Snakemake's `directory()` outputs read staleness off the directory's own mtime.

Three properties keep it safe:

- **Matching is on blob OID, not path.** A worktree created from a different ref only inherits mtimes for content that is byte-identical; anything that genuinely differs keeps its checkout time. Files modified but uncommitted on either side are skipped for the same reason — their mtime describes content the other side does not have.
- **Symlinks are never followed** (`follow_symlinks=False`). Annexed files are symlinks into `.git/annex/objects`, an object store *shared* with the main repository. Writing through the link would rewrite the source dataset's own (mode 444) objects, and would be a no-op for the consumer anyway: Snakemake reads the symlink's own mtime.
- **Unlocked annexed files are skipped entirely** (`unlocked_annex_paths()`). git-annex can track a file unlocked — committed as mode `100644` whose content is a one-line `/annex/objects/…` pointer, with the real bytes in the working tree. Git's index is a *stat cache*: it records the mtime at which each path's content was last verified, and `git status` skips hashing while that still matches. Rewriting a symlink's mtime is free to re-verify, because git only re-reads the link target; rewriting an unlocked annexed file's mtime forces git to re-read and re-hash the whole file through git-annex's clean filter. Measured on one real dataset: stamping 10099 symlinks cost the next `git status` 0.11 s, while stamping 28 unlocked files totalling 3.71 GB cost it **152 s**. Under `annex.thin` it is also the one case where stamping would reach the shared object store, since an unlocked file is then a hardlink to its annex object. Detection reads blob sizes from `ls-tree -r -l` and only `cat-file`s blobs small enough to be a pointer, so it costs no extra git call on the common path.

  The practical consequence: outputs your dataset tracks unlocked keep their checkout time rather than inheriting one. If that matters, the cause is usually a repo-global `git annex config --set annex.addunlocked <glob>`, which is inherited by every clone and does not appear in `.git/config`.

Reconstructing mtimes from commit dates (the `git-restore-mtime` approach) is deliberately not used — routine history rewriting (`jj squash`, rebase) would make every file look new.

To check the effect on a Snakemake pipeline, diff the dry runs:

```bash
SMK="snakemake -s code/Snakefile -c 1 -k -n"
$SMK | sed -n '/^job/,/^total/p' > /tmp/main.jobs
worktree add runs /mnt/Data/worktrees/2p-runs
(cd /mnt/Data/worktrees/2p-runs && $SMK | sed -n '/^job/,/^total/p') > /tmp/wt.jobs
diff /tmp/main.jobs /tmp/wt.jobs && echo "PASS: worktree inherits main's staleness"
```

A non-empty diff *only* for rules whose staleness comes from a `params`/`code`/`software-env` rerun trigger is expected: those are recorded in the untracked `.snakemake/metadata`, which is not a worktree's concern and is not copied.

## Fetch

The counterpart of `add`: run the long pipeline in a worktree, then bring the results into the checkout you are standing in.

1. **Pair up** every dataset on both sides, superdataset first.
2. **Pre-flight** all of them. Refuses — touching nothing — if a dataset has diverged in a way `git merge-tree` reports as conflicting, or if incoming paths overlap paths modified locally.
3. **Transport** deepest-first, so a submodule gitlink never arrives before the commit it names. Fast-forward where possible, a real merge where the sides diverged cleanly.
4. **Refresh mtimes** from the source, across the whole hierarchy.

**Step 4 is the point.** `git merge`/`rebase` gets content right and mtimes wrong: git moves only what it rewrites, so a changed output moves its file and its immediate parent but never the *grandparent* directory — which is what Snakemake's `directory()` output reads — and an output that came out byte-identical produces no commit at all, so nothing moves. Either way the work is done and the pipeline would run it again. So `fetch` does the transport and then runs `mtimes.sync_dataset` with the direction reversed (worktree = reference, main checkout = target). Blob-OID matching means only byte-identical content inherits a timestamp.

**Divergence is judged, not refused.** Recording a subdataset's new state in the superdataset is itself a superdataset commit, so as soon as work continues in the main checkout both sides have commits and a fast-forward is impossible. `merge_prediction()` runs `git merge-tree --write-tree`, which performs the merge in memory and exits 1 listing conflicted paths. Two commits that moved *different* paths (results on one side, a `code` gitlink on the other) merge cleanly and are merged; both sides moving the *same* gitlink is refused, because that conflict does not resolve by re-saving the superdataset — it leaves the superdataset naming a commit its own subdataset checkout lacks. Needs git >= 2.38; older git falls back to refusing divergence. The three-way gitlink merge keeps *our* value when the worktree never moved it, so `code/` stays consistent and the tree stays clean.

**Fast-forward is neither a merge nor a rebase.** When your side holds no commits the other side lacks, git just moves your branch pointer to theirs: nothing is replayed, nothing is rewritten, no commit is created. `git merge` and `git rebase` produce identical results in that case, so the choice between them only exists once both sides have moved. `fetch` fast-forwards where it can — that only rewrites the paths that differ, so unrelated work-in-progress survives — and merges where it cannot.

**Why merge rather than rebase for the diverged case.** Two arguments that look decisive are not, so they are dismissed first.

*Conflict handling is not the difference.* A conflicting `merge` stops in a conflicted worktree exactly as a conflicting `rebase` does; git cannot store a conflicted tree in a commit either way. Neither is ever reached here, because `merge_prediction()` performs the merge in memory with `git merge-tree` and `fetch` refuses before touching anything — no conflict mode, by design. This matters especially for annexed content: two versions of the same annexed path cannot be content-merged at all (the working-tree file is a symlink to a key), so "resolve the conflict" is not a meaningful offer. Changes to *different* paths merge cleanly whatever the file type, and that is the case `fetch` actually transports.

*The dirty tree is not the difference either.* `git rebase` refuses on a dirty tree, but `git rebase --autostash` does not — it stashes, replays, and reapplies. That is the same trick jj uses, where the working copy **is** a commit that gets snapshotted before every operation. The mtime cost is small: measured, `stash`/`pop` re-times only the files that were dirty and leaves the rest alone, and `fetch` already declines to stamp paths dirty on either side.

*What is left is the real difference:* a merge adds a commit and rewrites nothing, while a rebase replays commits under **new hashes**. In a DataLad hierarchy those hashes are load-bearing — a superdataset records each subdataset's state *by commit hash*, and `datalad run` provenance lives in those commits. Verified on a super+sub pair where the superdataset had recorded `code@S_late`:

```
after `git rebase` in the subdataset:   S_late on any branch? ''      ancestor of tip? NO
after `git merge`  in the subdataset:   S_late ancestor of tip? yes
```

So rebasing a subdataset leaves the superdataset's own history naming a commit no branch holds — alive in the reflog until it is garbage-collected, and absent from any push of that subdataset. The superdataset's recorded state stops being reproducible. A merge commit is a cosmetic cost; that is not.

A `--strategy merge|rebase` flag plus `--autostash` remains a possible user-facing option, and would be defensible for the superdataset (nothing records *its* commits) but not for subdatasets. Not implemented: it doubles the transport paths for a choice whose only upside is linear history.

**Pre-flight checks collisions, not cleanliness.** Refusing on any dirty file would block the workflow the command exists for (develop in main while the worktree runs). Instead `collisions()` intersects `incoming_paths()` with `dirty_paths()` and refuses only on overlap, naming the paths. `transferable_paths` skips paths dirty on *either* side for the same reason: stamping a locally-modified file would claim the reference's content.

**`behind` is not divergence.** A `code/` subdataset consumed in the worktree while development continues in main leaves the worktree strictly behind. That is "nothing to ship", not a conflict, and must skip rather than refuse — otherwise one subdataset aborts the whole update.

Direction follows from where you run it, because the data always lands in the tree you are standing in.

## List

Shows only datasets with worktrees beyond the main one. The main checkout comes first under the superdataset's branch, then one section per extra superdataset worktree.

**Prune before listing.** `list_nested_worktrees()` runs `git_worktree_prune()` on every dataset before reading its worktrees, so a directory removed outside the tool (`rm -rf`) does not linger as a stale entry.

**Grouping is by hierarchy, not by branch** (`group_by_branch()`). A worktree is filed under the superdataset worktree it sits beneath (innermost wins), with `annotation()` marking any dataset whose own branch differs. Subdatasets routinely end up on a detached HEAD — recording a subdataset commit that is no branch's tip does it — and grouping by their own branch put them in a separate `(detached)` section, splitting one hierarchy across two places (issue #13). A worktree under no known root keeps its own branch as its heading, and `branch_order()` sorts that residual `(detached)` heading last rather than first, where `(` would otherwise place it.

## Delete

1. **Resolve** which worktrees match the target (path or branch name).
2. **Preview** the directories that will be deleted and ask for confirmation (`--yes` to skip).
3. **Delete** deepest-first so children go before parents.
4. Optionally **delete the branch** (`git branch -d`, or `-D` with `--force`).

**Fallback deletion.** `git worktree remove` may fail on DataLad repos where `.git` is a directory instead of a gitlink file. It then falls back to `shutil.rmtree` + `git worktree prune`.

**The main working tree is never a delete target** (`is_main_worktree()`). `git worktree list` reports it alongside the linked ones, so a branch lookup landed on it, `git worktree remove` refused it, and the `rmtree` fallback then deleted the dataset outright — reported as `DELETED`. Annexed datasets survived by accident, because `rmtree` trips on mode-555 annex object directories; a text2git or no-annex dataset (what `code/` datasets are) was destroyed. Guarded in three places: the branch lookup skips it, path mode reports it as not-a-worktree, and `_git_worktree_remove` refuses it outright — the last being what makes the `rmtree` fallback safe at all. `add -f` replacement routes through `delete_nested_worktrees`, so the same guard covers it.

**Mainness is asked of git, per path** (`_worktree_kind()`). A linked worktree's git directory is `<common-dir>/worktrees/<name>`; a main working tree's git directory *is* the common directory. Comparing `rev-parse --git-dir` with `rev-parse --git-common-dir` therefore answers it for any repository at any nesting depth. The first version instead compared the candidate against `repo_path`, the dataset the command was resolved against — which holds only when that dataset *is* the main checkout. Run from inside a worktree, `repo_path` is the worktree, the real main checkout looks like just another linked entry, and both resolution modes deleted it (plus, in branch mode, a subdataset's `.git/modules/<name>` gitdir). Regression-tested from inside a worktree in both modes.

`"unknown"` — git finds no repository at the path — is deliberately *not* refused: a registration whose directory was removed by other means has to stay cleanable, and a path git cannot resolve is never the dataset the guard protects, since a dataset is a repository by definition.

Two cheaper heuristics were tried against real hierarchies and rejected:

- **`.git` is a directory ⇒ main checkout.** Tests the layout, not the property, and fails in *both* directions. DataLad keeps a subdataset's git dir in place, so `code/.git` is a directory (verified for `datalad create -d .` and for `datalad clone` + `get -n`) — but a subdataset added with plain `git submodule add` has a gitlink *file* there (`gitdir: ../.git/modules/code`), as does any repo made with `--separate-git-dir`, and discovery walks `.gitmodules` without caring which tool wrote the entry: main checkout read as worktree, i.e. the destructive direction. Meanwhile inside a worktree of an annexed dataset, git-annex replaces git's `.git` *file* with a **symlink** to `<main>/.git/worktrees/<name>` (so its object symlinks resolve), which `is_dir()` follows — worktree read as main checkout, and nothing is deletable. Mutation-tested: this heuristic fails four tests in `test_delete.py`.
- **First entry of `git worktree list --porcelain` is the main working tree.** True for the superdataset, but for a *submodule* git reports the main entry as `<super>/.git/modules/<name>`, so `<super>/code` never appears in the listing to be matched at all.

## Cross-cutting

- **Generator-based.** `create_nested_worktrees()`, `delete_nested_worktrees()` and the fetch driver yield one `WorktreeReport` per dataset, so both front-ends render progress in real time from the same code.
- **All git interaction** goes through `subprocess.run(..., capture_output=True, text=True)`. No gitpython dependency.
- **Failure isolation.** A failed subdataset never aborts the remaining ones. Only a superdataset failure is fatal.
- **Two front-ends, one core.** `cli.py` renders `WorktreeReport`s with ANSI colors; `dl_command.py` maps the same reports to DataLad result dicts, and degrades to stub classes when DataLad is absent.

## Worktrees are meant to be disposable

Create a worktree for one run, ship its results home, delete it. Do not keep one around and run in it repeatedly.

A long-lived worktree accumulates its own pipeline state, and that state goes stale in a way nothing here can repair. Snakemake records provenance per output under the untracked `.snakemake/metadata` — including the **text of the shell command** that produced it. Edit a rule afterwards, even cosmetically, and every output recorded in that worktree trips Snakemake's `code` rerun trigger.

The effect is counter-intuitive: a worktree that has *never* run is cleaner than one that has. With no records at all, Snakemake reports "missing provenance" and falls back to mtimes — which `add` and `fetch` keep correct. A worktree carrying 178 stale records once turned a 25-job dry run into 78 jobs, purely because a `-J 16` had been added to a rule since it last ran.

`.snakemake/` is gitignored, so it never travels into a worktree in the first place — deleting the worktree is what stops it going stale. If you do reuse a path, `add -f` replaces the worktree *and* its branch, so the result is equivalent to a fresh one.
