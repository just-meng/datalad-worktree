"""
Shared types, validation, and git helpers for datalad-worktree.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from datalad_worktree.discovery import is_git_repo

logger = logging.getLogger(__name__)


# ── Result types ─────────────────────────────────────────────────────────────


class WorktreeResult(Enum):
    """Outcome of a single worktree operation."""
    STARTING = auto()              # about to create/remove (progress indicator)
    CREATED = auto()
    CREATED_NEW_BRANCH = auto()
    SKIPPED_NOT_INSTALLED = auto()
    SKIPPED_NOT_GIT_REPO = auto()
    SKIPPED_DRY_RUN = auto()
    SKIPPED_NO_WORKTREE = auto()   # remove: no worktree found at path/branch
    SKIPPED_CONTAINER = auto()     # add: container left unconfigured (see message)
    CONFIGURED = auto()            # add: container bind-mount config written
    REMOVED = auto()
    REMOVED_BRANCH = auto()
    FAILED = auto()


SKIPPED_RESULTS = frozenset({
    WorktreeResult.SKIPPED_NOT_INSTALLED,
    WorktreeResult.SKIPPED_NOT_GIT_REPO,
    WorktreeResult.SKIPPED_DRY_RUN,
    WorktreeResult.SKIPPED_NO_WORKTREE,
    WorktreeResult.SKIPPED_CONTAINER,
})


@dataclass
class WorktreeReport:
    """Report for a single worktree operation."""
    dataset_path: str  # relative path (or "." for superds)
    source: Path
    destination: Path
    result: WorktreeResult
    branch: str
    message: str = ""


# ── Validation ───────────────────────────────────────────────────────────────


def validate_superds(path: Path) -> Path:
    """
    Validate that the given path is the root of a git repository.

    Returns the resolved absolute path.

    Raises
    ------
    ValueError
        If the path is not a git repo root.
    """
    path = path.resolve()

    if not is_git_repo(path):
        raise ValueError(f"Not a git repository: {path}")

    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    toplevel = Path(result.stdout.strip()).resolve()
    if toplevel != path:
        raise ValueError(
            f"Not at repository root.\n"
            f"  Given path: {path}\n"
            f"  Repo root:  {toplevel}"
        )

    return path


# ── Git helpers ──────────────────────────────────────────────────────────────


def git_branch_exists(repo_path: Path, branch: str) -> bool:
    """Check whether a branch exists in the given repository."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", branch],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


@dataclass
class GitWorktreeEntry:
    """A single entry from `git worktree list --porcelain`."""
    path: Path
    commit: str
    branch: str | None  # None if detached HEAD
    bare: bool = False


def git_worktree_list(repo_path: Path) -> list[GitWorktreeEntry]:
    """Parse `git worktree list --porcelain` for a repository."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    entries: list[GitWorktreeEntry] = []
    current: dict = {}

    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if current:
                entries.append(_parse_worktree_entry(current))
            current = {"path": line[len("worktree "):]}
        elif line.startswith("HEAD "):
            current["commit"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            # "branch refs/heads/main" -> "main"
            ref = line[len("branch "):]
            if ref.startswith("refs/heads/"):
                current["branch"] = ref[len("refs/heads/"):]
            else:
                current["branch"] = ref
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True

    if current:
        entries.append(_parse_worktree_entry(current))

    return entries


def _parse_worktree_entry(data: dict) -> GitWorktreeEntry:
    return GitWorktreeEntry(
        path=Path(data["path"]),
        commit=data.get("commit", ""),
        branch=data.get("branch"),
        bare=data.get("bare", False),
    )


def git_branch_checked_out_at(repo_path: Path, branch: str) -> Path | None:
    """
    If ``branch`` is checked out in any worktree of ``repo_path``,
    return that worktree's path. Otherwise return None.
    """
    for entry in git_worktree_list(repo_path):
        if entry.branch == branch and not entry.bare:
            return entry.path
    return None
