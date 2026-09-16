"""
List command: show all worktrees for a dataset hierarchy.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from datalad_worktree.core import (
    GitWorktreeEntry,
    git_worktree_list,
    git_worktree_prune,
    validate_superds,
)
from datalad_worktree.discovery import discover_subdatasets, is_git_repo


@dataclass
class DatasetWorktrees:
    """All worktrees for a single dataset in the hierarchy."""
    dataset_path: str  # relative path (or "." for superds)
    source: Path
    worktrees: list[GitWorktreeEntry]


def list_nested_worktrees(
    superds_path: Path,
) -> list[DatasetWorktrees]:
    """
    List all worktrees for the superdataset and all installed subdatasets.

    Prunes each dataset first, so a worktree directory removed some other
    way (e.g. ``rm -rf`` instead of ``worktree delete``) drops out of the
    listing instead of lingering as a stale entry.

    Returns
    -------
    list[DatasetWorktrees]
        One entry per dataset, each containing its worktree list.
    """
    superds_path = validate_superds(superds_path)

    results: list[DatasetWorktrees] = []

    # Superdataset
    git_worktree_prune(superds_path)
    results.append(DatasetWorktrees(
        dataset_path=".",
        source=superds_path,
        worktrees=git_worktree_list(superds_path),
    ))

    # Subdatasets
    for subds in discover_subdatasets(superds_path):
        if not subds.installed or not is_git_repo(subds.abs_path):
            continue
        git_worktree_prune(subds.abs_path)
        results.append(DatasetWorktrees(
            dataset_path=subds.rel_path,
            source=subds.abs_path,
            worktrees=git_worktree_list(subds.abs_path),
        ))

    return results


# (dataset_path, worktree_path, branch, is_main)
WorktreeEntry = tuple[str, Path, str, bool]
MainGroup = list[tuple[str, Path, str]]
BranchGroups = dict[str, list[tuple[str, Path]]]


def group_by_branch(
    entries: Iterable[WorktreeEntry],
) -> tuple[MainGroup, BranchGroups, str | None]:
    """
    Group worktree entries for ``list`` output.

    Returns each dataset's main worktree, its extra worktrees grouped by
    branch, and the superdataset's branch (or None if it has no worktree).
    """
    main_group: MainGroup = []
    branch_groups: BranchGroups = defaultdict(list)
    super_branch: str | None = None

    for dataset_path, worktree_path, branch, is_main in entries:
        if is_main:
            main_group.append((dataset_path, worktree_path, branch))
            if dataset_path == ".":
                super_branch = branch
        else:
            branch_groups[branch].append((dataset_path, worktree_path))

    return main_group, branch_groups, super_branch


def column_width(main_group: MainGroup, branch_groups: BranchGroups) -> int:
    """Column width to align the dataset-path label in ``list`` output."""
    paths = [p for p, _, _ in main_group] + [
        p for entries in branch_groups.values() for p, _ in entries
    ]
    return max(len(p) for p in paths) + 2 if paths else 20
