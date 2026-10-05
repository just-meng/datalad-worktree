# CLAUDE.md

Guidance for coding agents working on this repository. This file holds rules and pointers, not explanations. Each fact lives in exactly one place, so read it there:

| What | Where |
|---|---|
| What the tool is for, install | [README.md](README.md) |
| What each command and flag does | [docs/cli.md](docs/cli.md) |
| **Why**, across commands: the premise, containers, mtimes | [docs/design.md](docs/design.md) |
| **Why** a command or module is shaped the way it is, with the evidence | its module and function docstrings |
| Setup, test and lint commands, jj hooks | [CONTRIBUTING.md](CONTRIBUTING.md) |
| What changed in each release | [CHANGELOG.md](CHANGELOG.md) |

## Before changing behaviour

- **Read design.md and the docstrings of the code you are changing first.** Much of the code looks simplifiable and isn't: the obvious alternative was usually tried and broke something real, and the docstring next to it records what. If your change contradicts design.md or such a docstring, raise that with the maintainer instead of quietly working around it.
- **Some guards are pinned by tests that fail when the guard is removed.** Don't weaken these to get a test passing:
  - the `mtimes.py` safety rules and their `tests/test_mtimes.py` guards;
  - `_worktree_kind()` in `delete.py` and its tests;
  - `test_fails_without_bindpaths`.
- **Ask for a design decision instead of picking one.** Changing a default, adding a flag, or changing what gets refused is the maintainer's call. Propose the options and trade-offs, then wait.

## Making a change

- **User-facing changes reach both front-ends.** `cli.py` (argparse) and `dl_command.py` (the DataLad `Interface` classes) each declare every flag. A new outcome is a `WorktreeResult`, rendered in `cli.py:_render_report()` and mapped in `dl_command.py`.
- **All git access goes through `subprocess.run(..., capture_output=True, text=True)`.** No gitpython, and no runtime dependencies beyond the standard library. DataLad stays optional.
- **Resolve datasets against each other with `is_git_repo_root()`, not `is_git_repo()`.** The latter walks up the tree and is true at an empty submodule mount point.
- **Style:** `from __future__ import annotations`, full type hints, `logger = logging.getLogger(__name__)` per module. `uv run --dev ruff check .` must be clean.

## Tests

- **Use real repositories, never mocked git.** Tests build DataLad hierarchies in `tmp_path`, so `datalad` is a dev dependency. The full suite takes about 7 minutes and needs Linux. While iterating, run only the affected module.
- **Every bug fix gets a regression test that fails without the fix.** Show it: revert the fix, watch the test fail, restore it, and say in the PR that you did.
- **Assert invariants, not pinned values.** That means orderings, equality between trees, and paths left untouched. The generators also yield container and mtime reports, so filter reports by result type instead of counting all of them.
- **Stamp and check mtimes with `follow_symlinks=False`.** Annexed files are symlinks, so the default touches the shared annex object instead and silently makes staleness tests vacuous.
- **Some tests need extra capabilities.** `tests/test_containers_run.py` needs `datalad-container` and unprivileged user and mount namespaces, and skips itself without them. Don't turn those skips into failures.

## Docs

- **Update docs in the same commit as the change:** behaviour in `docs/cli.md`, rationale with its evidence in the docstring of the code it explains, or in `docs/design.md` when it spans commands, and README only when a headline claim changes.
- **Never put rationale in this file.** Copies drift, so point to where the explanation lives instead.

## Commits and PRs

- **One concern per commit.** A refactor, a fix, a dependency change and a doc edit are separate commits.
- **Commit subjects use a prefix:** `BF:` (fix), `ENH:` (feature), `DOC:`, `CI:`, `NF:`, or `API:`. The body explains why, and references the issue (`Closes #N`).
- **History is linear.** The maintainer uses jj and stacks PRs; rebase, and never merge `master` into a branch.
