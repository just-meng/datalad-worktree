"""
List command: show all worktrees for a dataset hierarchy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from datalad_worktree.core import (
    GitWorktreeEntry,
    git_worktree_list,
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

    Returns
    -------
    list[DatasetWorktrees]
        One entry per dataset, each containing its worktree list.
    """
    superds_path = validate_superds(superds_path)

    results: list[DatasetWorktrees] = []

    # Superdataset
    results.append(DatasetWorktrees(
        dataset_path=".",
        source=superds_path,
        worktrees=git_worktree_list(superds_path),
    ))

    # Subdatasets
    for subds in discover_subdatasets(superds_path):
        if not subds.installed or not is_git_repo(subds.abs_path):
            continue
        results.append(DatasetWorktrees(
            dataset_path=subds.rel_path,
            source=subds.abs_path,
            worktrees=git_worktree_list(subds.abs_path),
        ))

    return results
