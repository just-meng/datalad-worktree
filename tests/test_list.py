"""Tests for the list command."""

from __future__ import annotations

import shutil
from pathlib import Path

from datalad_worktree.add import create_nested_worktrees
from datalad_worktree.core import WorktreeResult
from datalad_worktree.list_cmd import (
    DETACHED_LABEL,
    group_by_branch,
    list_nested_worktrees,
)


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


class TestListNestedWorktrees:

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

    def test_prunes_externally_deleted_worktree(self, superds: dict):
        """A worktree directory removed outside the tool (e.g. `rm -rf`
        instead of `worktree delete`) drops out of the listing instead of
        lingering as a stale entry."""
        wt_path = _create_worktrees(superds, "prune-test", "feat/prune")

        shutil.rmtree(wt_path)

        results = list_nested_worktrees(superds["super"])
        for ds_wt in results:
            non_bare = [w for w in ds_wt.worktrees if not w.bare]
            assert len(non_bare) == 1, (
                f"{ds_wt.dataset_path} still lists a stale worktree: {non_bare}"
            )


# ── Grouping (issue #13) ─────────────────────────────────────────────────────


class TestGroupByBranch:
    """
    A worktree is grouped by the hierarchy it sits in, not by its own branch.

    Reproduces the reported layout: a superdataset worktree on 'runs' whose
    `code` subdataset is on a detached HEAD. That `code` belongs under 'runs',
    annotated -- not in a separate '(detached)' section.
    """

    def _entries(self):
        main = Path("/ds")
        runs = Path("/wt/runs")
        return [
            (".", main, "master", True),
            ("code", main / "code", DETACHED_LABEL, True),
            (".", runs, "runs", False),
            ("code", runs / "code", DETACHED_LABEL, False),
            ("inputs/raw", runs / "inputs/raw", "runs", False),
        ]

    def test_detached_subdataset_joins_its_hierarchy(self):
        _main, branch_groups, _super = group_by_branch(self._entries())

        assert DETACHED_LABEL not in branch_groups
        assert sorted(branch_groups) == ["runs"]
        assert ("code", Path("/wt/runs/code"), DETACHED_LABEL) \
            in branch_groups["runs"]

    def test_main_group_keeps_the_main_checkout(self):
        main_group, _groups, super_branch = group_by_branch(self._entries())

        assert super_branch == "master"
        assert [p for p, _, _ in main_group] == [".", "code"]

    def test_innermost_root_wins_for_nested_worktrees(self):
        entries = [
            (".", Path("/ds"), "master", True),
            (".", Path("/wt/outer"), "outer", False),
            (".", Path("/wt/outer/inner"), "inner", False),
            ("code", Path("/wt/outer/inner/code"), DETACHED_LABEL, False),
        ]

        _main, branch_groups, _super = group_by_branch(entries)

        assert ("code", Path("/wt/outer/inner/code"), DETACHED_LABEL) \
            in branch_groups["inner"]
