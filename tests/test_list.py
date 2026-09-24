"""Tests for the list command."""

from __future__ import annotations

import shutil
from pathlib import Path

from datalad_worktree.add import create_nested_worktrees
from datalad_worktree.core import WorktreeResult
from datalad_worktree.list_cmd import (
    DETACHED_LABEL,
    annotation,
    branch_order,
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


class TestAnnotation:
    def test_agreeing_branch_gets_none(self):
        assert annotation("runs", "runs") == ""

    def test_differing_branch_is_parenthesised(self):
        assert annotation("other", "runs") == " (other)"

    def test_detached_is_not_double_parenthesised(self):
        """It produced ((detached)) before."""
        assert annotation(DETACHED_LABEL, "runs") == " (detached)"


class TestBranchOrder:
    def test_named_branches_sort_alphabetically(self):
        assert branch_order({"runs": [], "aaa": [], "zzz": []}) == \
            ["aaa", "runs", "zzz"]

    def test_detached_sorts_last_not_first(self):
        order = branch_order({"runs": [], DETACHED_LABEL: [], "aaa": []})
        assert order[-1] == DETACHED_LABEL


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

    def test_a_worktree_under_no_known_root_keeps_its_own_heading(self):
        """Hand-made subdataset worktree with no superdataset worktree above it."""
        entries = [
            (".", Path("/ds"), "master", True),
            ("code", Path("/elsewhere/code"), "experiment", False),
        ]

        _main, branch_groups, _super = group_by_branch(entries)

        assert sorted(branch_groups) == ["experiment"]

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
