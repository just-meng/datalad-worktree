"""Tests for the add command."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from datalad_worktree.add import (
    _git_worktree_add,
    _prepare_destination,
    create_nested_worktrees,
)
from datalad_worktree.core import (
    WorktreeCreateResult,
    WorktreeResult,
    collect_worktree_reports,
)
from tests.conftest import _git


def _run_create(**kwargs) -> WorktreeCreateResult:
    """Convenience wrapper: runs create_nested_worktrees and collects into a result."""
    worktree_path = kwargs["worktree_path"]
    branch = kwargs["branch"]
    return collect_worktree_reports(
        create_nested_worktrees(**kwargs),
        worktree_root=worktree_path.resolve(),
        branch=branch,
    )


class TestPrepareDestination:
    def test_nonexistent_path_creates_parent(self, tmp_path: Path):
        dest = tmp_path / "a" / "b" / "target"
        _prepare_destination(dest)
        assert dest.parent.exists()
        assert not dest.exists()

    def test_gitlink_file_removed(self, tmp_path: Path):
        dest = tmp_path / "subds"
        dest.write_text("gitdir: /some/path/.git/modules/subds")
        _prepare_destination(dest)
        assert not dest.exists()

    def test_non_gitlink_file_removed(self, tmp_path: Path):
        dest = tmp_path / "subds"
        dest.write_text("something else")
        _prepare_destination(dest)
        assert not dest.exists()

    def test_empty_dir_removed(self, tmp_path: Path):
        dest = tmp_path / "subds"
        dest.mkdir()
        _prepare_destination(dest)
        assert not dest.exists()

    def test_dir_with_only_git_file_removed(self, tmp_path: Path):
        dest = tmp_path / "subds"
        dest.mkdir()
        (dest / ".git").write_text("gitdir: /some/path")
        _prepare_destination(dest)
        assert not dest.exists()

    def test_dir_with_only_git_dir_removed(self, tmp_path: Path):
        dest = tmp_path / "subds"
        dest.mkdir()
        (dest / ".git").mkdir()
        _prepare_destination(dest)
        assert not dest.exists()

    def test_nonempty_dir_left_alone(self, tmp_path: Path):
        dest = tmp_path / "subds"
        dest.mkdir()
        (dest / "real_file.txt").write_text("important")
        _prepare_destination(dest)
        assert dest.exists()
        assert (dest / "real_file.txt").exists()


class TestGitWorktreeAdd:
    def test_create_new_branch(self, datalad_ds: Path, tmp_path: Path):
        dest = tmp_path / "wt"
        result, msg = _git_worktree_add(datalad_ds, dest, "new-branch")
        assert result == WorktreeResult.CREATED_NEW_BRANCH
        assert dest.exists()
        assert (dest / ".git").exists()

    def test_checkout_existing_branch(self, datalad_ds: Path, tmp_path: Path):
        _git(datalad_ds, "branch", "existing-branch")
        dest = tmp_path / "wt"
        result, msg = _git_worktree_add(datalad_ds, dest, "existing-branch")
        assert result == WorktreeResult.CREATED
        assert dest.exists()

    def test_no_create_branch_fails(self, datalad_ds: Path, tmp_path: Path):
        dest = tmp_path / "wt"
        result, msg = _git_worktree_add(
            datalad_ds, dest, "nonexistent", create_branch=False
        )
        assert result == WorktreeResult.FAILED
        assert "--no-create-branch" in msg


class TestCreateNestedWorktrees:
    def test_dry_run(self, superds: dict):
        result = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch="test-branch",
            dry_run=True,
        )
        assert result.all_ok
        assert all(
            r.result == WorktreeResult.SKIPPED_DRY_RUN for r in result.reports
        )
        assert not (superds["wt_location"] / "test-wt").exists()

    def test_creates_all_worktrees(self, superds: dict):
        result = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch="feat/test",
        )
        assert result.all_ok

        wt_root = superds["wt_location"] / "test-wt"
        assert wt_root.is_dir()
        assert (wt_root / ".git").exists()
        assert (wt_root / "sub-01" / ".git").exists()
        assert (wt_root / "sub-02" / ".git").exists()
        assert (wt_root / "sub-01" / "derivatives" / ".git").exists()

    def test_report_counts(self, superds: dict):
        result = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch="feat/counts",
        )
        assert len(result.reports) == 4
        assert len(result.succeeded) == 4
        assert len(result.failed) == 0

    def test_worktree_branches_correct(self, superds: dict):
        branch = "feat/verify-branch"
        result = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch=branch,
        )
        assert result.all_ok

        wt_root = superds["wt_location"] / "test-wt"
        for subdir in [wt_root, wt_root / "sub-01", wt_root / "sub-02"]:
            out = _git(subdir, "branch", "--show-current")
            assert out.stdout.strip() == branch

    def test_existing_root_without_force_fails(self, superds: dict):
        wt_root = superds["wt_location"] / "test-wt"
        wt_root.mkdir(parents=True)
        (wt_root / "file.txt").write_text("block")

        result = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch="feat/block",
        )
        assert not result.all_ok
        assert result.failed[0].result == WorktreeResult.FAILED
        assert "already exists" in result.failed[0].message

    def test_no_create_branch(self, superds: dict):
        result = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch="nonexistent/branch",
            create_branch=False,
        )
        assert not result.all_ok

    def test_not_a_repo_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Not a git repository"):
            list(create_nested_worktrees(
                superds_path=tmp_path,
                worktree_path=tmp_path / "wt" / "n",
                branch="b",
            ))

    def test_uninstalled_subdataset_skipped(self, superds: dict):
        """An uninstalled subdataset is skipped, not fatal."""
        git_entry = superds["sub02"] / ".git"
        if git_entry.is_file():
            git_entry.unlink()
        elif git_entry.is_dir():
            shutil.rmtree(git_entry)

        result = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch="feat/skip",
        )
        assert result.all_ok
        skipped = [r for r in result.reports if r.dataset_path == "sub-02"]
        assert skipped[0].result == WorktreeResult.SKIPPED_NOT_INSTALLED

    def test_preflight_blocks_branch_conflict(self, superds: dict, tmp_path: Path):
        """If a branch is already checked out, add aborts before creating anything."""
        # Create a worktree for sub-01 on branch 'conflict'
        sub01_path = superds["sub01"]
        conflict_wt = tmp_path / "conflict-wt"
        _git(sub01_path, "worktree", "add", "-b", "conflict", str(conflict_wt))

        result = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch="conflict",
        )
        # Should fail without creating anything
        assert not result.all_ok
        assert any("already checked out" in r.message for r in result.failed)
        # The worktree root should NOT have been created
        assert not (superds["wt_location"] / "test-wt").exists()
