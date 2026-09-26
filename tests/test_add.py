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
        if r.result in (WorktreeResult.CREATED, WorktreeResult.CREATED_NEW_BRANCH,
                        WorktreeResult.CREATED_RESET_BRANCH)
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


class TestReplacingWorktrees:
    """
    Replacement is the default (issue #28).

    Worktrees are ephemeral, and a worktree that is merged or behind holds
    nothing worth keeping except stale mtimes -- so refusing to overwrite it
    only made the caller type a flag. What still refuses is unmerged work.
    """

    def test_a_merged_worktree_is_replaced_without_any_flag(self, superds: dict):
        worktree = superds["wt_location"] / "wt"
        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs")

        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs")

        assert _all_ok(reports)
        assert _succeeded(reports)
        assert (worktree / ".git").exists()

    def test_a_stray_directory_is_still_refused(self, superds: dict):
        """Only paths git calls worktrees are replaced, flag or no flag."""
        worktree = superds["wt_location"] / "wt"
        worktree.mkdir(parents=True)
        (worktree / "someones-data.txt").write_text("not a worktree\n")

        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs")

        assert any("already exists" in r.message for r in _failed(reports))
        assert (worktree / "someones-data.txt").exists()

    def test_replacement_can_be_switched_off(self, superds: dict):
        """The library keeps the old refusal available; the CLI does not."""
        worktree = superds["wt_location"] / "wt"
        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs")

        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs2",
                              replace=False)

        assert any("already exists" in r.message for r in _failed(reports))

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
                    branch="runs")

        # recreated from the main checkout's HEAD, not from a stale branch ref
        assert _head(worktree) == _head(superds["super"])

    def test_unmerged_work_is_refused_and_nothing_changes(self, superds: dict):
        worktree = superds["wt_location"] / "wt"
        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs")
        unmerged = _commit_in(worktree)

        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs")

        failed = _failed(reports)
        assert failed
        assert any("unmerged work" in r.message for r in failed)
        assert not _succeeded(reports)
        # the worktree and its commit survive untouched
        assert _head(worktree) == unmerged

    def test_force_discards_it(self, superds: dict):
        worktree = superds["wt_location"] / "wt"
        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs")
        unmerged = _commit_in(worktree)

        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs",
                              discard_unmerged=True)

        assert _all_ok(reports)
        assert _head(worktree) != unmerged
        assert _head(worktree) == _head(superds["super"])

    def test_uncommitted_changes_are_discarded(self, superds: dict):
        """Decided deliberately: replacement refuses on commits, not on dirt."""
        worktree = superds["wt_location"] / "wt"
        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs")
        (worktree / "scratch.txt").write_text("uncommitted\n")
        _git(worktree, "add", "scratch.txt")

        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs")

        assert _all_ok(reports)
        assert not (worktree / "scratch.txt").exists()

    def test_dry_run_reports_the_replacement_without_doing_it(self, superds: dict):
        worktree = superds["wt_location"] / "wt"
        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs")
        before = _head(worktree)

        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs",
                              dry_run=True)

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
    def test_force_is_the_only_force_flag(self):
        """-F is gone (issue #28); an old invocation must fail, not change meaning."""
        import pytest as _pytest

        from datalad_worktree.cli import build_parser

        args = build_parser().parse_args(["add", "runs", "/tmp/wt"])
        assert args.force is False
        args = build_parser().parse_args(["add", "-f", "runs", "/tmp/wt"])
        assert args.force is True
        with _pytest.raises(SystemExit):
            build_parser().parse_args(["add", "-F", "runs", "/tmp/wt"])


# ── Leftover branches from an earlier run ────────────────────────────────────


def _branch_of(repo: Path) -> str:
    return _git(repo, "branch", "--show-current").stdout.strip()


class TestLeftoverBranches:
    """
    A branch already present in *some* datasets is a leftover, not a state.

    `worktree delete` keeps branches by default, so the next `add` on the same
    name used to check them out -- silently resurrecting the previous run's
    code and outputs in those datasets while the others started fresh. A
    branch present in *every* dataset is different: the superdataset commit
    names the subdataset commits that belong with it, so that is a recorded
    state somebody may want back, and it is left alone.
    """

    def _leftover_in_sub02(self, superds: dict, *, ahead: bool) -> Path:
        """Put a 'runs' branch in sub-02 only, optionally holding a commit."""
        sub02 = superds["sub02"]
        main = _branch_of(sub02)
        if ahead:
            _git(sub02, "checkout", "-q", "-b", "runs")
            _commit_in(sub02, "unfetched-result.txt")
            _git(sub02, "checkout", "-q", main)
        else:
            _git(sub02, "branch", "runs")
            _commit_in(sub02, "moved-on.txt")   # leaves 'runs' behind HEAD
        return sub02

    def test_leftover_is_reset_to_the_source_head(self, superds: dict):
        sub02 = self._leftover_in_sub02(superds, ahead=False)
        worktree = superds["wt_location"] / "wt"

        reports = _run_create(
            superds_path=superds["super"], worktree_path=worktree, branch="runs",
        )

        assert _all_ok(reports)
        reset = [r for r in reports
                 if r.result == WorktreeResult.CREATED_RESET_BRANCH]
        assert [r.dataset_path for r in reset] == ["sub-02"]
        # the whole point: the worktree starts where the checkout is now
        assert _head(worktree / "sub-02") == _head(sub02)
        assert (worktree / "sub-02" / "moved-on.txt").exists()

    def test_branch_in_every_dataset_is_checked_out_as_is(self, superds: dict):
        """A state recorded across the hierarchy is not a leftover."""
        datasets = [superds["super"], superds["sub01"],
                    superds["sub01_deriv"], superds["sub02"]]
        for ds in datasets:
            _git(ds, "branch", "runs")
        recorded = _head(superds["super"])
        _commit_in(superds["super"], "later-work.txt")   # main moves on
        worktree = superds["wt_location"] / "wt"

        reports = _run_create(
            superds_path=superds["super"], worktree_path=worktree, branch="runs",
        )

        assert _all_ok(reports)
        assert not [r for r in reports
                    if r.result == WorktreeResult.CREATED_RESET_BRANCH]
        assert _head(worktree) == recorded
        assert not (worktree / "later-work.txt").exists()

    def test_refuses_when_the_leftover_holds_unfetched_commits(self, superds: dict):
        """A finished run whose results were never fetched is not discarded."""
        self._leftover_in_sub02(superds, ahead=True)
        worktree = superds["wt_location"] / "wt"

        reports = _run_create(
            superds_path=superds["super"], worktree_path=worktree, branch="runs",
        )

        failed = _failed(reports)
        assert [r.dataset_path for r in failed] == ["sub-02"]
        assert "--force" in failed[0].message
        assert not worktree.exists()   # all-or-nothing

    def test_force_resets_it_anyway(self, superds: dict):
        self._leftover_in_sub02(superds, ahead=True)
        worktree = superds["wt_location"] / "wt"

        reports = _run_create(
            superds_path=superds["super"], worktree_path=worktree, branch="runs",
            discard_unmerged=True,
        )

        assert _all_ok(reports)
        assert any(r.result == WorktreeResult.CREATED_RESET_BRANCH
                   and r.dataset_path == "sub-02" for r in reports)
        assert not (worktree / "sub-02" / "unfetched-result.txt").exists()

    def test_dry_run_says_the_branch_would_be_reset(self, superds: dict):
        self._leftover_in_sub02(superds, ahead=False)
        worktree = superds["wt_location"] / "wt"

        reports = _run_create(
            superds_path=superds["super"], worktree_path=worktree, branch="runs",
            dry_run=True,
        )

        noted = [r for r in reports if "reset" in (r.message or "")]
        assert [r.dataset_path for r in noted] == ["sub-02"]
        assert not worktree.exists()

    def test_no_create_branch_never_resets(self, superds: dict):
        """--no-create-branch asks for the branch as it stands."""
        self._leftover_in_sub02(superds, ahead=False)
        worktree = superds["wt_location"] / "wt"

        reports = _run_create(
            superds_path=superds["super"], worktree_path=worktree, branch="runs",
            create_branch=False,
        )

        assert not [r for r in reports
                    if r.result == WorktreeResult.CREATED_RESET_BRANCH]
        assert any("no-create-branch" in (r.message or "") for r in _failed(reports))


# ── --follow-parent: the state the parent records, not the branch name ───────


class TestFollowParent:
    """
    Issue #29. A subdataset's state comes from the commit its parent records,
    so the worktrees reproduce one consistent state of the hierarchy instead of
    one branch name per dataset. With a commit, that state is a past one.
    """

    def test_subdataset_follows_the_recorded_commit(self, superds: dict):
        """Without the flag the subdataset is at its own tip, which the
        superdataset has not recorded -- a worktree born dirty."""
        sub02 = superds["sub02"]
        recorded = _head(sub02)
        _commit_in(sub02, "unrecorded.txt")          # ahead of the gitlink
        assert _head(sub02) != recorded

        plain = superds["wt_location"] / "plain"
        _run_create(superds_path=superds["super"], worktree_path=plain,
                    branch="a")
        assert "sub-02" in _git(plain, "status", "--short").stdout

        followed = superds["wt_location"] / "followed"
        reports = _run_create(superds_path=superds["super"],
                              worktree_path=followed, branch="b",
                              follow_parent=True)

        assert _all_ok(reports)
        assert _head(followed / "sub-02") == recorded
        assert _git(followed, "status", "--short").stdout == ""

    def test_nested_subdataset_resolves_through_its_own_parent(
        self, superds: dict,
    ):
        """
        The trap this exists to avoid: `<commit>:sub-01/derivatives` cannot see
        inside sub-01's tree, so the gitlink has to be read from sub-01 at the
        commit the superdataset records for *it*.
        """
        deriv = superds["sub01_deriv"]
        sub01 = superds["sub01"]
        _commit_in(deriv, "d1.txt")
        _git(sub01, "commit", "-qam", "record derivatives@d1")
        _git(superds["super"], "commit", "-qam", "record sub-01")
        recorded_deriv = _head(deriv)          # what sub-01 records *now*...
        # ... then derivatives and sub-01 move on, unrecorded by the superds
        _commit_in(deriv, "d2.txt")
        _git(sub01, "commit", "-qam", "record derivatives@d2")
        assert _head(deriv) != recorded_deriv

        worktree = superds["wt_location"] / "wt"
        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs",
                              follow_parent=True)

        assert _all_ok(reports)
        assert _head(worktree / "sub-01" / "derivatives") == recorded_deriv

    def test_at_a_commit_mirrors_that_state(self, superds: dict):
        sub02 = superds["sub02"]
        old_super = _head(superds["super"])
        old_sub = _head(sub02)
        _commit_in(sub02, "later.txt")
        _git(superds["super"], "commit", "-qam", "record sub-02 later")

        worktree = superds["wt_location"] / "wt"
        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="rerun",
                              follow_parent=True, at_commit=old_super)

        assert _all_ok(reports)
        assert _head(worktree) == old_super
        assert _head(worktree / "sub-02") == old_sub
        assert not (worktree / "sub-02" / "later.txt").exists()
        assert _git(worktree, "status", "--short").stdout == ""

    def test_a_dataset_added_after_the_commit_is_absent(self, superds: dict):
        """The commit defines the set: what was not there gets no worktree."""
        before = _head(superds["super"])
        _git(superds["super"], "-c", "protocol.file.allow=always",
             "submodule", "add", "-q", str(superds["sub02"]), "late")
        _git(superds["super"], "commit", "-qm", "add a late subdataset")

        worktree = superds["wt_location"] / "wt"
        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="rerun",
                              follow_parent=True, at_commit=before)

        assert _all_ok(reports)
        assert not (worktree / "late").exists()
        assert not [r for r in reports if r.dataset_path == "late"]

    def test_an_unresolvable_commit_refuses(self, superds: dict):
        worktree = superds["wt_location"] / "wt"
        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="rerun",
                              follow_parent=True, at_commit="deadbeef")

        assert any("cannot resolve commit" in r.message for r in _failed(reports))
        assert not worktree.exists()

    def test_a_recorded_commit_the_subdataset_lacks_refuses(self, superds: dict):
        """
        The realistic failure: the superdataset names a subdataset commit that
        was never fetched here. Half a hierarchy is worse than none.
        """
        bogus = "0" * 39 + "1"
        _git(superds["super"], "update-index", "--add",
             "--cacheinfo", f"160000,{bogus},sub-02")
        _git(superds["super"], "-c", "user.email=t@e.st", "-c", "user.name=t",
             "commit", "-qm", "record a commit nobody has")

        worktree = superds["wt_location"] / "wt"
        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="rerun",
                              follow_parent=True)

        failed = _failed(reports)
        assert [r.dataset_path for r in failed] == ["sub-02"]
        assert "not present in this subdataset" in failed[0].message
        assert not worktree.exists()

    def test_cli_flag_takes_an_optional_commit(self):
        from datalad_worktree.cli import build_parser

        p = build_parser()
        assert p.parse_args(["add", "b", "/tmp/wt"]).follow_parent is None
        assert p.parse_args(
            ["add", "b", "/tmp/wt", "--follow-parent"]).follow_parent == "HEAD"
        assert p.parse_args(
            ["add", "b", "/tmp/wt", "--follow-parent", "4f2a91c"]
        ).follow_parent == "4f2a91c"
