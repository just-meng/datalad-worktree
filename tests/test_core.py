"""Tests for core types and git helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from datalad_worktree.core import (
    WorktreeCreateResult,
    WorktreeReport,
    WorktreeResult,
    git_branch_checked_out_at,
    git_branch_exists,
    git_worktree_list,
    validate_superds,
)
from tests.conftest import _git


class TestGitBranchExists:
    def test_existing_branch(self, datalad_ds: Path):
        assert git_branch_exists(datalad_ds, "HEAD") is True

    def test_nonexistent_branch(self, datalad_ds: Path):
        assert git_branch_exists(datalad_ds, "nonexistent-branch-xyz") is False


class TestValidateSuperds:
    def test_valid_dataset_root(self, datalad_ds: Path):
        result = validate_superds(datalad_ds)
        assert result == datalad_ds.resolve()

    def test_not_a_repo(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Not a git repository"):
            validate_superds(tmp_path)

    def test_subdirectory_of_dataset(self, datalad_ds: Path):
        subdir = datalad_ds / "subdir"
        subdir.mkdir()
        with pytest.raises(ValueError, match="Not at repository root"):
            validate_superds(subdir)


class TestGitWorktreeList:
    def test_lists_main_worktree(self, datalad_ds: Path):
        entries = git_worktree_list(datalad_ds)
        assert len(entries) >= 1
        assert entries[0].path.resolve() == datalad_ds.resolve()

    def test_lists_additional_worktree(self, datalad_ds: Path, tmp_path: Path):
        wt_path = tmp_path / "wt"
        _git(datalad_ds, "worktree", "add", "-b", "test-wt", str(wt_path))
        entries = git_worktree_list(datalad_ds)
        assert len(entries) == 2
        paths = [e.path.resolve() for e in entries]
        assert wt_path.resolve() in paths


class TestGitBranchCheckedOutAt:
    def test_branch_checked_out(self, datalad_ds: Path):
        # The main worktree has some branch checked out
        result = _git(datalad_ds, "branch", "--show-current")
        current_branch = result.stdout.strip()
        if current_branch:
            conflict = git_branch_checked_out_at(datalad_ds, current_branch)
            assert conflict is not None

    def test_branch_not_checked_out(self, datalad_ds: Path):
        _git(datalad_ds, "branch", "unused-branch")
        assert git_branch_checked_out_at(datalad_ds, "unused-branch") is None


class TestWorktreeCreateResult:
    def _make_report(self, result: WorktreeResult) -> WorktreeReport:
        return WorktreeReport(
            dataset_path="test",
            source=Path("/src"),
            destination=Path("/dst"),
            result=result,
            branch="main",
        )

    def test_succeeded(self):
        r = WorktreeCreateResult(worktree_root=Path("/wt"), branch="main")
        r.reports = [
            self._make_report(WorktreeResult.CREATED),
            self._make_report(WorktreeResult.CREATED_NEW_BRANCH),
            self._make_report(WorktreeResult.FAILED),
        ]
        assert len(r.succeeded) == 2

    def test_failed(self):
        r = WorktreeCreateResult(worktree_root=Path("/wt"), branch="main")
        r.reports = [
            self._make_report(WorktreeResult.CREATED),
            self._make_report(WorktreeResult.FAILED),
        ]
        assert len(r.failed) == 1

    def test_skipped(self):
        r = WorktreeCreateResult(worktree_root=Path("/wt"), branch="main")
        r.reports = [
            self._make_report(WorktreeResult.SKIPPED_DRY_RUN),
            self._make_report(WorktreeResult.SKIPPED_NOT_INSTALLED),
            self._make_report(WorktreeResult.CREATED),
        ]
        assert len(r.skipped) == 2

    def test_all_ok_true(self):
        r = WorktreeCreateResult(worktree_root=Path("/wt"), branch="main")
        r.reports = [self._make_report(WorktreeResult.CREATED)]
        assert r.all_ok is True

    def test_all_ok_false(self):
        r = WorktreeCreateResult(worktree_root=Path("/wt"), branch="main")
        r.reports = [self._make_report(WorktreeResult.FAILED)]
        assert r.all_ok is False

    def test_summary_contains_key_info(self):
        r = WorktreeCreateResult(worktree_root=Path("/wt"), branch="main")
        r.reports = [self._make_report(WorktreeResult.CREATED)]
        s = r.summary()
        assert "Succeeded:" in s
        assert "/wt" in s
        assert "main" in s
