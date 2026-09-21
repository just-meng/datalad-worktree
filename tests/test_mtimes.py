"""Tests for mtime preservation when creating worktrees."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from datalad.api import create, install
from datalad.distribution.dataset import Dataset

from datalad_worktree.add import create_nested_worktrees
from datalad_worktree.core import WorktreeResult
from datalad_worktree.mtimes import (
    copy_mtimes,
    dirty_paths,
    main_working_tree,
    parent_dirs,
    resolve_worktree_target,
    sync_nested_mtimes,
    tracked_blobs,
    transferable_paths,
)

# Two fixed, well-separated timestamps. The "output" is deliberately newer
# than the "script": that ordering is the up-to-date state a make-style
# pipeline reads, and it is what a fresh worktree destroys.
SCRIPT_MTIME_NS = 1_788_256_800_000_000_000  # 2026-09-01 12:00 UTC
OUTPUT_MTIME_NS = 1_789_034_400_000_000_000  # 2026-09-10 12:00 UTC


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=True,
    )


def _set_mtime(path: Path, mtime_ns: int) -> None:
    os.utime(path, ns=(mtime_ns, mtime_ns), follow_symlinks=False)


@pytest.fixture()
def pipeline_ds(tmp_path: Path) -> dict:
    """
    A superdataset shaped like a make-style pipeline.

    ::

        super/
        ├── out.txt        "derived output", mtime 2026-09-10
        └── code/          subdataset
            └── script.py  "input",          mtime 2026-09-01

    Returns a dict with keys: super, code, wt_location.
    """
    origins = tmp_path / "origins"

    code = Dataset(create(path=str(origins / "code"), result_renderer="disabled").path)
    (code.pathobj / "script.py").write_text("print('hello')\n")
    code.save(message="add script", result_renderer="disabled")

    superds = Dataset(create(path=str(origins / "super"), result_renderer="disabled").path)
    (superds.pathobj / "out.txt").write_text("derived\n")
    (superds.pathobj / "results").mkdir()
    (superds.pathobj / "results" / "table.csv").write_text("a,b\n1,2\n")
    superds.save(message="add outputs", result_renderer="disabled")

    install(
        dataset=superds,
        source=str(code.path),
        path="code",
        result_renderer="disabled",
    )
    superds.save(message="add code subdataset", result_renderer="disabled")

    _set_mtime(superds.pathobj / "code" / "script.py", SCRIPT_MTIME_NS)
    for name in ("out.txt", "results/table.csv"):
        _set_mtime(superds.pathobj / name, OUTPUT_MTIME_NS)
    _set_mtime(superds.pathobj / "results", OUTPUT_MTIME_NS)

    return {
        "super": superds.pathobj,
        "code": superds.pathobj / "code",
        "wt_location": tmp_path / "worktrees",
    }


def _add(pipeline_ds: dict, name: str, branch: str, **kwargs) -> Path:
    """Create worktrees and return the worktree root."""
    worktree = pipeline_ds["wt_location"] / name
    list(create_nested_worktrees(
        superds_path=pipeline_ds["super"],
        worktree_path=worktree,
        branch=branch,
        **kwargs,
    ))
    return worktree


# ── Parsing helpers ──────────────────────────────────────────────────────────


class TestTrackedBlobs:
    def test_excludes_submodule_gitlinks(self, pipeline_ds: dict):
        """A gitlink is where another worktree mounts, not a file to stamp."""
        blobs = tracked_blobs(pipeline_ds["super"])
        assert "out.txt" in blobs
        assert "code" not in blobs

    def test_includes_symlinks(self, pipeline_ds: dict):
        """Annexed files are symlinks; they carry their own mtime."""
        blobs = tracked_blobs(pipeline_ds["super"])
        symlinks = [
            p for p in blobs
            if (pipeline_ds["super"] / p).is_symlink()
        ]
        # The fixture annexes its content, so at least one must be a symlink.
        assert symlinks, f"expected annexed symlinks among {sorted(blobs)}"

    def test_empty_for_non_repo(self, tmp_path: Path):
        assert tracked_blobs(tmp_path) == {}


class TestDirtyPaths:
    def test_clean_tree_is_empty(self, pipeline_ds: dict):
        assert dirty_paths(pipeline_ds["super"]) == set()

    def test_reports_modification(self, pipeline_ds: dict):
        _git(pipeline_ds["super"], "annex", "unlock", "out.txt")
        (pipeline_ds["super"] / "out.txt").write_text("edited\n")
        assert "out.txt" in dirty_paths(pipeline_ds["super"])

    def test_reports_both_sides_of_a_rename(self, pipeline_ds: dict):
        _git(pipeline_ds["super"], "mv", "out.txt", "renamed.txt")
        dirty = dirty_paths(pipeline_ds["super"])
        assert {"out.txt", "renamed.txt"} <= dirty


class TestParentDirs:
    def test_deepest_first(self):
        dirs = parent_dirs(["a/b/c/file.txt", "a/other.txt"])
        assert dirs == ["a/b/c", "a/b", "a"]

    def test_excludes_the_root(self):
        assert parent_dirs(["top.txt"]) == []


# ── The behaviour the feature exists for ─────────────────────────────────────


class TestCreateNestedWorktreesMtimes:
    def test_tracked_paths_keep_their_mtime(self, pipeline_ds: dict):
        worktree = _add(pipeline_ds, "wt", "feat/mtimes")

        for rel in ("out.txt", "results/table.csv", "code/script.py"):
            assert os.lstat(worktree / rel).st_mtime_ns == \
                os.lstat(pipeline_ds["super"] / rel).st_mtime_ns, rel

    def test_directory_mtimes_are_copied(self, pipeline_ds: dict):
        """Snakemake's directory() outputs read the directory's own mtime."""
        worktree = _add(pipeline_ds, "wt", "feat/mtimes")

        assert os.lstat(worktree / "results").st_mtime_ns == \
            os.lstat(pipeline_ds["super"] / "results").st_mtime_ns

    def test_cross_dataset_ordering_is_preserved(self, pipeline_ds: dict):
        """The whole point: the output must stay newer than the script."""
        worktree = _add(pipeline_ds, "wt", "feat/mtimes")

        assert os.lstat(worktree / "out.txt").st_mtime_ns > \
            os.lstat(worktree / "code" / "script.py").st_mtime_ns

    def test_ordering_is_inverted_without_the_fix(self, pipeline_ds: dict):
        """The negative control -- without this, the test above proves nothing."""
        worktree = _add(pipeline_ds, "wt", "feat/mtimes", preserve_mtimes=False)

        assert os.lstat(worktree / "out.txt").st_mtime_ns < \
            os.lstat(worktree / "code" / "script.py").st_mtime_ns

    def test_no_mtimes_leaves_checkout_times(self, pipeline_ds: dict):
        worktree = _add(pipeline_ds, "wt", "feat/mtimes", preserve_mtimes=False)

        assert os.lstat(worktree / "out.txt").st_mtime_ns != \
            os.lstat(pipeline_ds["super"] / "out.txt").st_mtime_ns

    def test_reports_one_line_per_dataset(self, pipeline_ds: dict):
        reports = list(create_nested_worktrees(
            superds_path=pipeline_ds["super"],
            worktree_path=pipeline_ds["wt_location"] / "wt",
            branch="feat/mtimes",
        ))
        synced = [r for r in reports if r.result == WorktreeResult.MTIMES_SYNCED]

        assert sorted(r.dataset_path for r in synced) == [".", "code"]
        assert all("files" in r.message and "dirs" in r.message for r in synced)

    def test_no_mtimes_reports_nothing(self, pipeline_ds: dict):
        reports = list(create_nested_worktrees(
            superds_path=pipeline_ds["super"],
            worktree_path=pipeline_ds["wt_location"] / "wt",
            branch="feat/mtimes",
            preserve_mtimes=False,
        ))

        assert not [r for r in reports if r.result == WorktreeResult.MTIMES_SYNCED]


class TestSafety:
    def test_annex_objects_are_untouched(self, pipeline_ds: dict):
        """
        Regression test for a follow_symlinks=True slip.

        The annex object store is shared between the main repo and its
        worktrees -- writing through an annex symlink would rewrite the
        source dataset's own (mode 444) objects.
        """
        objects = sorted(
            p for p in (pipeline_ds["super"] / ".git" / "annex" / "objects").rglob("*")
            if p.is_file()
        )
        assert objects, "fixture produced no annex objects to guard"
        before = {p: os.lstat(p).st_mtime_ns for p in objects}

        _add(pipeline_ds, "wt", "feat/mtimes")

        assert {p: os.lstat(p).st_mtime_ns for p in objects} == before

    def test_differing_blob_keeps_its_checkout_mtime(self, pipeline_ds: dict):
        """
        Content that genuinely differs must not be stamped as fresh.

        This is the failure mode that matching on blob OID rules out: a
        path-only match would claim the reference's mtime for content the
        worktree does not have.
        """
        superds = pipeline_ds["super"]
        _git(superds, "checkout", "-q", "-b", "variant")
        _git(superds, "annex", "unlock", "out.txt")
        (superds / "out.txt").write_text("a different result\n")
        Dataset(str(superds)).save(message="diverge", result_renderer="disabled")
        _git(superds, "checkout", "-q", "master")
        _set_mtime(superds / "out.txt", OUTPUT_MTIME_NS)

        worktree = _add(pipeline_ds, "wt", "variant")

        assert os.lstat(worktree / "out.txt").st_mtime_ns != OUTPUT_MTIME_NS
        # ...while everything that did not diverge is still carried over.
        assert os.lstat(worktree / "results" / "table.csv").st_mtime_ns == \
            OUTPUT_MTIME_NS

    def test_dirty_reference_file_is_skipped(self, pipeline_ds: dict):
        """A modified file's mtime describes content the worktree lacks."""
        superds = pipeline_ds["super"]
        _git(superds, "annex", "unlock", "out.txt")
        (superds / "out.txt").write_text("uncommitted edit\n")
        _set_mtime(superds / "out.txt", OUTPUT_MTIME_NS)

        worktree = _add(pipeline_ds, "wt", "feat/mtimes")

        assert os.lstat(worktree / "out.txt").st_mtime_ns != OUTPUT_MTIME_NS


class TestCopyMtimes:
    def test_returns_counts(self, pipeline_ds: dict):
        worktree = _add(pipeline_ds, "wt", "feat/mtimes", preserve_mtimes=False)

        files, dirs, error = copy_mtimes(pipeline_ds["super"], worktree)

        assert error == ""
        assert files >= 2      # out.txt, results/table.csv, .datalad/*
        assert dirs >= 1       # results/
        assert os.lstat(worktree / "out.txt").st_mtime_ns == OUTPUT_MTIME_NS

    def test_transferable_excludes_gitlink_and_dirty(self, pipeline_ds: dict):
        worktree = _add(pipeline_ds, "wt", "feat/mtimes")

        paths = transferable_paths(pipeline_ds["super"], worktree)

        assert "out.txt" in paths
        assert "code" not in paths


class TestSyncNestedMtimes:
    def test_restores_mtimes_after_a_checkout(self, pipeline_ds: dict):
        """The case the subcommand exists for: files rewritten after creation."""
        worktree = _add(pipeline_ds, "wt", "feat/mtimes")

        # Something rewrites the files -- datalad get, a merge, a checkout.
        for rel in ("out.txt", "results/table.csv", "code/script.py"):
            _set_mtime(worktree / rel, 1_700_000_000_000_000_000)

        list(sync_nested_mtimes(worktree_path=worktree))

        assert os.lstat(worktree / "out.txt").st_mtime_ns == OUTPUT_MTIME_NS
        assert os.lstat(worktree / "code" / "script.py").st_mtime_ns == \
            SCRIPT_MTIME_NS

    def test_covers_superdataset_and_subdatasets(self, pipeline_ds: dict):
        worktree = _add(pipeline_ds, "wt", "feat/mtimes")

        reports = list(sync_nested_mtimes(worktree_path=worktree))
        synced = [r for r in reports if r.result == WorktreeResult.MTIMES_SYNCED]

        assert sorted(r.dataset_path for r in synced) == [".", "code"]

    def test_reference_is_auto_detected(self, pipeline_ds: dict):
        """The super worktree knows the working tree it was created from."""
        worktree = _add(pipeline_ds, "wt", "feat/mtimes")

        assert main_working_tree(worktree) == pipeline_ds["super"].resolve()

    def test_explicit_reference_is_used(self, pipeline_ds: dict):
        worktree = _add(pipeline_ds, "wt", "feat/mtimes")
        _set_mtime(worktree / "out.txt", 1_700_000_000_000_000_000)

        list(sync_nested_mtimes(
            worktree_path=worktree,
            reference=pipeline_ds["super"],
        ))

        assert os.lstat(worktree / "out.txt").st_mtime_ns == OUTPUT_MTIME_NS

    def test_rejects_a_non_worktree(self, pipeline_ds: dict):
        """A dataset that is its own reference has nothing to copy."""
        with pytest.raises(ValueError, match="its own reference"):
            list(sync_nested_mtimes(worktree_path=pipeline_ds["super"]))

    def test_rejects_a_non_repo(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Not a git repository"):
            list(sync_nested_mtimes(worktree_path=tmp_path))

    def test_skips_a_missing_reference_subdataset(self, pipeline_ds: dict):
        """A reference without the subdataset installed is reported, not fatal."""
        worktree = _add(pipeline_ds, "wt", "feat/mtimes")
        bare_reference = pipeline_ds["wt_location"] / "reference-only"
        _git(pipeline_ds["super"], "worktree", "add", "-q",
             "-b", "reference-only", str(bare_reference))

        reports = list(sync_nested_mtimes(
            worktree_path=worktree,
            reference=bare_reference,
        ))
        skipped = [r for r in reports if r.dataset_path == "code"]

        assert skipped
        assert skipped[0].result in (
            WorktreeResult.SKIPPED_NOT_GIT_REPO,
            WorktreeResult.SKIPPED_NOT_INSTALLED,
        )


class TestSyncMtimesCLI:
    def test_parser_accepts_optional_target(self):
        from datalad_worktree.cli import build_parser

        args = build_parser().parse_args(["sync-mtimes"])
        assert args.command == "sync-mtimes"
        assert args.target is None
        assert args.reference is None

    def test_parser_accepts_from(self):
        from datalad_worktree.cli import build_parser

        args = build_parser().parse_args(
            ["sync-mtimes", "--from", "/data/super", "/tmp/wt"]
        )
        assert str(args.reference) == "/data/super"
        assert args.target == "/tmp/wt"

    def test_main_syncs(self, pipeline_ds: dict, capsys):
        from datalad_worktree.cli import main

        worktree = _add(pipeline_ds, "wt", "feat/mtimes")
        _set_mtime(worktree / "out.txt", 1_700_000_000_000_000_000)

        exit_code = main(["--no-color", "sync-mtimes", str(worktree)])
        out = capsys.readouterr().out

        assert exit_code == 0
        assert "mtimes" in out
        assert os.lstat(worktree / "out.txt").st_mtime_ns == OUTPUT_MTIME_NS

    def test_main_reports_error_for_non_repo(self, tmp_path: Path, capsys):
        from datalad_worktree.cli import main

        exit_code = main(["--no-color", "sync-mtimes", str(tmp_path)])

        assert exit_code == 1
        assert "Not a git repository" in capsys.readouterr().err


class TestResolveWorktreeTarget:
    """A target is a worktree path or a branch name, as `delete` reads it."""

    def test_branch_name_resolves_to_its_worktree(self, pipeline_ds: dict):
        worktree = _add(pipeline_ds, "wt", "runs")

        root = resolve_worktree_target(target="runs", dataset=pipeline_ds["super"])

        assert root == worktree.resolve()

    def test_existing_path_is_taken_as_a_path(self, pipeline_ds: dict):
        worktree = _add(pipeline_ds, "wt", "runs")

        assert resolve_worktree_target(target=str(worktree)) == worktree.resolve()

    def test_unknown_branch_names_both_readings(self, pipeline_ds: dict):
        with pytest.raises(ValueError, match="no worktree on branch 'nope'"):
            resolve_worktree_target(target="nope", dataset=pipeline_ds["super"])

    def test_branch_of_the_dataset_itself_is_rejected(self, pipeline_ds: dict):
        """The main checkout is its own reference; there is nothing to copy."""
        branch = _git(
            pipeline_ds["super"], "symbolic-ref", "--short", "HEAD"
        ).stdout.strip()
        root = resolve_worktree_target(target=branch, dataset=pipeline_ds["super"])

        with pytest.raises(ValueError, match="its own reference"):
            list(sync_nested_mtimes(worktree_path=root))

    def test_resolves_a_sibling_from_inside_a_worktree(self, pipeline_ds: dict):
        """git worktree list reports siblings, so worktree->worktree works."""
        first = _add(pipeline_ds, "wt-a", "runs")
        second = _add(pipeline_ds, "wt-b", "other")

        assert resolve_worktree_target(target="other", dataset=first) == \
            second.resolve()

    def test_sibling_lookup_still_copies_from_the_main_tree(self, pipeline_ds: dict):
        """Not from the sibling that happened to answer the lookup."""
        first = _add(pipeline_ds, "wt-a", "runs")
        second = _add(pipeline_ds, "wt-b", "other")
        _set_mtime(second / "out.txt", 1_700_000_000_000_000_000)
        _set_mtime(first / "out.txt", 1_600_000_000_000_000_000)

        root = resolve_worktree_target(target="other", dataset=first)
        list(sync_nested_mtimes(worktree_path=root))

        assert os.lstat(second / "out.txt").st_mtime_ns == OUTPUT_MTIME_NS

    def test_stale_worktree_is_pruned_before_lookup(self, pipeline_ds: dict):
        """A directory removed with rm -rf must not resolve."""
        worktree = _add(pipeline_ds, "wt", "runs")
        shutil.rmtree(worktree)

        with pytest.raises(ValueError, match="no worktree on branch 'runs'"):
            resolve_worktree_target(target="runs", dataset=pipeline_ds["super"])


class TestSyncMtimesByBranch:
    def test_cli_accepts_a_branch_name(self, pipeline_ds: dict, capsys):
        """`worktree sync-mtimes runs`, run from the superdataset."""
        from datalad_worktree.cli import main

        worktree = _add(pipeline_ds, "wt", "runs")
        _set_mtime(worktree / "out.txt", 1_700_000_000_000_000_000)

        exit_code = main([
            "--no-color", "sync-mtimes", "runs",
            "-d", str(pipeline_ds["super"]),
        ])

        assert exit_code == 0
        assert "mtimes" in capsys.readouterr().out
        assert os.lstat(worktree / "out.txt").st_mtime_ns == OUTPUT_MTIME_NS

    def test_cli_unknown_branch_exits_1(self, pipeline_ds: dict, capsys):
        from datalad_worktree.cli import main

        exit_code = main([
            "--no-color", "sync-mtimes", "nope",
            "-d", str(pipeline_ds["super"]),
        ])

        assert exit_code == 1
        assert "no worktree on branch 'nope'" in capsys.readouterr().err
