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

## Submitting changes

Open a pull request against `master`. Keep commits focused -- one logical change per commit, with a message explaining *why*, not just *what*. `uv run ruff check .` should pass clean.

See [CLAUDE.md](CLAUDE.md) for an overview of the codebase's architecture and where to make common kinds of changes.
