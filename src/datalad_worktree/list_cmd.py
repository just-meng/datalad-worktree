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


# Stand-in branch name for a worktree with no branch checked out.
DETACHED_LABEL = "(detached)"

# (dataset_path, worktree_path, branch, is_main)
WorktreeEntry = tuple[str, Path, str, bool]
MainGroup = list[tuple[str, Path, str]]
BranchGroups = dict[str, list[tuple[str, Path, str]]]


def group_by_branch(
    entries: Iterable[WorktreeEntry],
) -> tuple[MainGroup, BranchGroups, str | None]:
    """
    Group worktree entries by the hierarchy each one sits in.

    A worktree is grouped by the *superdataset worktree it lives under*, not
    by its own branch. A subdataset checked out on a detached HEAD -- which is
    what ``git worktree add`` leaves behind when a superdataset worktree
    records a subdataset commit that is not any branch's tip -- therefore
    appears alongside its siblings and is annotated, the way the main group
    already handles a subdataset whose branch differs from the
    superdataset's. Listing it in a separate ``(detached)`` section instead
    split one hierarchy across two places.

    Returns each dataset's main worktree, the extra worktrees grouped by the
    branch of the superdataset worktree they belong to, and the
    superdataset's own branch (or None if it has no worktree).
    """
    collected = list(entries)

    # Superdataset worktrees define the groups.
    roots: dict[Path, str] = {
        worktree_path.resolve(): branch
        for dataset_path, worktree_path, branch, _is_main in collected
        if dataset_path == "."
    }
    main_root: Path | None = None
    super_branch: str | None = None
    for dataset_path, worktree_path, branch, is_main in collected:
        if dataset_path == "." and is_main:
            main_root = worktree_path.resolve()
            super_branch = branch

    def root_of(worktree_path: Path) -> Path | None:
        """The innermost superdataset worktree this path belongs to."""
        resolved = worktree_path.resolve()
        candidates = [
            root for root in roots
            if resolved == root or root in resolved.parents
        ]
        return max(candidates, key=lambda r: len(str(r))) if candidates else None

    main_group: MainGroup = []
    branch_groups: BranchGroups = defaultdict(list)
    for dataset_path, worktree_path, branch, _is_main in collected:
        root = root_of(worktree_path)
        if root is not None and root == main_root:
            main_group.append((dataset_path, worktree_path, branch))
        else:
            # A worktree under no known superdataset root -- someone made it
            # by hand -- has no hierarchy to join, so it keeps its own branch
            # as its heading.
            heading = roots[root] if root is not None else branch
            branch_groups[heading].append((dataset_path, worktree_path, branch))

    return main_group, branch_groups, super_branch


def branch_order(branch_groups: BranchGroups) -> list[str]:
    """
    Section order for ``list``: named branches alphabetically, detached last.

    A ``(detached)`` heading only survives grouping when a worktree sits under
    no known superdataset root, so it is rare -- but sorting the headings
    directly would put it first, since "(" precedes any letter.
    """
    named = sorted(b for b in branch_groups if b != DETACHED_LABEL)
    return named + ([DETACHED_LABEL] if DETACHED_LABEL in branch_groups else [])


def annotation(branch: str, group_branch: str | None) -> str:
    """
    Suffix marking a dataset whose branch differs from its group's.

    Returns "" when it agrees. ``DETACHED_LABEL`` is already parenthesised,
    so it is not wrapped again -- that produced ``((detached))``.
    """
    if branch == group_branch:
        return ""
    return f" {branch}" if branch.startswith("(") else f" ({branch})"


def column_width(main_group: MainGroup, branch_groups: BranchGroups) -> int:
    """Column width to align the dataset-path label in ``list`` output."""
    paths = [p for p, _, _ in main_group] + [
        p for entries in branch_groups.values() for p, _, _ in entries
    ]
    return max(len(p) for p in paths) + 2 if paths else 20
