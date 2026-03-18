"""Tests for the remove command."""

from __future__ import annotations

import shutil
from pathlib import Path

from datalad_worktree.add import create_nested_worktrees
from datalad_worktree.core import WorktreeResult
from datalad_worktree.remove import (
    _git_worktree_remove,
    _resolve_target,
    remove_nested_worktrees,
)
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
        r.result in (
            WorktreeResult.CREATED, WorktreeResult.CREATED_NEW_BRANCH,
            WorktreeResult.STARTING,
        )
        for r in reports
    )
    return wt_path


class TestResolveTarget:
    def test_absolute_existing_path(self, tmp_path: Path):
        d = tmp_path / "some-dir"
        d.mkdir()
        assert _resolve_target(str(d)) == "path"

    def test_absolute_nonexistent_path(self):
        assert _resolve_target("/tmp/nonexistent-xyz-12345") == "branch"

    def test_relative_existing_path(self, tmp_path: Path, monkeypatch):
        d = tmp_path / "rel-target"
        d.mkdir()
        monkeypatch.chdir(tmp_path)
        assert _resolve_target("rel-target") == "path"

    def test_branch_name(self):
        assert _resolve_target("feat/my-feature") == "branch"

    def test_branch_like_path(self):
        """A string that looks like a path but doesn't exist is treated as branch."""
        assert _resolve_target("some/nested/thing") == "branch"


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

    def test_deepest_first_ordering(self, superds: dict):
        """Worktrees are removed deepest-first (children before parents)."""
        wt_path = _create_worktrees(superds, "rm-order", "feat/rm-order")

        reports = list(remove_nested_worktrees(
            superds_path=superds["super"],
            target=str(wt_path),
        ))
        removed_paths = [
            r.dataset_path for r in reports
            if r.result == WorktreeResult.REMOVED
        ]
        # sub-01/derivatives must come before sub-01, and both before "."
        assert removed_paths.index("sub-01/derivatives") < removed_paths.index("sub-01")
        assert removed_paths.index("sub-01") < removed_paths.index(".")
        assert removed_paths.index("sub-02") < removed_paths.index(".")


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


class TestRemoveWithForce:
    def test_force_removes_dirty_worktree(self, superds: dict):
        """--force removes worktrees even with uncommitted changes."""
        wt_path = _create_worktrees(superds, "rm-force", "feat/rm-force")

        # Make the worktree dirty (uncommitted changes)
        (wt_path / "dirty-file.txt").write_text("uncommitted\n")
        _git(wt_path, "add", "dirty-file.txt")

        # Without force, git worktree remove would refuse
        # With force, it should succeed
        reports = list(remove_nested_worktrees(
            superds_path=superds["super"],
            target="feat/rm-force",
            force=True,
        ))
        removed = [r for r in reports if r.result == WorktreeResult.REMOVED]
        assert len(removed) == 4
        assert not wt_path.exists()

    def test_force_delete_branch_unmerged(self, superds: dict):
        """--force with --delete-branch uses -D to delete unmerged branches."""
        wt_path = _create_worktrees(superds, "rm-force-br", "feat/force-del")

        # Make a commit on the branch so it's "unmerged" relative to main
        (wt_path / "new-file.txt").write_text("branch-only\n")
        _git(wt_path, "add", "new-file.txt")
        _git(wt_path, "commit", "-m", "branch-only commit")

        reports = list(remove_nested_worktrees(
            superds_path=superds["super"],
            target="feat/force-del",
            delete_branch=True,
            force=True,
        ))
        removed_branches = [
            r for r in reports if r.result == WorktreeResult.REMOVED_BRANCH
        ]
        # At least the super's branch should be force-deleted
        assert len(removed_branches) >= 1

        # Verify the branch is gone from the superdataset
        out = _git(superds["super"], "branch", "--list", "feat/force-del")
        assert out.stdout.strip() == ""


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


class TestRemoveFallback:
    def test_fallback_when_git_dir_is_directory(self, superds: dict):
        """When .git is a directory (not gitlink), git worktree remove fails.

        The fallback should delete the directory and prune instead.
        DataLad worktrees typically have .git as a directory already.
        """
        wt_path = _create_worktrees(superds, "rm-fallback", "feat/rm-fb")

        # Replace the .git gitlink file with a real .git directory
        # to simulate what DataLad sometimes does
        git_entry = wt_path / ".git"
        if git_entry.is_file():
            git_entry.unlink()
            git_entry.mkdir()
            (git_entry / "HEAD").write_text("ref: refs/heads/feat/rm-fb\n")
        elif git_entry.is_dir():
            # Already a directory (DataLad default) — this is the case we test
            pass

        # _git_worktree_remove should fall back to rmtree + prune
        err = _git_worktree_remove(superds["super"], wt_path)
        assert err == ""
        assert not wt_path.exists()

    def test_fallback_nonexistent_path_returns_error(self, superds: dict):
        """If the worktree path doesn't exist and git can't remove it, return error."""
        err = _git_worktree_remove(
            superds["super"],
            Path("/tmp/nonexistent-wt-fallback-xyz"),
        )
        # git worktree remove will fail, and the path doesn't exist for fallback
        assert err != ""
