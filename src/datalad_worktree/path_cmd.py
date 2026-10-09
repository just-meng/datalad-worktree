"""
Path lookup: print where a branch's worktree is, for ``cd (worktree runs)``.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterator
from pathlib import Path

from datalad_worktree.core import git_worktree_list

logger = logging.getLogger(__name__)


def _toplevel(path: Path) -> Path | None:
    """The root of the working tree containing ``path``, or None."""
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def _enclosing_repos(start: Path) -> Iterator[Path]:
    """
    Yield the working-tree roots around ``start``, innermost first.

    Each step asks git from the parent of the previous root, so an empty
    submodule mount point resolves to the dataset that contains it, and a
    nested dataset's own root is never skipped.
    """
    path: Path | None = start.resolve()
    while path is not None:
        top = _toplevel(path)
        if top is None:
            return
        yield top
        path = top.parent if top.parent != top else None


def find_worktree_path(branch: str, start: Path) -> Path:
    """
    The worktree checking out ``branch``, in the innermost repo around ``start``.

    The lookup starts at the dataset you stand in, so from inside a
    subdataset ``cd (worktree runs)`` lands in that subdataset's worktree,
    not at the top of the hierarchy. A repo with no worktree on ``branch``
    (a subdataset left out of the worktree, say) is passed over for the one
    enclosing it.

    Unlike ``delete``'s branch lookup, the main checkout counts: printing
    a path discards nothing, and it is what makes ``cd (worktree master)``
    the way back from a worktree. A worktree whose directory is gone is
    passed over rather than pruned, as a lookup should not write.

    Raises
    ------
    ValueError
        If ``start`` is not inside a git repository, or no enclosing repo
        has a worktree on ``branch``.
    """
    searched: list[Path] = []
    for repo in _enclosing_repos(start):
        searched.append(repo)
        for entry in git_worktree_list(repo):
            if entry.branch == branch and not entry.bare and entry.path.is_dir():
                return entry.path
    if not searched:
        raise ValueError(f"Not inside a git repository: {start}")
    raise ValueError(
        f"no worktree on branch '{branch}' in "
        + ", ".join(str(p) for p in searched)
    )
