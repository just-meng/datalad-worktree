"""
Delete command: delete nested worktrees by path or branch name.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from datalad_worktree.core import (
    WorktreeReport,
    WorktreeResult,
    git_worktree_list,
    git_worktree_prune,
    validate_superds,
)
from datalad_worktree.discovery import discover_subdatasets, is_git_repo

logger = logging.getLogger(__name__)


def _resolve_target(target: str) -> str:
    """Determine if target is a path or branch name."""
    return "path" if Path(target).exists() else "branch"


def _find_worktree_by_path(
    repo_path: Path, worktree_path: Path,
) -> Path | None:
    """Find a worktree entry matching the given path."""
    wt_resolved = worktree_path.resolve()
    for entry in git_worktree_list(repo_path):
        if entry.path.resolve() == wt_resolved:
            return entry.path
    return None


def _worktree_kind(path: Path) -> str:
    """
    Classify ``path`` as git sees it: ``"main"``, ``"linked"`` or ``"unknown"``.

    A linked worktree's git directory is ``<common-dir>/worktrees/<name>``,
    while a main working tree's git directory *is* the common directory. So
    comparing the two answers the question for any repository at any nesting
    depth, without assuming anything about the layout on disk.

    ``"unknown"`` means git found no repository at ``path`` -- it is missing,
    or a plain directory outside any repo.

    Two heuristics are deliberately not used. Comparing ``path`` against the
    repository the command was resolved against only works when that
    repository *is* the main checkout -- run from inside a worktree it makes
    the real main checkout look linked, which deleted it. And testing whether
    ``.git`` is a directory tests the layout rather than git's semantics, and
    is wrong in both directions: a subdataset added with plain ``git submodule
    add`` has a gitlink *file* there, exactly like a linked worktree (as does
    any repo made with ``--separate-git-dir``), while inside a worktree of an
    annexed dataset git-annex replaces that file with a *symlink* to
    ``<main>/.git/worktrees/<name>``, which ``is_dir()`` follows.
    """
    result = subprocess.run(
        [
            "git", "-C", str(path), "rev-parse",
            "--path-format=absolute", "--git-dir", "--git-common-dir",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "unknown"

    lines = result.stdout.splitlines()
    if len(lines) != 2:
        return "unknown"

    try:
        git_dir = Path(lines[0]).resolve()
        common_dir = Path(lines[1]).resolve()
    except OSError:
        return "unknown"

    return "main" if git_dir == common_dir else "linked"


def is_main_worktree(path: Path) -> bool:
    """
    Whether ``path`` is some repository's own checkout rather than a worktree.

    ``git worktree list`` reports the main working tree alongside the linked
    ones, so a branch lookup can land on it -- and deleting it means deleting
    the dataset.

    Only a confirmed main working tree is refused. ``"unknown"`` is not: a
    registration whose directory was removed by other means must stay
    cleanable, and a path git cannot resolve is never the dataset this guard
    protects, because a dataset is a repository by definition.
    """
    return _worktree_kind(path) == "main"


def _find_worktree_by_branch(
    repo_path: Path, branch: str,
) -> tuple[Path | None, str | None]:
    """
    Find a *linked* non-bare worktree checking out the given branch.

    The main working tree is skipped: it is the dataset, not a worktree of it,
    and `git worktree remove` refuses it -- after which the fallback below
    would have deleted it outright. Note this holds for *any* main working
    tree in the listing, not just ``repo_path``: run from inside a worktree,
    the checkout it was created from appears here like any other entry.
    """
    for entry in git_worktree_list(repo_path):
        if entry.branch != branch or entry.bare:
            continue
        if is_main_worktree(entry.path):
            continue
        return entry.path, entry.branch
    return None, None


def _git_worktree_remove(repo_path: Path, worktree_path: Path, force: bool = False) -> str:
    """
    Delete a worktree. Tries `git worktree remove` first; if that fails
    (e.g. .git is a directory instead of a gitlink file, common in DataLad),
    falls back to deleting the directory and pruning.
    Returns error message or empty string.
    """
    if is_main_worktree(worktree_path):
        # Never reachable through the normal resolution path, which filters
        # main working trees out -- but this is the guard that makes the
        # rmtree fallback below safe, so it stays.
        return f"refusing to delete {worktree_path}: it is the main working tree"

    cmd = ["git", "-C", str(repo_path), "worktree", "remove"]
    if force:
        cmd.append("--force")
    cmd.append(str(worktree_path))

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return ""

    # Fallback: delete directory manually and prune. Reached when `git
    # worktree remove` fails on a DataLad repo whose .git is a directory
    # rather than a gitlink file.
    wt = Path(worktree_path)
    if wt.exists():
        try:
            shutil.rmtree(wt)
        except OSError as e:
            return f"failed to delete {wt}: {e}"
        git_worktree_prune(repo_path)
        return ""

    return result.stderr.strip()


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


@dataclass
class DeleteTarget:
    """A resolved worktree target for deletion."""
    dataset_path: str   # relative path (or "." for superds)
    repo_path: Path     # path to the original repository
    worktree_path: Path # path to the worktree to delete
    branch: str         # branch name (or "" if unknown)


def resolve_delete_targets(
    superds_path: Path,
    target: str,
) -> tuple[list[DeleteTarget], list[WorktreeReport]]:
    """
    Resolve which worktrees would be deleted, without deleting anything.

    Returns
    -------
    targets : list[DeleteTarget]
        Worktrees that would be deleted, deepest-first.
    skipped : list[WorktreeReport]
        Datasets where no matching worktree was found.
    """
    superds_path = validate_superds(superds_path)
    mode = _resolve_target(target)

    if mode == "path":
        worktree_root = Path(target).resolve()
    else:
        worktree_root = None

    # Collect all datasets: super + installed subs
    all_datasets: list[tuple[str, Path]] = [(".", superds_path)]
    for subds in discover_subdatasets(superds_path):
        if subds.installed and is_git_repo(subds.abs_path):
            all_datasets.append((subds.rel_path, subds.abs_path))

    # Process deepest first for deletion
    all_datasets.reverse()

    targets: list[DeleteTarget] = []
    skipped: list[WorktreeReport] = []

    for dataset_path, repo_path in all_datasets:
        if mode == "path":
            if dataset_path == ".":
                wt_path = worktree_root
            else:
                wt_path = worktree_root / dataset_path

            if is_main_worktree(wt_path):
                skipped.append(WorktreeReport(
                    dataset_path=dataset_path,
                    source=repo_path,
                    destination=wt_path,
                    result=WorktreeResult.SKIPPED_NO_WORKTREE,
                    branch="",
                    message=f"{wt_path} is the main working tree, not a worktree",
                ))
                continue

            found = _find_worktree_by_path(repo_path, wt_path)
            if found is None:
                skipped.append(WorktreeReport(
                    dataset_path=dataset_path,
                    source=repo_path,
                    destination=wt_path,
                    result=WorktreeResult.SKIPPED_NO_WORKTREE,
                    branch="",
                    message=f"no worktree at {wt_path}",
                ))
                continue
            wt_path = found
            branch = ""
            for entry in git_worktree_list(repo_path):
                if entry.path.resolve() == wt_path.resolve():
                    branch = entry.branch or ""
                    break
            targets.append(DeleteTarget(dataset_path, repo_path, wt_path, branch))

        else:  # mode == "branch"
            wt_path, found_branch = _find_worktree_by_branch(repo_path, target)
            if wt_path is None:
                skipped.append(WorktreeReport(
                    dataset_path=dataset_path,
                    source=repo_path,
                    destination=repo_path,
                    result=WorktreeResult.SKIPPED_NO_WORKTREE,
                    branch=target,
                    message=f"no worktree on branch '{target}'",
                ))
                continue
            targets.append(DeleteTarget(dataset_path, repo_path, wt_path, target))

    return targets, skipped


def delete_nested_worktrees(
    superds_path: Path,
    target: str,
    delete_branch: bool = False,
    force: bool = False,
) -> Iterator[WorktreeReport]:
    """
    Delete nested worktrees by path or branch name.

    If ``target`` is an existing directory path, deletes the worktree at that
    path for each dataset. If ``target`` is a branch name, finds and deletes
    worktrees checking out that branch.

    Processes subdatasets deepest-first (reverse order) so children are
    deleted before parents.

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
    targets, skipped = resolve_delete_targets(superds_path, target)

    # Yield skipped reports
    yield from skipped

    # Delete each target
    for t in targets:
        err = _git_worktree_remove(t.repo_path, t.worktree_path, force=force)
        if err:
            yield WorktreeReport(
                dataset_path=t.dataset_path,
                source=t.repo_path,
                destination=t.worktree_path,
                result=WorktreeResult.FAILED,
                branch=t.branch,
                message=err,
            )
            continue

        yield WorktreeReport(
            dataset_path=t.dataset_path,
            source=t.repo_path,
            destination=t.worktree_path,
            result=WorktreeResult.DELETED,
            branch=t.branch,
        )

        # Delete branch if requested
        if delete_branch and t.branch:
            err = _git_branch_delete(t.repo_path, t.branch, force=force)
            if err:
                yield WorktreeReport(
                    dataset_path=t.dataset_path,
                    source=t.repo_path,
                    destination=t.worktree_path,
                    result=WorktreeResult.FAILED,
                    branch=t.branch,
                    message=f"branch delete failed: {err}",
                )
            else:
                yield WorktreeReport(
                    dataset_path=t.dataset_path,
                    source=t.repo_path,
                    destination=t.worktree_path,
                    result=WorktreeResult.DELETED_BRANCH,
                    branch=t.branch,
                )

    # Prune all repos
    all_repos = {t.repo_path for t in targets}
    for repo_path in all_repos:
        git_worktree_prune(repo_path)
