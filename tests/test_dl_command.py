"""Tests for DataLad command interfaces."""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    from datalad.interface.base import Interface

    from datalad_worktree.dl_command import WorktreeAdd, WorktreeDelete

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
        """Creating nested worktrees yields one 'ok' result dict per dataset,
        with the DataLad-specific fields set."""
        wt_path = superds["wt_location"] / "dl-add"
        results = _call_interface(
            WorktreeAdd,
            worktree_path=str(wt_path),
            branch="feat/dl-add",
            dataset=str(superds["super"]),
        )
        ok_results = [
            r for r in results
            if r["status"] == "ok" and not r.get("mtimes")
        ]
        assert len(ok_results) == 4
        assert wt_path.is_dir()
        assert (wt_path / "sub-01" / ".git").exists()

        for res in results:
            assert res["action"] == "worktree-add"
            assert "path" in res
            assert "branch" in res
            assert "dataset_path" in res
            assert "worktree_root" in res
            assert res["type"] == "dataset"

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


class TestWorktreeDelete:
    def _setup_worktrees(self, superds, name, branch):
        _call_interface(
            WorktreeAdd,
            worktree_path=str(superds["wt_location"] / name),
            branch=branch,
            dataset=str(superds["super"]),
        )

    def test_delete_by_branch(self, superds: dict):
        """Delete by branch, and check the result-dict shape while at it."""
        self._setup_worktrees(superds, "dl-rm", "feat/dl-rm")

        results = _call_interface(
            WorktreeDelete,
            target="feat/dl-rm",
            dataset=str(superds["super"]),
        )
        ok_results = [
            r for r in results
            if r["status"] == "ok" and not r.get("branch_deleted")
        ]
        assert len(ok_results) == 4
        # the branch goes by default
        assert len([r for r in results if r.get("branch_deleted")]) == 4
        assert not (superds["wt_location"] / "dl-rm").exists()

        for res in results:
            assert res["action"] == "worktree-delete"
            assert "path" in res
            assert "status" in res
            assert "branch" in res
            assert "dataset_path" in res
            assert res["type"] == "dataset"

