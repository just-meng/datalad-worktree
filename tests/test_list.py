"""Tests for the list command."""

from __future__ import annotations

from pathlib import Path

from datalad_worktree.add import create_nested_worktrees
from datalad_worktree.core import WorktreeResult
from datalad_worktree.list_cmd import list_nested_worktrees


def _create_worktrees(superds: dict, name: str, branch: str) -> Path:
    """Helper: create nested worktrees and return the root path."""
    wt_path = superds["wt_location"] / name
    reports = list(create_nested_worktrees(
        superds_path=superds["super"],
        worktree_path=wt_path,
        branch=branch,
    ))
    assert all(
        r.result in (
            WorktreeResult.CREATED, WorktreeResult.CREATED_NEW_BRANCH,
            WorktreeResult.STARTING,
        )
        for r in reports
    )
    return wt_path


class TestListNestedWorktrees:
    def test_no_extra_worktrees(self, superds: dict):
        """With no extra worktrees, all datasets have only the main worktree."""
        results = list_nested_worktrees(superds["super"])
        # Should have entries for super + 3 subs
        assert len(results) == 4
        # Each should have exactly 1 worktree (the main one)
        for ds_wt in results:
            non_bare = [w for w in ds_wt.worktrees if not w.bare]
            assert len(non_bare) == 1

    def test_lists_extra_worktrees(self, superds: dict):
        """After creating worktrees, list shows them."""
        _create_worktrees(superds, "list-test", "feat/list")
        results = list_nested_worktrees(superds["super"])

        # Every dataset should now have 2 non-bare worktrees (main + new)
        for ds_wt in results:
            non_bare = [w for w in ds_wt.worktrees if not w.bare]
            assert len(non_bare) == 2, (
                f"{ds_wt.dataset_path} has {len(non_bare)} worktrees, expected 2"
            )

    def test_dataset_paths_correct(self, superds: dict):
        """dataset_path is '.' for super, relative paths for subs."""
        results = list_nested_worktrees(superds["super"])
        paths = [r.dataset_path for r in results]
        assert "." in paths
        assert "sub-01" in paths
        assert "sub-02" in paths
        assert "sub-01/derivatives" in paths

    def test_source_points_to_repo(self, superds: dict):
        """source should point to the original repo, not the worktree."""
        results = list_nested_worktrees(superds["super"])
        for ds_wt in results:
            assert ds_wt.source.exists()
            assert (ds_wt.source / ".git").exists()

    def test_worktree_branch_matches(self, superds: dict):
        """Created worktrees should report the correct branch."""
        _create_worktrees(superds, "list-branch", "feat/list-br")
        results = list_nested_worktrees(superds["super"])

        for ds_wt in results:
            non_bare = [w for w in ds_wt.worktrees if not w.bare]
            branches = [w.branch for w in non_bare]
            assert "feat/list-br" in branches, (
                f"{ds_wt.dataset_path}: expected 'feat/list-br' in {branches}"
            )

    def test_multiple_worktrees(self, superds: dict):
        """Creating two sets of worktrees shows both."""
        _create_worktrees(superds, "list-a", "feat/a")
        _create_worktrees(superds, "list-b", "feat/b")
        results = list_nested_worktrees(superds["super"])

        for ds_wt in results:
            non_bare = [w for w in ds_wt.worktrees if not w.bare]
            # main + feat/a + feat/b = 3
            assert len(non_bare) == 3, (
                f"{ds_wt.dataset_path} has {len(non_bare)} worktrees, expected 3"
            )
