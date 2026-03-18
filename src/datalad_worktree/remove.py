"""
Remove command: remove nested worktrees by path or branch name.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

from datalad_worktree.core import (
    WorktreeReport,
    WorktreeResult,
    git_worktree_list,
    validate_superds,
)
from datalad_worktree.discovery import discover_subdatasets, is_git_repo

logger = logging.getLogger(__name__)


def _resolve_target(target: str) -> str:
    """Determine if target is a path or branch name."""
    p = Path(target)
    if p.is_absolute() and p.exists():
        return "path"
    # Could be a relative path that exists
    if p.exists():
        return "path"
    return "branch"


def _find_worktree_by_path(
    repo_path: Path, worktree_path: Path,
) -> Path | None:
    """Find a worktree entry matching the given path."""
    wt_resolved = worktree_path.resolve()
    for entry in git_worktree_list(repo_path):
        if entry.path.resolve() == wt_resolved:
            return entry.path
    return None


def _find_worktree_by_branch(
    repo_path: Path, branch: str,
) -> tuple[Path | None, str | None]:
    """
    Find a non-bare worktree checking out the given branch.
    Returns (worktree_path, branch) or (None, None).
    """
    for entry in git_worktree_list(repo_path):
        if entry.branch == branch and not entry.bare:
            return entry.path, entry.branch
    return None, None


def _git_worktree_remove(repo_path: Path, worktree_path: Path, force: bool = False) -> str:
    """
    Remove a worktree. Tries `git worktree remove` first; if that fails
    (e.g. .git is a directory instead of a gitlink file, common in DataLad),
    falls back to deleting the directory and pruning.
    Returns error message or empty string.
    """
    cmd = ["git", "-C", str(repo_path), "worktree", "remove"]
    if force:
        cmd.append("--force")
    cmd.append(str(worktree_path))

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return ""

    # Fallback: remove directory manually and prune
    wt = Path(worktree_path)
    if wt.exists():
        try:
            shutil.rmtree(wt)
        except OSError as e:
            return f"failed to remove {wt}: {e}"
        _git_worktree_prune(repo_path)
        return ""

    return result.stderr.strip()


def _git_worktree_prune(repo_path: Path) -> None:
    """Run `git worktree prune`."""
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "prune"],
        capture_output=True,
        text=True,
    )


def _git_branch_delete(repo_path: Path, branch: str, force: bool = False) -> str:
    """
    Delete a branch. Uses -d (safe) by default, -D (force) if force=True.
    Returns error message or empty string.
    """
    flag = "-D" if force else "-d"
    result = subprocess.run(
        ["git", "-C", str(repo_path), "branch", flag, branch],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return result.stderr.strip()
    return ""


def remove_nested_worktrees(
    superds_path: Path,
    target: str,
    delete_branch: bool = False,
    force: bool = False,
) -> Iterator[WorktreeReport]:
    """
    Remove nested worktrees by path or branch name.

    If ``target`` is an existing directory path, removes the worktree at that
    path for each dataset. If ``target`` is a branch name, finds and removes
    worktrees checking out that branch.

    Processes subdatasets deepest-first (reverse order) so children are
    removed before parents.

    Parameters
    ----------
    superds_path : Path
        Path to the root of the superdataset.
    target : str
        Either a worktree path or a branch name.
    delete_branch : bool
        If True, also delete the branch (using safe ``git branch -d``).
    force : bool
        Pass ``--force`` to ``git worktree remove`` and use ``-D`` for
        branch deletion.

    Yields
    ------
    WorktreeReport
        One report per dataset processed.
    """
    superds_path = validate_superds(superds_path)
    mode = _resolve_target(target)

    if mode == "path":
        worktree_root = Path(target).resolve()
    else:
        worktree_root = None  # will be determined per-dataset

    # Collect all datasets: super + installed subs
    all_datasets: list[tuple[str, Path]] = [(".", superds_path)]
    for subds in discover_subdatasets(superds_path):
        if subds.installed and is_git_repo(subds.abs_path):
            all_datasets.append((subds.rel_path, subds.abs_path))

    # Process deepest first for removal
    all_datasets.reverse()

    branch_for_report = target if mode == "branch" else ""

    for dataset_path, repo_path in all_datasets:
        if mode == "path":
            if dataset_path == ".":
                wt_path = worktree_root
            else:
                wt_path = worktree_root / dataset_path

            found = _find_worktree_by_path(repo_path, wt_path)
            if found is None:
                yield WorktreeReport(
                    dataset_path=dataset_path,
                    source=repo_path,
                    destination=wt_path,
                    result=WorktreeResult.SKIPPED_NO_WORKTREE,
                    branch=branch_for_report,
                    message=f"no worktree at {wt_path}",
                )
                continue
            wt_path = found
            # Determine the branch for this worktree
            for entry in git_worktree_list(repo_path):
                if entry.path.resolve() == wt_path.resolve():
                    branch_for_report = entry.branch or ""
                    break

        else:  # mode == "branch"
            wt_path, found_branch = _find_worktree_by_branch(repo_path, target)
            if wt_path is None:
                yield WorktreeReport(
                    dataset_path=dataset_path,
                    source=repo_path,
                    destination=repo_path,  # no worktree path to show
                    result=WorktreeResult.SKIPPED_NO_WORKTREE,
                    branch=target,
                    message=f"no worktree on branch '{target}'",
                )
                continue
            branch_for_report = target

        # Remove the worktree
        err = _git_worktree_remove(repo_path, wt_path, force=force)
        if err:
            yield WorktreeReport(
                dataset_path=dataset_path,
                source=repo_path,
                destination=wt_path,
                result=WorktreeResult.FAILED,
                branch=branch_for_report,
                message=err,
            )
            continue

        yield WorktreeReport(
            dataset_path=dataset_path,
            source=repo_path,
            destination=wt_path,
            result=WorktreeResult.REMOVED,
            branch=branch_for_report,
        )

        # Delete branch if requested
        if delete_branch and branch_for_report:
            err = _git_branch_delete(repo_path, branch_for_report, force=force)
            if err:
                yield WorktreeReport(
                    dataset_path=dataset_path,
                    source=repo_path,
                    destination=wt_path,
                    result=WorktreeResult.FAILED,
                    branch=branch_for_report,
                    message=f"branch delete failed: {err}",
                )
            else:
                yield WorktreeReport(
                    dataset_path=dataset_path,
                    source=repo_path,
                    destination=wt_path,
                    result=WorktreeResult.REMOVED_BRANCH,
                    branch=branch_for_report,
                )

    # Prune all repos
    for _, repo_path in all_datasets:
        _git_worktree_prune(repo_path)
