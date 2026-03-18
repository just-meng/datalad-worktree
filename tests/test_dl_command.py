"""Tests for DataLad command interfaces."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

try:
    from datalad.distribution.dataset import Dataset
    from datalad.interface.base import Interface

    from datalad_worktree.dl_command import WorktreeAdd, WorktreeList, WorktreeRemove

    # Verify these are real DataLad Interface classes, not stubs
    HAS_DATALAD = issubclass(WorktreeAdd, Interface)
except (ImportError, TypeError):
    HAS_DATALAD = False

pytestmark = pytest.mark.skipif(
    not HAS_DATALAD,
    reason="DataLad not installed",
)


def _call_interface(cls, **kwargs):
    """Call a DataLad Interface class and collect results."""
    kwargs.setdefault("result_renderer", "disabled")
    return list(cls.__call__(**kwargs))


class TestWorktreeAdd:
    def test_basic_creation(self, superds: dict):
        wt_path = superds["wt_location"] / "dl-add"
        results = _call_interface(
            WorktreeAdd,
            worktree_path=str(wt_path),
            branch="feat/dl-add",
            dataset=str(superds["super"]),
        )
        ok_results = [r for r in results if r["status"] == "ok"]
        assert len(ok_results) == 4
        assert wt_path.is_dir()
        assert (wt_path / "sub-01" / ".git").exists()

    def test_dry_run(self, superds: dict):
        wt_path = superds["wt_location"] / "dl-dry"
        results = _call_interface(
            WorktreeAdd,
            worktree_path=str(wt_path),
            branch="feat/dl-dry",
            dataset=str(superds["super"]),
            dry_run=True,
        )
        dry_results = [r for r in results if r.get("dry_run")]
        assert len(dry_results) == 4
        assert not wt_path.exists()

    def test_result_dict_fields(self, superds: dict):
        """Verify result dicts contain expected fields."""
        wt_path = superds["wt_location"] / "dl-fields"
        results = _call_interface(
            WorktreeAdd,
            worktree_path=str(wt_path),
            branch="feat/dl-fields",
            dataset=str(superds["super"]),
        )
        for res in results:
            assert res["action"] == "worktree-add"
            assert "path" in res
            assert "status" in res
            assert "branch" in res
            assert "dataset_path" in res
            assert "worktree_root" in res
            assert res["type"] == "dataset"

    def test_new_branch_flag(self, superds: dict):
        """new_branch flag is set when a branch is newly created."""
        wt_path = superds["wt_location"] / "dl-newbr"
        results = _call_interface(
            WorktreeAdd,
            worktree_path=str(wt_path),
            branch="feat/dl-newbr",
            dataset=str(superds["super"]),
        )
        ok_results = [r for r in results if r["status"] == "ok"]
        assert all(r.get("new_branch") for r in ok_results)

    def test_skip_reason_for_uninstalled(self, superds: dict):
        """Uninstalled subdatasets produce skip_reason in result dict."""
        sub02 = superds["sub02"]
        git_entry = sub02 / ".git"
        if git_entry.is_file():
            git_entry.unlink()
        elif git_entry.is_dir():
            shutil.rmtree(git_entry)

        wt_path = superds["wt_location"] / "dl-skip"
        results = _call_interface(
            WorktreeAdd,
            worktree_path=str(wt_path),
            branch="feat/dl-skip",
            dataset=str(superds["super"]),
        )
        skipped = [r for r in results if r["status"] == "notneeded"]
        assert len(skipped) >= 1
        assert any(r.get("skip_reason") == "not installed" for r in skipped)

    def test_preflight_failure(self, superds: dict, tmp_path: Path):
        """Pre-flight failure produces error status results."""
        from tests.conftest import _git

        sub01 = superds["sub01"]
        conflict_wt = tmp_path / "conflict"
        _git(sub01, "worktree", "add", "-b", "conflict-dl", str(conflict_wt))

        wt_path = superds["wt_location"] / "dl-preflight"
        results = _call_interface(
            WorktreeAdd,
            worktree_path=str(wt_path),
            branch="conflict-dl",
            dataset=str(superds["super"]),
            on_failure="ignore",
        )
        errors = [r for r in results if r["status"] == "error"]
        assert len(errors) >= 1
        assert not wt_path.exists()


class TestWorktreeList:
    def test_no_extra_worktrees_returns_empty(self, superds: dict):
        """With no extra worktrees, list returns nothing (filtered in Interface)."""
        results = _call_interface(
            WorktreeList,
            dataset=str(superds["super"]),
        )
        # Each dataset has only 1 non-bare worktree, so none pass the >1 filter
        assert len(results) == 0

    def test_shows_extra_worktrees(self, superds: dict):
        """After creating worktrees, list returns them."""
        _call_interface(
            WorktreeAdd,
            worktree_path=str(superds["wt_location"] / "dl-list"),
            branch="feat/dl-list",
            dataset=str(superds["super"]),
        )

        results = _call_interface(
            WorktreeList,
            dataset=str(superds["super"]),
        )
        ok_results = [r for r in results if r["status"] == "ok"]
        # Each of 4 datasets has 2 worktrees (main + new) = 8 entries
        assert len(ok_results) == 8

    def test_result_dict_fields(self, superds: dict):
        """Verify list result dicts contain expected fields."""
        _call_interface(
            WorktreeAdd,
            worktree_path=str(superds["wt_location"] / "dl-list-fields"),
            branch="feat/dl-list-f",
            dataset=str(superds["super"]),
        )

        results = _call_interface(
            WorktreeList,
            dataset=str(superds["super"]),
        )
        for res in results:
            assert res["action"] == "worktree-list"
            assert "path" in res
            assert "branch" in res
            assert "dataset_path" in res
            assert "is_main" in res
            assert res["type"] == "dataset"

    def test_is_main_flag(self, superds: dict):
        """Main worktree should have is_main=True."""
        _call_interface(
            WorktreeAdd,
            worktree_path=str(superds["wt_location"] / "dl-list-main"),
            branch="feat/dl-list-m",
            dataset=str(superds["super"]),
        )

        results = _call_interface(
            WorktreeList,
            dataset=str(superds["super"]),
        )
        main_wts = [r for r in results if r.get("is_main")]
        non_main = [r for r in results if not r.get("is_main")]
        assert len(main_wts) == 4
        assert len(non_main) == 4


class TestWorktreeRemove:
    def _setup_worktrees(self, superds, name, branch):
        _call_interface(
            WorktreeAdd,
            worktree_path=str(superds["wt_location"] / name),
            branch=branch,
            dataset=str(superds["super"]),
        )

    def test_remove_by_branch(self, superds: dict):
        self._setup_worktrees(superds, "dl-rm", "feat/dl-rm")

        results = _call_interface(
            WorktreeRemove,
            target="feat/dl-rm",
            dataset=str(superds["super"]),
        )
        ok_results = [
            r for r in results
            if r["status"] == "ok" and not r.get("branch_deleted")
        ]
        assert len(ok_results) == 4
        assert not (superds["wt_location"] / "dl-rm").exists()

    def test_remove_by_path(self, superds: dict):
        wt_path = superds["wt_location"] / "dl-rm-path"
        self._setup_worktrees(superds, "dl-rm-path", "feat/dl-rm-path")

        results = _call_interface(
            WorktreeRemove,
            target=str(wt_path),
            dataset=str(superds["super"]),
        )
        ok_results = [
            r for r in results
            if r["status"] == "ok" and not r.get("branch_deleted")
        ]
        assert len(ok_results) == 4
        assert not wt_path.exists()

    def test_result_dict_fields(self, superds: dict):
        """Verify remove result dicts contain expected fields."""
        self._setup_worktrees(superds, "dl-rm-fields", "feat/dl-rm-f")

        results = _call_interface(
            WorktreeRemove,
            target="feat/dl-rm-f",
            dataset=str(superds["super"]),
        )
        for res in results:
            assert res["action"] == "worktree-remove"
            assert "path" in res
            assert "status" in res
            assert "branch" in res
            assert "dataset_path" in res
            assert res["type"] == "dataset"

    def test_delete_branch(self, superds: dict):
        """--delete-branch produces branch_deleted results."""
        self._setup_worktrees(superds, "dl-rm-delbr", "feat/dl-delbr")

        results = _call_interface(
            WorktreeRemove,
            target="feat/dl-delbr",
            dataset=str(superds["super"]),
            delete_branch=True,
        )
        branch_deleted = [r for r in results if r.get("branch_deleted")]
        assert len(branch_deleted) == 4

    def test_skip_nonexistent_branch(self, superds: dict):
        results = _call_interface(
            WorktreeRemove,
            target="nonexistent/dl-xyz",
            dataset=str(superds["super"]),
        )
        skipped = [r for r in results if r["status"] == "notneeded"]
        assert len(skipped) == 4
