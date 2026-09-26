# Contributing

Thanks for considering a contribution to `datalad-worktree`.

## Setup

```bash
git clone https://github.com/just-meng/datalad-worktree.git
cd datalad-worktree
uv sync --dev
```

## Running tests and lint

```bash
uv run pytest
uv run ruff check .
```

Worktrees of annexed repos and `datalad containers-run` don't work on Windows, so the full test suite needs Linux (or WSL). `tests/test_containers_run.py` additionally needs unprivileged user/mount namespaces and skips itself where those aren't available.

### Running lint before you commit

CI's `lint` job is `uv run ruff check .`, and finding out from CI costs a round trip. A hook that runs it locally lives in `.githooks/`; enable it with:

```bash
git config core.hooksPath .githooks
```

It checks the whole tree (ruff takes well under a second here), and `git commit --no-verify` bypasses it.

**If you commit with [jujutsu](https://jj-vcs.github.io/), that hook never fires** — jj runs no hooks at all, `pre-commit` included, and there is no hook mechanism in its config. Two jj-native equivalents; both are repo-local config, so they have to be set per clone:

```bash
# `jj lint` -- exactly what CI runs
jj config set --repo aliases.lint \
  '["util", "exec", "--", "uv", "run", "--dev", "ruff", "check", "."]'

# `jj fix` -- rewrite the commit with ruff's fixes applied
jj config set --repo fix.tools.ruff.command \
  '["uv", "run", "--dev", "ruff", "check", "--fix", "--quiet", "--stdin-filename=$path", "-"]'
jj config set --repo fix.tools.ruff.patterns '["glob:**/*.py"]'
```

`jj fix` works because ruff reads a file on stdin and writes the fixed version to stdout, which is the interface `jj fix` expects. It rewrites the working-copy commit by default, or `jj fix -s <rev>` a particular one — handy when CI goes red on a commit in the middle of a stack, since descendants are rebased for you.

## Submitting changes

Open a pull request against `master`. Keep commits focused -- one logical change per commit, with a message explaining *why*, not just *what*. `uv run ruff check .` should pass clean.

See [CLAUDE.md](CLAUDE.md) for an overview of the codebase's architecture and where to make common kinds of changes.
