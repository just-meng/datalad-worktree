"""Tests for the remove command."""

from __future__ import annotations

from pathlib import Path

from datalad_worktree.add import create_nested_worktrees
from datalad_worktree.core import WorktreeResult
from datalad_worktree.remove import remove_nested_worktrees
from tests.conftest import _git


def _create_worktrees(superds: dict, name: str, branch: str) -> Path:
    """Helper: create nested worktrees and return the root path."""
    wt_path = superds["wt_location"] / name
    reports = list(create_nested_worktrees(
        superds_path=superds["super"],
        worktree_path=wt_path,
        branch=branch,
    ))
    assert all(
        r.result in (WorktreeResult.CREATED, WorktreeResult.CREATED_NEW_BRANCH)
        for r in reports
    )
    return wt_path


class TestRemoveByPath:
    def test_removes_all_worktrees(self, superds: dict):
        wt_path = _create_worktrees(superds, "rm-test", "feat/rm")
        assert wt_path.exists()

        reports = list(remove_nested_worktrees(
            superds_path=superds["super"],
            target=str(wt_path),
        ))
        removed = [r for r in reports if r.result == WorktreeResult.REMOVED]
        assert len(removed) == 4  # super + 3 subs
        assert not wt_path.exists()

    def test_skips_missing_worktrees(self, superds: dict):
        """Removing a nonexistent path skips all datasets."""
        reports = list(remove_nested_worktrees(
            superds_path=superds["super"],
            target="/tmp/nonexistent-worktree-path-xyz",
        ))
        assert all(
            r.result == WorktreeResult.SKIPPED_NO_WORKTREE for r in reports
        )


class TestRemoveByBranch:
    def test_removes_by_branch(self, superds: dict):
        wt_path = _create_worktrees(superds, "rm-branch", "feat/rm-branch")
        assert wt_path.exists()

        reports = list(remove_nested_worktrees(
            superds_path=superds["super"],
            target="feat/rm-branch",
        ))
        removed = [r for r in reports if r.result == WorktreeResult.REMOVED]
        assert len(removed) == 4
        assert not wt_path.exists()

    def test_skips_nonexistent_branch(self, superds: dict):
        reports = list(remove_nested_worktrees(
            superds_path=superds["super"],
            target="nonexistent/branch/xyz",
        ))
        assert all(
            r.result == WorktreeResult.SKIPPED_NO_WORKTREE for r in reports
        )


class TestRemoveWithDeleteBranch:
    def test_deletes_branch(self, superds: dict):
        wt_path = _create_worktrees(superds, "rm-delbr", "feat/del-branch")

        reports = list(remove_nested_worktrees(
            superds_path=superds["super"],
            target="feat/del-branch",
            delete_branch=True,
        ))
        removed_branches = [
            r for r in reports if r.result == WorktreeResult.REMOVED_BRANCH
        ]
        assert len(removed_branches) == 4

        # Verify the branch is gone
        out = _git(superds["super"], "branch", "--list", "feat/del-branch")
        assert out.stdout.strip() == ""
