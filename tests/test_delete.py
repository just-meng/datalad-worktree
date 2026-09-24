"""Tests for the delete command."""

from __future__ import annotations

from pathlib import Path

from datalad_worktree.add import create_nested_worktrees
from datalad_worktree.core import WorktreeResult
from datalad_worktree.delete import (
    _git_worktree_remove,
    _resolve_target,
    delete_nested_worktrees,
    is_main_worktree,
    resolve_delete_targets,
)
import pytest
from datalad.api import create
from datalad.distribution.dataset import Dataset

from tests.conftest import _git


def _create_worktrees(superds: dict, name: str, branch: str) -> Path:
    """Helper: create nested worktrees and return the root path."""
    wt_path = superds["wt_location"] / name
    reports = list(create_nested_worktrees(
        superds_path=superds["super"],
        worktree_path=wt_path,
        branch=branch,
    ))
    assert not [r for r in reports if r.result == WorktreeResult.FAILED]
    return wt_path


class TestResolveTarget:
    def test_existing_path(self, tmp_path: Path):
        d = tmp_path / "some-dir"
        d.mkdir()
        assert _resolve_target(str(d)) == "path"

    def test_nonexistent_path_is_branch(self):
        """A string that looks like a path but doesn't exist is treated as branch."""
        assert _resolve_target("feat/my-feature") == "branch"


class TestDeleteByPath:
    def test_deletes_all_worktrees(self, superds: dict):
        wt_path = _create_worktrees(superds, "rm-test", "feat/rm")
        assert wt_path.exists()

        reports = list(delete_nested_worktrees(
            superds_path=superds["super"],
            target=str(wt_path),
        ))
        deleted = [r for r in reports if r.result == WorktreeResult.DELETED]
        assert len(deleted) == 4  # super + 3 subs
        assert not wt_path.exists()

    def test_skips_missing_worktrees(self, superds: dict):
        """Deleting a nonexistent path skips all datasets."""
        reports = list(delete_nested_worktrees(
            superds_path=superds["super"],
            target="/tmp/nonexistent-worktree-path-xyz",
        ))
        assert all(
            r.result == WorktreeResult.SKIPPED_NO_WORKTREE for r in reports
        )

    def test_deepest_first_ordering(self, superds: dict):
        """Worktrees are deleted deepest-first (children before parents)."""
        wt_path = _create_worktrees(superds, "rm-order", "feat/rm-order")

        reports = list(delete_nested_worktrees(
            superds_path=superds["super"],
            target=str(wt_path),
        ))
        deleted_paths = [
            r.dataset_path for r in reports
            if r.result == WorktreeResult.DELETED
        ]
        # sub-01/derivatives must come before sub-01, and both before "."
        assert deleted_paths.index("sub-01/derivatives") < deleted_paths.index("sub-01")
        assert deleted_paths.index("sub-01") < deleted_paths.index(".")
        assert deleted_paths.index("sub-02") < deleted_paths.index(".")


class TestDeleteByBranch:
    def test_deletes_by_branch(self, superds: dict):
        wt_path = _create_worktrees(superds, "rm-branch", "feat/rm-branch")
        assert wt_path.exists()

        reports = list(delete_nested_worktrees(
            superds_path=superds["super"],
            target="feat/rm-branch",
        ))
        deleted = [r for r in reports if r.result == WorktreeResult.DELETED]
        assert len(deleted) == 4
        assert not wt_path.exists()

    def test_skips_nonexistent_branch(self, superds: dict):
        reports = list(delete_nested_worktrees(
            superds_path=superds["super"],
            target="nonexistent/branch/xyz",
        ))
        assert all(
            r.result == WorktreeResult.SKIPPED_NO_WORKTREE for r in reports
        )


class TestDeleteWithForce:
    def test_force_deletes_dirty_worktree(self, superds: dict):
        """--force deletes worktrees even with uncommitted changes."""
        wt_path = _create_worktrees(superds, "rm-force", "feat/rm-force")

        # Make the worktree dirty (uncommitted changes)
        (wt_path / "dirty-file.txt").write_text("uncommitted\n")
        _git(wt_path, "add", "dirty-file.txt")

        # Without force, git worktree remove would refuse
        # With force, it should succeed
        reports = list(delete_nested_worktrees(
            superds_path=superds["super"],
            target="feat/rm-force",
            force=True,
        ))
        deleted = [r for r in reports if r.result == WorktreeResult.DELETED]
        assert len(deleted) == 4
        assert not wt_path.exists()

    def test_force_delete_branch_unmerged(self, superds: dict):
        """--force with --delete-branch uses -D to delete unmerged branches."""
        wt_path = _create_worktrees(superds, "rm-force-br", "feat/force-del")

        # Make a commit on the branch so it's "unmerged" relative to main
        (wt_path / "new-file.txt").write_text("branch-only\n")
        _git(wt_path, "add", "new-file.txt")
        _git(wt_path, "commit", "-m", "branch-only commit")

        reports = list(delete_nested_worktrees(
            superds_path=superds["super"],
            target="feat/force-del",
            delete_branch=True,
            force=True,
        ))
        deleted_branches = [
            r for r in reports if r.result == WorktreeResult.DELETED_BRANCH
        ]
        # At least the super's branch should be force-deleted
        assert len(deleted_branches) >= 1

        # Verify the branch is gone from the superdataset
        out = _git(superds["super"], "branch", "--list", "feat/force-del")
        assert out.stdout.strip() == ""


class TestDeleteWithDeleteBranch:
    def test_deletes_branch(self, superds: dict):
        wt_path = _create_worktrees(superds, "rm-delbr", "feat/del-branch")

        reports = list(delete_nested_worktrees(
            superds_path=superds["super"],
            target="feat/del-branch",
            delete_branch=True,
        ))
        deleted_branches = [
            r for r in reports if r.result == WorktreeResult.DELETED_BRANCH
        ]
        assert len(deleted_branches) == 4
        assert not wt_path.exists()

        # Verify the branch is gone
        out = _git(superds["super"], "branch", "--list", "feat/del-branch")
        assert out.stdout.strip() == ""


class TestDeleteFallback:
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


# ── Never delete the main working tree ───────────────────────────────────────


@pytest.fixture()
def text2git_ds(tmp_path: Path) -> Path:
    """
    A dataset whose content lives in git, not the annex.

    Deliberately not the annexed fixture: a `shutil.rmtree` over an annexed
    dataset trips on mode-555 annex object directories and fails partway, so an
    annexed dataset survived this bug by accident. A text2git dataset -- what
    `datalad create -c text2git` and no-annex code datasets look like -- has no
    such directories and was deleted outright.
    """
    ds_path = tmp_path / "code-ds"
    create(path=str(ds_path), cfg_proc=["text2git"], result_renderer="disabled")
    (ds_path / "precious.txt").write_text("the whole dataset\n")
    Dataset(str(ds_path)).save(message="init", result_renderer="disabled")
    return ds_path


class TestMainWorktreeIsNeverDeleted:
    def test_is_main_worktree_identifies_the_checkout(self, text2git_ds: Path):
        assert is_main_worktree(text2git_ds, text2git_ds) is True
        assert is_main_worktree(text2git_ds, text2git_ds / "sub") is False

    def test_branch_lookup_skips_the_main_worktree(self, text2git_ds: Path):
        """`git worktree list` reports it, so the lookup must filter it out."""
        branch = _git(text2git_ds, "branch", "--show-current").stdout.strip()

        targets, skipped = resolve_delete_targets(text2git_ds, branch)

        assert targets == []
        assert skipped and skipped[0].result == WorktreeResult.SKIPPED_NO_WORKTREE

    def test_deleting_the_main_branch_leaves_the_dataset_intact(
        self, text2git_ds: Path,
    ):
        """The regression: this destroyed the dataset and reported DELETED."""
        branch = _git(text2git_ds, "branch", "--show-current").stdout.strip()

        reports = list(delete_nested_worktrees(
            superds_path=text2git_ds, target=branch,
        ))

        assert not [r for r in reports if r.result == WorktreeResult.DELETED]
        assert (text2git_ds / "precious.txt").exists()
        assert (text2git_ds / ".git").exists()

    def test_deleting_the_main_path_is_refused(self, text2git_ds: Path):
        reports = list(delete_nested_worktrees(
            superds_path=text2git_ds, target=str(text2git_ds),
        ))

        assert any("main working tree" in r.message for r in reports)
        assert (text2git_ds / "precious.txt").exists()

    def test_remove_helper_refuses_it_directly(self, text2git_ds: Path):
        """Defence in depth: the guard that makes the rmtree fallback safe."""
        err = _git_worktree_remove(text2git_ds, text2git_ds, force=True)

        assert "main working tree" in err
        assert (text2git_ds / "precious.txt").exists()

    def test_a_real_worktree_still_deletes(self, superds: dict):
        """The guard must not block what delete is for."""
        worktree = _create_worktrees(superds, "wt", "runs")

        reports = list(delete_nested_worktrees(
            superds_path=superds["super"], target="runs",
        ))

        assert any(r.result == WorktreeResult.DELETED for r in reports)
        assert not worktree.exists()
