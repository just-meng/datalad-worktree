"""Tests for the add command."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from datalad_worktree.add import (
    _git_worktree_add,
    _prepare_destination,
    create_nested_worktrees,
    existing_worktrees,
    unmerged_worktrees,
)
from datalad_worktree.discovery import discover_subdatasets
from datalad_worktree.core import WorktreeReport, WorktreeResult
from tests.conftest import _git


def _run_create(**kwargs) -> list[WorktreeReport]:
    """Run create_nested_worktrees and collect its reports, dropping progress markers."""
    return [
        r for r in create_nested_worktrees(**kwargs)
        if r.result != WorktreeResult.STARTING
    ]


def _all_ok(reports: list[WorktreeReport]) -> bool:
    return not any(r.result == WorktreeResult.FAILED for r in reports)


def _succeeded(reports: list[WorktreeReport]) -> list[WorktreeReport]:
    return [
        r for r in reports
        if r.result in (WorktreeResult.CREATED, WorktreeResult.CREATED_NEW_BRANCH)
    ]


def _failed(reports: list[WorktreeReport]) -> list[WorktreeReport]:
    return [r for r in reports if r.result == WorktreeResult.FAILED]


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
        reports = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch="test-branch",
            dry_run=True,
        )
        assert _all_ok(reports)
        assert all(r.result == WorktreeResult.SKIPPED_DRY_RUN for r in reports)
        assert not (superds["wt_location"] / "test-wt").exists()

    def test_creates_all_worktrees(self, superds: dict):
        reports = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch="feat/test",
        )
        assert _all_ok(reports)
        assert len(_succeeded(reports)) == 4

        wt_root = superds["wt_location"] / "test-wt"
        assert wt_root.is_dir()
        assert (wt_root / ".git").exists()
        assert (wt_root / "sub-01" / ".git").exists()
        assert (wt_root / "sub-02" / ".git").exists()
        assert (wt_root / "sub-01" / "derivatives" / ".git").exists()

    def test_worktree_branches_correct(self, superds: dict):
        branch = "feat/verify-branch"
        reports = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch=branch,
        )
        assert _all_ok(reports)

        wt_root = superds["wt_location"] / "test-wt"
        for subdir in [wt_root, wt_root / "sub-01", wt_root / "sub-02"]:
            out = _git(subdir, "branch", "--show-current")
            assert out.stdout.strip() == branch

    def test_existing_root_without_force_fails(self, superds: dict):
        wt_root = superds["wt_location"] / "test-wt"
        wt_root.mkdir(parents=True)
        (wt_root / "file.txt").write_text("block")

        reports = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch="feat/block",
        )
        assert not _all_ok(reports)
        failed = _failed(reports)
        assert failed[0].result == WorktreeResult.FAILED
        assert "already exists" in failed[0].message

    def test_no_create_branch(self, superds: dict):
        reports = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch="nonexistent/branch",
            create_branch=False,
        )
        assert not _all_ok(reports)
        assert "--no-create-branch" in _failed(reports)[0].message

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

        reports = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch="feat/skip",
        )
        assert _all_ok(reports)
        skipped = [r for r in reports if r.dataset_path == "sub-02"]
        assert skipped[0].result == WorktreeResult.SKIPPED_NOT_INSTALLED

    def test_preflight_blocks_branch_conflict(self, superds: dict, tmp_path: Path):
        """If a branch is already checked out, add aborts before creating anything."""
        # Create a worktree for sub-01 on branch 'conflict'
        sub01_path = superds["sub01"]
        conflict_wt = tmp_path / "conflict-wt"
        _git(sub01_path, "worktree", "add", "-b", "conflict", str(conflict_wt))

        reports = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch="conflict",
        )
        # Should fail without creating anything
        assert not _all_ok(reports)
        assert any("already checked out" in r.message for r in _failed(reports))
        # The worktree root should NOT have been created
        assert not (superds["wt_location"] / "test-wt").exists()


# ── Replacing an existing worktree ───────────────────────────────────────────


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _commit_in(worktree: Path, name: str = "extra.txt") -> str:
    """Make a commit in a worktree that its main checkout does not have."""
    (worktree / name).write_text("work done here\n")
    _git(worktree, "add", name)
    _git(worktree, "commit", "-qm", f"add {name}")
    return _head(worktree)


class TestExistingWorktrees:
    def test_finds_every_destination_git_knows(self, superds: dict):
        worktree = superds["wt_location"] / "wt"
        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs")

        found = existing_worktrees(
            superds["super"], worktree, discover_subdatasets(superds["super"]),
        )

        assert "." in [d for d, _, _ in found]
        assert len(found) > 1  # the superdataset plus its subdatasets

    def test_ignores_a_stray_directory(self, superds: dict, tmp_path: Path):
        """--force replaces worktrees, it must not delete arbitrary dirs."""
        stray = tmp_path / "not-a-worktree"
        stray.mkdir()
        (stray / "precious.txt").write_text("do not delete me\n")

        found = existing_worktrees(
            superds["super"], stray, discover_subdatasets(superds["super"]),
        )

        assert found == []


class TestForceReplacesWorktrees:
    def test_without_force_it_refuses(self, superds: dict):
        worktree = superds["wt_location"] / "wt"
        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs")

        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs2")

        assert any("already exists" in r.message for r in _failed(reports))

    def test_force_replaces_a_merged_worktree(self, superds: dict):
        worktree = superds["wt_location"] / "wt"
        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs")

        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs", force=True)

        assert _all_ok(reports)
        assert _succeeded(reports)
        assert (worktree / ".git").exists()

    def test_force_deletes_the_branch_so_the_recreate_starts_from_main(
        self, superds: dict,
    ):
        """Worktrees are disposable: a replaced one must not inherit old commits."""
        worktree = superds["wt_location"] / "wt"
        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs")
        # a commit that IS merged nowhere but lives only on the old branch is
        # unmerged, so merge it into the main checkout first
        stale = _commit_in(worktree)
        _git(superds["super"], "merge", "--ff-only", "runs")
        assert _head(superds["super"]) == stale

        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs", force=True)

        # recreated from the main checkout's HEAD, not from a stale branch ref
        assert _head(worktree) == _head(superds["super"])

    def test_force_refuses_unmerged_work_and_changes_nothing(self, superds: dict):
        worktree = superds["wt_location"] / "wt"
        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs")
        unmerged = _commit_in(worktree)

        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs", force=True)

        failed = _failed(reports)
        assert failed
        assert any("unmerged work" in r.message for r in failed)
        assert not _succeeded(reports)
        # the worktree and its commit survive untouched
        assert _head(worktree) == unmerged

    def test_force_unmerged_discards_it(self, superds: dict):
        worktree = superds["wt_location"] / "wt"
        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs")
        unmerged = _commit_in(worktree)

        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs",
                              force=True, force_unmerged=True)

        assert _all_ok(reports)
        assert _head(worktree) != unmerged
        assert _head(worktree) == _head(superds["super"])

    def test_uncommitted_changes_are_discarded(self, superds: dict):
        """Decided deliberately: -f refuses on unmerged commits, not dirt."""
        worktree = superds["wt_location"] / "wt"
        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs")
        (worktree / "scratch.txt").write_text("uncommitted\n")
        _git(worktree, "add", "scratch.txt")

        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs", force=True)

        assert _all_ok(reports)
        assert not (worktree / "scratch.txt").exists()

    def test_dry_run_reports_the_replacement_without_doing_it(self, superds: dict):
        worktree = superds["wt_location"] / "wt"
        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs")
        before = _head(worktree)

        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs",
                              force=True, dry_run=True)

        assert any("would replace" in r.message for r in reports)
        assert _head(worktree) == before

    def test_unmerged_worktrees_reports_the_detached_case(self, superds: dict):
        worktree = superds["wt_location"] / "wt"
        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs")
        _git(worktree, "checkout", "-q", "--detach")

        existing = existing_worktrees(
            superds["super"], worktree, discover_subdatasets(superds["super"]),
        )
        blocked = unmerged_worktrees([e for e in existing if e[0] == "."])

        assert blocked
        assert "detached HEAD" in blocked[0][1]


class TestForceCLI:
    def test_parser_accepts_both_force_flags(self):
        from datalad_worktree.cli import build_parser

        args = build_parser().parse_args(["add", "-F", "runs", "/tmp/wt"])
        assert args.force_unmerged is True
        args = build_parser().parse_args(["add", "-f", "runs", "/tmp/wt"])
        assert args.force is True
        assert args.force_unmerged is False
