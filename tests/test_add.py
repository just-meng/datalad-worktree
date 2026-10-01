"""Tests for the add command."""

from __future__ import annotations

import shutil
from pathlib import Path


from datalad_worktree.add import (
    create_nested_worktrees,
)
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
        if r.result == WorktreeResult.CREATED
    ]


def _failed(reports: list[WorktreeReport]) -> list[WorktreeReport]:
    return [r for r in reports if r.result == WorktreeResult.FAILED]



class TestCreateNestedWorktrees:
    def test_dry_run_reports_what_the_real_run_refuses(self, superds: dict):
        """-n runs the pre-flight: it must not promise a worktree that `add` refuses."""
        _git(superds["sub02"], "worktree", "add", "-q", "-b", "taken",
             str(superds["wt_location"] / "elsewhere"))

        reports = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch="taken",
            dry_run=True,
        )

        failed = _failed(reports)
        assert [r.dataset_path for r in failed] == ["sub-02"]
        assert "already checked out" in failed[0].message
        assert not [r for r in reports if r.result == WorktreeResult.SKIPPED_DRY_RUN]

    def test_creates_a_worktree_per_dataset_all_on_the_branch(self, superds: dict):
        branch = "feat/test"
        reports = _run_create(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"] / "test-wt",
            branch=branch,
        )
        assert _all_ok(reports)
        assert len(_succeeded(reports)) == 4

        wt_root = superds["wt_location"] / "test-wt"
        for rel in (".", "sub-01", "sub-01/derivatives", "sub-02"):
            assert _branch_of(wt_root / rel) == branch, rel

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

# ── Replacing an existing worktree ───────────────────────────────────────────


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _commit_in(worktree: Path, name: str = "extra.txt") -> str:
    """Make a commit in a worktree that its main checkout does not have."""
    (worktree / name).write_text("work done here\n")
    _git(worktree, "add", name)
    _git(worktree, "commit", "-qm", f"add {name}")
    return _head(worktree)


class TestReplacingWorktrees:
    """
    Replacement is the default (issue #28).

    Worktrees are ephemeral, and a worktree that is merged or behind holds
    nothing worth keeping except stale mtimes -- so refusing to overwrite it
    only made the caller type a flag. What still refuses is unmerged work.
    """

    def test_a_stray_directory_is_still_refused(self, superds: dict):
        """Only paths git calls worktrees are replaced, flag or no flag."""
        worktree = superds["wt_location"] / "wt"
        worktree.mkdir(parents=True)
        (worktree / "someones-data.txt").write_text("not a worktree\n")

        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs")

        assert any("already exists" in r.message for r in _failed(reports))
        assert (worktree / "someones-data.txt").exists()

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

    def test_a_later_refusal_leaves_the_old_worktree_in_place(
        self, superds: dict,
    ):
        """All-or-nothing: no check may run after the old worktree is gone."""
        worktree = superds["wt_location"] / "wt"
        _run_create(superds_path=superds["super"], worktree_path=worktree,
                    branch="runs")
        before = _head(worktree)

        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs",
                              follow_parent=True, at_commit="deadbeef")

        assert any("cannot resolve commit" in r.message for r in _failed(reports))
        assert _head(worktree) == before
        assert _branch_of(worktree) == "runs"

    def test_a_failure_after_the_checks_rolls_back(self, superds: dict):
        """
        No pre-flight can foresee every git failure. One that slips through
        stops the run and removes what it created: no half hierarchy.
        """
        # A stale ref lock: a real git failure the pre-flight does not check.
        git_dir = Path(_git(superds["sub02"], "rev-parse", "--absolute-git-dir")
                       .stdout.strip())
        (git_dir / "refs" / "heads" / "runs.lock").write_text("")
        worktree = superds["wt_location"] / "wt"

        reports = _run_create(superds_path=superds["super"],
                              worktree_path=worktree, branch="runs")

        assert [r.dataset_path for r in _failed(reports)] == ["sub-02"]
        assert not worktree.exists()
        for repo in (superds["super"], superds["sub01"], superds["sub01_deriv"]):
            listed = _git(repo, "worktree", "list", "--porcelain").stdout
            assert str(worktree) not in listed, repo
            assert not _git(repo, "branch", "--list", "runs").stdout.strip(), repo

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


# ── Leftover branches from an earlier run ────────────────────────────────────


def _branch_of(repo: Path) -> str:
    return _git(repo, "branch", "--show-current").stdout.strip()


class TestExistingBranches:
    """
    An existing branch always starts from the checkout's HEAD.

    Checking it out where it sat resurrected an earlier run's code and outputs
    -- in some datasets only, when the branch was a leftover there, or in all
    of them, which contradicted a new worktree being a fresh start. Resuming
    a kept branch is explicit: --follow-parent <branch>, under a new name.
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
        # the whole point: the worktree starts where the checkout is now
        assert _head(worktree / "sub-02") == _head(sub02)
        assert (worktree / "sub-02" / "moved-on.txt").exists()

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

