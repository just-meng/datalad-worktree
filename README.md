# datalad-worktree

Nested [git worktrees](https://git-scm.com/docs/git-worktree) for [DataLad](https://www.datalad.org/) dataset hierarchies: create one for a run, ship its results home, dispose of it — or simply repeat.

## What it is for

DataLad records provenance, Snakemake automates pipelines. Combining the two gives you [an automated computational workflow with staleness detection and full provenance](https://blog.datalad.org/posts/snakemake-datalad-worktree/). A long run then wants a checkout of its own, so that development can continue while it computes — which is exactly what `git worktree` is for.

Except a DataLad superdataset is not one repository. It needs a worktree per dataset, wired together so submodule mount points line up, and two things break when you do that:

- **`datalad containers-run` cannot read annexed files in a worktree.** `.git` is a link into the main repository, so annex object symlinks resolve outside the worktree, where the container cannot see them — `FileNotFoundError` on every annexed input ([datalad-container#288](https://github.com/datalad/datalad-container/issues/288)).
- **A fresh worktree looks arbitrarily stale.** Git records content, not timestamps, so for a make-style pipeline the mtime *ordering* that **is** the up-to-date state is gone — and systematically inverted, since the superdataset is checked out before its subdatasets. Every `code/` file ends up newer than every output derived from it: "all your code changed". Bringing results back has the mirror problem — git moves only what it rewrites, so a run that reproduces byte-identical output produces no commit at all, nothing moves, and that output stays stale forever, rerunning on every invocation.

`datalad-worktree` fixes both, in one command over the whole hierarchy:

```bash
cd /data/my-project
worktree add runs /tmp/worktrees/runs   # one worktree per dataset, mtimes preserved,
                                        # containers configured to reach the annex

cd /tmp/worktrees/runs
snakemake code/Snakefile -c 1 -k        # the long run — keep developing in main meanwhile

cd /data/my-project
worktree fetch runs                     # results home, mtimes included
worktree delete runs                    # dispose of it — or overwrite with `worktree add`
                                        # in the same location next time
```

`add` creates a worktree for the superdataset and every *installed* subdataset, nested the same way; uninstalled ones are skipped:

```
/tmp/worktrees/runs/        <- superdataset worktree (branch: runs)
├── inputs/                 <- subdataset worktree
├── code/                   <- subdataset worktree
└── results/
```

Each is on branch `runs`, starting from that dataset's current state, even if `runs` already exists.

`fetch` brings the worktree's commits back into each dataset's main checkout, then copies their mtimes across. `delete` removes the worktree from every dataset along with its branch, and refuses when the worktree contains unfetched commits.

## Highlights

- **mtimes are preserved in both directions** — creating a worktree and fetching from one — so staleness detection keeps working and finished work is not recomputed. A file inherits a timestamp only when its content is identical on both sides, matched by git's content hash rather than by filename. Snakemake's untracked `.snakemake_timestamp` markers travel too, so an output marked up to date with `snakemake --touch` stays up to date.
- **`datalad containers-run` works inside the worktree**, via bind-mount configuration written per worktree, so the machine-specific paths stay out of the main checkout and out of history. One empty placeholder is committed on the worktree branch, which is what keeps a run record made there rerunnable elsewhere.
- **Reproduce any recorded state.** `worktree add [branch] [path] --follow-parent <commit-or-tag>` checks every dataset out exactly as that superdataset commit recorded it, including which subdatasets existed then — to rerun a `datalad run` record from a clean slate, say. Without a commit, subdatasets follow what the superdataset records now.
- **Results ship home without a clean tree.** Where your side has no commits of its own, `fetch` fast-forwards: nothing is rewritten, so unrelated work in progress is left alone. Where both sides have moved, it merges. It also runs the other way: from inside a worktree, `worktree fetch` brings new code and inputs in.
- **Safe by default.** Creation and deletion are all-or-nothing: if any dataset would fail, none is touched. Deletion never touches the main working tree or a directory git does not call a worktree; it refuses a worktree with uncommitted changes or unfetched commits, unless forced. Every command that changes something takes `--dry-run`.
- **No dependencies.** Python standard library plus `git`. DataLad itself is optional — it only adds the `datalad worktree-*` commands.

## Installation

As a DataLad extension:

```bash
uv tool install datalad \
  --with datalad-worktree@git+https://github.com/just-meng/datalad-worktree.git
```

As a standalone CLI tool:

```bash
git clone https://github.com/just-meng/datalad-worktree.git
cd datalad-worktree
uv sync         # --dev for the test suite
```

## Commands

```bash
worktree add <branch> <worktree-path>   # create nested worktrees
worktree fetch [target]                 # bring commits and mtimes into the checkout you stand in
worktree delete <target>                # remove worktrees, deepest-first
worktree list                           # show worktrees across the hierarchy (also the default)
```

Installed as a DataLad extension — that is, into the same environment as DataLad, as above — each one is also available as `datalad worktree-add`, `worktree-list`, `worktree-delete`, `worktree-fetch`. Installed standalone, only the `worktree` command exists.

Flags and behaviour: [docs/cli.md](docs/cli.md). Why it works the way it does: [docs/design.md](docs/design.md).

## Requirements

- Python >= 3.11
- git on PATH (>= 2.38 for `fetch` to merge diverged datasets rather than refuse)
- [DataLad](https://www.datalad.org/), optional

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
