"""Tests for fetching a worktree's results into the main checkout."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from datalad.api import create, install
from datalad.distribution.dataset import Dataset

from datalad_worktree.add import create_nested_worktrees
from datalad_worktree.cli import build_parser, main as main_cli
from datalad_worktree.core import WorktreeResult
from datalad_worktree.fetch import (
    MERGE_COMMIT_PREFIX,
    resolve_fetch_source,
    collisions,
    dataset_pairs,
    fast_forward_state,
    incoming_paths,
    merge_prediction,
    preflight,
    fetch_nested_worktrees,
)

# Snakemake's declared output is the *directory*, and the job writes two
# levels down inside it -- the layout in which git never moves the mtime
# that decides staleness.
DECLARED_OUTPUT = "derived/vis/ses-A"
OUTPUT_FILE = "derived/vis/ses-A/heatmaps/a.png"
INPUT_FILE = "inputs/scan.nwb"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True,
    )


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _looks_stale(base: Path) -> bool:
    """The question snakemake asks: is the declared output older than its input?"""
    return (os.lstat(base / DECLARED_OUTPUT).st_mtime_ns
            < os.lstat(base / INPUT_FILE).st_mtime_ns)


def _rewrite(path: Path, payload: bytes) -> None:
    """Replace an annexed file, which checks out as a read-only symlink."""
    path.unlink()
    path.write_bytes(payload)


def _touch(path: Path) -> None:
    """
    Set a path's mtime to now, without following it.

    ``follow_symlinks=False`` is not optional here: annexed files check out
    as symlinks into a shared object store, so the default would stamp the
    annex object and leave the symlink -- the thing whose mtime is actually
    read -- untouched.
    """
    os.utime(path, None, follow_symlinks=False)


@pytest.fixture()
def shipping_ds(tmp_path: Path) -> dict:
    """
    A superdataset with one subdataset holding the pipeline's outputs.

    ::

        super/
        ├── inputs/scan.nwb                        the input
        └── derived/                               subdataset
            └── vis/ses-A/heatmaps/a.png           the output

    The input is left *newer* than the declared output directory, which is
    the state that makes a make-style pipeline rerun the job.
    """
    origins = tmp_path / "origins"

    derived = Dataset(
        create(path=str(origins / "derived"), result_renderer="disabled").path
    )
    (derived.pathobj / "vis/ses-A/heatmaps").mkdir(parents=True)
    (derived.pathobj / "vis/ses-A/heatmaps/a.png").write_bytes(b"\x89PNG" + b"v1" * 50)
    derived.save(message="first output", result_renderer="disabled")

    superds = Dataset(
        create(path=str(origins / "super"), result_renderer="disabled").path
    )
    (superds.pathobj / "inputs").mkdir()
    (superds.pathobj / INPUT_FILE).write_bytes(b"nwb" * 50)
    superds.save(message="add input", result_renderer="disabled")

    install(
        dataset=superds, source=str(derived.path), path="derived",
        result_renderer="disabled",
    )
    superds.save(message="add derived subdataset", result_renderer="disabled")

    time.sleep(1.1)
    _touch(superds.pathobj / INPUT_FILE)

    worktree = tmp_path / "worktrees" / "runs"
    list(create_nested_worktrees(
        superds_path=superds.pathobj, worktree_path=worktree, branch="runs",
    ))
    return {"main": superds.pathobj, "wt": worktree}


def _run_in_worktree(shipping_ds: dict, *, identical: bool) -> None:
    """Regenerate the output in the worktree, as the pipeline would."""
    wt = shipping_ds["wt"]
    time.sleep(1.1)
    _rewrite(wt / OUTPUT_FILE, b"\x89PNG" + (b"v1" if identical else b"v2") * 50)
    Dataset(str(wt / "derived")).save(message="regenerate", result_renderer="disabled")
    Dataset(str(wt)).save(message="record", result_renderer="disabled")
    # The rule touches its own declared output, because even a local run
    # never moves the grandparent directory.
    _touch(wt / DECLARED_OUTPUT)


def _fetch(shipping_ds: dict, **kwargs) -> list:
    return list(fetch_nested_worktrees(
        main_path=shipping_ds["main"], worktree_path=shipping_ds["wt"], **kwargs,
    ))


# ── Building blocks ──────────────────────────────────────────────────────────


class TestDatasetPairs:
    def test_superdataset_first_then_subdatasets(self, shipping_ds: dict):
        pairs = dataset_pairs(shipping_ds["main"], shipping_ds["wt"])

        assert [p.dataset_path for p in pairs] == [".", "derived"]
        assert pairs[0].main == shipping_ds["main"]
        assert pairs[1].worktree == shipping_ds["wt"] / "derived"

    def test_skips_a_subdataset_absent_from_the_main_checkout(self, shipping_ds: dict):
        import shutil
        shutil.rmtree(shipping_ds["main"] / "derived")
        (shipping_ds["main"] / "derived").mkdir()

        pairs = dataset_pairs(shipping_ds["main"], shipping_ds["wt"])

        assert [p.dataset_path for p in pairs] == ["."]


class TestFastForwardState:
    def test_up_to_date_before_any_work(self, shipping_ds: dict):
        state, branch = fast_forward_state(
            shipping_ds["main"], shipping_ds["wt"],
        )
        assert state == "up-to-date"
        assert branch == "runs"

    def test_ready_once_the_worktree_is_ahead(self, shipping_ds: dict):
        _run_in_worktree(shipping_ds, identical=False)

        state, branch = fast_forward_state(
            shipping_ds["main"] / "derived", shipping_ds["wt"] / "derived",
        )
        assert state == "ready"
        assert branch == "runs"

    def test_diverged_when_both_sides_committed(self, shipping_ds: dict):
        _run_in_worktree(shipping_ds, identical=False)
        main_sub = shipping_ds["main"] / "derived"
        (main_sub / "notes.md").write_text("meanwhile\n")
        Dataset(str(main_sub)).save(message="unrelated", result_renderer="disabled")

        state, branch = fast_forward_state(main_sub, shipping_ds["wt"] / "derived")

        assert state == "diverged"
        assert branch == "runs"


class TestBehindIsNotDivergence:
    def test_worktree_strictly_behind_reports_behind(self, shipping_ds: dict):
        """
        The `code/`-subdataset case: consumed in the worktree, developed
        further in the main checkout. There is nothing to ship, and calling
        that "diverged" would refuse the whole update for no reason.
        """
        main_sub = shipping_ds["main"] / "derived"
        (main_sub / "notes.md").write_text("carried on working\n")
        Dataset(str(main_sub)).save(message="dev work", result_renderer="disabled")

        state, branch = fast_forward_state(main_sub, shipping_ds["wt"] / "derived")

        assert state == "behind"
        assert branch == "runs"

    def test_behind_dataset_is_skipped_not_refused(self, shipping_ds: dict):
        main_sub = shipping_ds["main"] / "derived"
        (main_sub / "notes.md").write_text("carried on working\n")
        Dataset(str(main_sub)).save(message="dev work", result_renderer="disabled")

        reports = _fetch(shipping_ds)

        assert not [r for r in reports if r.result == WorktreeResult.FAILED]
        skipped = [r for r in reports
                   if r.result == WorktreeResult.SKIPPED_UP_TO_DATE]
        # "ahead", not "already up to date": both skip, but only one of them
        # means the worktree had nothing to ship. Matching the discriminating
        # word rather than the whole sentence keeps this robust to rewording.
        assert any(r.dataset_path == "derived" and "ahead" in r.message
                   for r in skipped)


class TestCollisions:
    def test_incoming_paths_lists_what_the_update_rewrites(self, shipping_ds: dict):
        _run_in_worktree(shipping_ds, identical=False)

        paths = incoming_paths(shipping_ds["main"] / "derived", "runs")

        assert paths == {"vis/ses-A/heatmaps/a.png"}

    def test_unrelated_dirty_file_is_not_a_collision(self, shipping_ds: dict):
        _run_in_worktree(shipping_ds, identical=False)
        _rewrite(shipping_ds["main"] / INPUT_FILE, b"work in progress")

        assert collisions(shipping_ds["main"], "runs") == set()

    def test_dirty_incoming_path_is_a_collision(self, shipping_ds: dict):
        _run_in_worktree(shipping_ds, identical=False)
        _rewrite(shipping_ds["main"] / OUTPUT_FILE, b"hand-edited")

        clash = collisions(shipping_ds["main"] / "derived", "runs")

        assert clash == {"vis/ses-A/heatmaps/a.png"}


# ── The behaviour the command exists for ─────────────────────────────────────


class TestFetchShipsContent:
    def test_changed_output_arrives(self, shipping_ds: dict):
        _run_in_worktree(shipping_ds, identical=False)

        _fetch(shipping_ds)

        assert (shipping_ds["main"] / OUTPUT_FILE).read_bytes() == \
            b"\x89PNG" + b"v2" * 50

    def test_subdatasets_are_updated_before_the_superdataset(self, shipping_ds: dict):
        """A gitlink must not arrive before the commit it names."""
        _run_in_worktree(shipping_ds, identical=False)

        reports = _fetch(shipping_ds)
        order = [r.dataset_path for r in reports
                 if r.result == WorktreeResult.FETCHED]

        assert order == ["derived", "."]

    def test_leaves_the_checkout_clean(self, shipping_ds: dict):
        _run_in_worktree(shipping_ds, identical=False)

        _fetch(shipping_ds)

        assert _git(shipping_ds["main"], "status", "--porcelain").stdout == ""
        gitlink = _git(shipping_ds["main"], "rev-parse", "HEAD:derived").stdout.strip()
        assert gitlink == _head(shipping_ds["main"] / "derived")


class TestFetchRefreshesMtimes:
    def test_changed_output_stops_looking_stale(self, shipping_ds: dict):
        _run_in_worktree(shipping_ds, identical=False)
        assert _looks_stale(shipping_ds["main"])

        _fetch(shipping_ds)

        assert not _looks_stale(shipping_ds["main"])

    def test_identical_output_stops_looking_stale(self, shipping_ds: dict):
        """
        The case a diff-based refresh cannot see.

        A byte-identical result produces no commit, so the transport ships
        nothing and moves nothing -- yet the work was done and must not be
        repeated. Only copying mtimes out of the worktree fixes it.
        """
        _run_in_worktree(shipping_ds, identical=True)
        assert _looks_stale(shipping_ds["main"])

        reports = _fetch(shipping_ds)

        assert not _looks_stale(shipping_ds["main"])
        # nothing was merged, precisely because there was nothing to merge
        assert not [r for r in reports if r.result == WorktreeResult.FETCHED]
        assert [r.dataset_path for r in reports
                if r.result == WorktreeResult.SKIPPED_UP_TO_DATE] == ["derived", "."]

    def test_no_mtimes_leaves_it_stale(self, shipping_ds: dict):
        """Without the refresh the update is content-correct but still stale."""
        _run_in_worktree(shipping_ds, identical=True)

        reports = _fetch(shipping_ds, preserve_mtimes=False)

        assert _looks_stale(shipping_ds["main"])
        assert not [r for r in reports
                    if r.result == WorktreeResult.MTIMES_SYNCED]


class TestShippingIntoADirtyCheckout:
    """
    Unrelated work-in-progress must not block shipping.

    The workflow this exists for: create a worktree, start the long run, then
    keep developing in the main checkout. When the run finishes the main tree
    is dirty, but never in the paths the results occupy -- outputs go to the
    superdataset, code and inputs are only consumed.
    """

    def test_unrelated_dirty_file_does_not_block(self, shipping_ds: dict):
        _run_in_worktree(shipping_ds, identical=False)
        _rewrite(shipping_ds["main"] / INPUT_FILE, b"work in progress")

        reports = _fetch(shipping_ds)

        assert not [r for r in reports if r.result == WorktreeResult.FAILED]
        assert (shipping_ds["main"] / OUTPUT_FILE).read_bytes() == \
            b"\x89PNG" + b"v2" * 50

    def test_the_dirty_work_survives_untouched(self, shipping_ds: dict):
        _run_in_worktree(shipping_ds, identical=False)
        _rewrite(shipping_ds["main"] / INPUT_FILE, b"work in progress")

        _fetch(shipping_ds)

        assert (shipping_ds["main"] / INPUT_FILE).read_bytes() == b"work in progress"

    def test_dirty_path_is_not_stamped_with_the_worktrees_mtime(self, shipping_ds: dict):
        """Its mtime describes local content, not the worktree's."""
        _run_in_worktree(shipping_ds, identical=False)
        _rewrite(shipping_ds["main"] / INPUT_FILE, b"work in progress")
        before = os.lstat(shipping_ds["main"] / INPUT_FILE).st_mtime_ns

        _fetch(shipping_ds)

        assert os.lstat(shipping_ds["main"] / INPUT_FILE).st_mtime_ns == before


class TestCleanDivergenceIsMerged:
    """
    Divergence is routine, not a conflict.

    Recording a subdataset's new state in the superdataset is itself a
    superdataset commit, so as soon as work continues in the main checkout
    both sides have superdataset commits. When those commits moved *different*
    paths the merge is trivial, and `git merge-tree` says so before anything
    is touched.
    """

    def _diverge(self, shipping_ds: dict) -> None:
        _run_in_worktree(shipping_ds, identical=False)
        (shipping_ds["main"] / "notes.md").write_text("carried on working\n")
        Dataset(str(shipping_ds["main"])).save(
            message="unrelated superdataset work", result_renderer="disabled",
        )

    def test_prediction_is_clean_for_disjoint_changes(self, shipping_ds: dict):
        self._diverge(shipping_ds)

        verdict, conflicted = merge_prediction(shipping_ds["main"], "runs")

        assert verdict == "clean"
        assert conflicted == set()

    def test_prediction_is_conflict_on_the_same_path(self, shipping_ds: dict):
        _run_in_worktree(shipping_ds, identical=False)
        main_sub = shipping_ds["main"] / "derived"
        _rewrite(main_sub / "vis/ses-A/heatmaps/a.png", b"\x89PNG" + b"local" * 50)
        Dataset(str(main_sub)).save(message="local edit", result_renderer="disabled")

        verdict, conflicted = merge_prediction(main_sub, "runs")

        assert verdict == "conflict"
        assert "vis/ses-A/heatmaps/a.png" in conflicted

    def test_diverged_superdataset_is_merged(self, shipping_ds: dict):
        self._diverge(shipping_ds)

        reports = _fetch(shipping_ds)

        assert not [r for r in reports if r.result == WorktreeResult.FAILED]
        how = {r.dataset_path: r.message
               for r in reports if r.result == WorktreeResult.FETCHED}
        assert "fast-forward" in how["derived"]
        assert "merge" in how["."]

    def test_the_merge_keeps_both_sides(self, shipping_ds: dict):
        self._diverge(shipping_ds)

        _fetch(shipping_ds)

        assert (shipping_ds["main"] / OUTPUT_FILE).read_bytes() == \
            b"\x89PNG" + b"v2" * 50
        assert (shipping_ds["main"] / "notes.md").read_text() == "carried on working\n"
        assert not _looks_stale(shipping_ds["main"])

    def test_the_gitlink_agrees_with_the_subdataset_checkout(self, shipping_ds: dict):
        """The thing a superdataset merge can silently get wrong."""
        self._diverge(shipping_ds)

        _fetch(shipping_ds)

        gitlink = _git(shipping_ds["main"], "rev-parse", "HEAD:derived").stdout.strip()
        assert gitlink == _head(shipping_ds["main"] / "derived")
        assert _git(shipping_ds["main"], "status", "--porcelain").stdout == ""

    def test_the_merge_commit_is_marked_as_extension_made(self, shipping_ds: dict):
        self._diverge(shipping_ds)

        _fetch(shipping_ds)

        subject = _git(shipping_ds["main"], "log", "-1", "--format=%s").stdout
        assert subject.startswith(MERGE_COMMIT_PREFIX)


class TestPreflightRefuses:
    def test_colliding_path_changes_nothing(self, shipping_ds: dict):
        _run_in_worktree(shipping_ds, identical=False)
        _rewrite(shipping_ds["main"] / OUTPUT_FILE, b"hand-edited, uncommitted")
        before = _head(shipping_ds["main"] / "derived")

        reports = _fetch(shipping_ds)

        assert [r.result for r in reports] == [WorktreeResult.FAILED]
        assert "would overwrite uncommitted changes" in reports[0].message
        assert "vis/ses-A/heatmaps/a.png" in reports[0].message
        assert _head(shipping_ds["main"] / "derived") == before
        assert (shipping_ds["main"] / OUTPUT_FILE).read_bytes() == \
            b"hand-edited, uncommitted"

    def test_conflicting_divergence_changes_nothing(self, shipping_ds: dict):
        """Both sides rewrote the same output: merge-tree reports a conflict."""
        _run_in_worktree(shipping_ds, identical=False)
        main_sub = shipping_ds["main"] / "derived"
        _rewrite(main_sub / "vis/ses-A/heatmaps/a.png", b"\x89PNG" + b"local" * 50)
        Dataset(str(main_sub)).save(message="local edit", result_renderer="disabled")
        before_super = _head(shipping_ds["main"])
        before_sub = _head(main_sub)

        reports = _fetch(shipping_ds)

        assert all(r.result == WorktreeResult.FAILED for r in reports)
        assert any("conflicts with" in r.message for r in reports)
        assert _head(shipping_ds["main"]) == before_super
        assert _head(main_sub) == before_sub

    def test_preflight_names_the_offending_dataset(self, shipping_ds: dict):
        _run_in_worktree(shipping_ds, identical=False)
        _rewrite(shipping_ds["main"] / OUTPUT_FILE, b"hand-edited")

        errors = preflight(dataset_pairs(shipping_ds["main"], shipping_ds["wt"]))

        assert [path for path, _ in errors] == ["derived"]


class TestFetchGuards:
    def test_rejects_a_worktree_that_is_its_own_target(self, shipping_ds: dict):
        with pytest.raises(ValueError, match="its own target"):
            list(fetch_nested_worktrees(
                main_path=shipping_ds["main"], worktree_path=shipping_ds["main"],
            ))

    def test_rejects_a_non_repo(self, tmp_path: Path, shipping_ds: dict):
        with pytest.raises(ValueError, match="Not a git repository"):
            list(fetch_nested_worktrees(
                main_path=tmp_path, worktree_path=shipping_ds["wt"],
            ))

    def test_dry_run_changes_nothing(self, shipping_ds: dict):
        _run_in_worktree(shipping_ds, identical=False)
        before = _head(shipping_ds["main"] / "derived")

        reports = _fetch(shipping_ds, dry_run=True)

        assert _head(shipping_ds["main"] / "derived") == before
        assert _looks_stale(shipping_ds["main"])
        assert [r.result for r in reports] == [
            WorktreeResult.SKIPPED_DRY_RUN, WorktreeResult.SKIPPED_DRY_RUN,
        ]
        assert "would fast-forward" in reports[0].message


class TestRefreshingAWorktree:
    """
    `fetch` with no argument, run inside a worktree.

    The other direction: bring the checkout you came from into this worktree,
    mtimes included. When the worktree is already up to date no commits move
    and it reduces to an mtime repair -- which is all the removed
    `sync-mtimes` subcommand ever did.
    """

    def test_source_defaults_to_where_the_worktree_came_from(self, shipping_ds: dict):
        assert resolve_fetch_source(None, shipping_ds["wt"]) == \
            shipping_ds["main"].resolve()

    def test_a_main_checkout_has_no_default_source(self, shipping_ds: dict):
        with pytest.raises(ValueError, match="not a linked worktree"):
            resolve_fetch_source(None, shipping_ds["main"])

    def test_restores_mtimes_reset_by_a_checkout(self, shipping_ds: dict):
        wt, main = shipping_ds["wt"], shipping_ds["main"]
        original = os.lstat(main / OUTPUT_FILE).st_mtime_ns
        _touch(wt / OUTPUT_FILE)  # as a branch switch would
        assert os.lstat(wt / OUTPUT_FILE).st_mtime_ns != original

        list(fetch_nested_worktrees(main_path=wt, worktree_path=main))

        assert os.lstat(wt / OUTPUT_FILE).st_mtime_ns == original

    def test_nothing_moves_when_already_up_to_date(self, shipping_ds: dict):
        before = _head(shipping_ds["wt"])

        reports = list(fetch_nested_worktrees(
            main_path=shipping_ds["wt"], worktree_path=shipping_ds["main"],
        ))

        assert _head(shipping_ds["wt"]) == before
        assert not [r for r in reports if r.result == WorktreeResult.FETCHED]
        assert [r.dataset_path for r in reports
                if r.result == WorktreeResult.MTIMES_SYNCED] == [".", "derived"]

    def test_brings_the_worktree_forward_when_behind(self, shipping_ds: dict):
        """Development continued in main while the worktree sat still."""
        main, wt = shipping_ds["main"], shipping_ds["wt"]
        (main / "notes.md").write_text("developed in main\n")
        Dataset(str(main)).save(message="dev", result_renderer="disabled")

        reports = list(fetch_nested_worktrees(main_path=wt, worktree_path=main))

        assert (wt / "notes.md").read_text() == "developed in main\n"
        assert [r.dataset_path for r in reports
                if r.result == WorktreeResult.FETCHED] == ["."]

    def test_cli_with_no_target_from_inside_the_worktree(
        self, shipping_ds: dict, capsys, monkeypatch,
    ):
        wt, main = shipping_ds["wt"], shipping_ds["main"]
        original = os.lstat(main / OUTPUT_FILE).st_mtime_ns
        _touch(wt / OUTPUT_FILE)
        monkeypatch.chdir(wt)

        exit_code = main_cli(["--no-color", "fetch"])

        assert exit_code == 0
        assert "mtimes" in capsys.readouterr().out
        assert os.lstat(wt / OUTPUT_FILE).st_mtime_ns == original

    def test_cli_with_no_target_in_a_main_checkout_fails(
        self, shipping_ds: dict, capsys, monkeypatch,
    ):
        monkeypatch.chdir(shipping_ds["main"])

        exit_code = main_cli(["--no-color", "fetch"])

        assert exit_code == 1
        assert "not a linked worktree" in capsys.readouterr().err

    def test_cli_reports_a_non_repo(self, tmp_path: Path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)

        exit_code = main_cli(["--no-color", "fetch", str(tmp_path)])

        assert exit_code == 1
        assert "Not a git repository" in capsys.readouterr().err


class TestFetchCLI:
    def test_parser_accepts_update(self):
        args = build_parser().parse_args(["fetch", "runs"])
        assert args.command == "fetch"
        assert args.target == "runs"
        assert args.dry_run is False
        assert args.no_mtimes is False

    def test_parser_accepts_flags(self):
        args = build_parser().parse_args(["fetch", "-n", "--no-mtimes", "/tmp/wt"])
        assert args.dry_run is True
        assert args.no_mtimes is True

    def test_parser_accepts_no_target(self):
        args = build_parser().parse_args(["fetch"])
        assert args.target is None

    def test_main_updates_by_branch_name(self, shipping_ds: dict, capsys, monkeypatch):
        _run_in_worktree(shipping_ds, identical=False)
        monkeypatch.chdir(shipping_ds["main"])

        exit_code = main_cli(["--no-color", "fetch", "runs"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "fetch  derived" in out
        assert "2 fetched, 0 failed" in out
        assert not _looks_stale(shipping_ds["main"])

    def test_main_reports_a_refusal(self, shipping_ds: dict, capsys, monkeypatch):
        _run_in_worktree(shipping_ds, identical=False)
        _rewrite(shipping_ds["main"] / OUTPUT_FILE, b"hand-edited")
        monkeypatch.chdir(shipping_ds["main"])

        exit_code = main_cli(["--no-color", "fetch", "runs"])

        assert exit_code == 1
        assert "would overwrite uncommitted changes" in capsys.readouterr().err
