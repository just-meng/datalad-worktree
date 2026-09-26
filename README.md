# datalad-worktree

Nested git worktrees for [DataLad](https://www.datalad.org/) dataset hierarchies: create one for a run, ship its results home, dispose of it — or simply repeat.

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
worktree delete runs                    # dispose of it — or `worktree add -f` next time
```

`add` walks `.gitmodules` recursively and creates a worktree for each subdataset that is *installed*, i.e. only when its directory exists and holds a `.git`, and an uninstalled one is skipped without being descended into. 

```
/tmp/worktrees/runs/        <- superdataset worktree (branch: runs)
├── inputs/                 <- subdataset worktree
├── code/                   <- subdataset worktree
└── results/
```

Each is checked out on `runs`, created from the main checkout's current state if the branch does not exist yet.

## Highlights

- **mtimes are preserved on both legs** — creating a worktree and fetching from one — so staleness detection keeps working and finished work is not recomputed. A file inherits a timestamp only when its content is identical on both sides, matched by git's content hash rather than by filename.
- **`datalad containers-run` works inside the worktree**, via bind-mount configuration written per worktree, so the machine-specific paths stay out of the main checkout and out of history. One empty placeholder is committed on the worktree branch, which is what keeps a run record made there rerunnable elsewhere.
- **Results ship home without a clean tree.** Where your side has no commits of its own, `fetch` fast-forwards: nothing is rewritten, so unrelated work in progress is left alone. Where both sides have moved, it merges. It also runs the other way: from inside a worktree, `worktree fetch` brings new code and inputs in.
- **Safe by default.** Creation is all-or-nothing: if any dataset would fail, none are created. Deletion never touches the main working tree, never removes a directory git does not call a worktree, and refuses worktrees still holding unmerged work unless forced.
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
