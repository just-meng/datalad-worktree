"""Tests for mtime preservation when creating worktrees."""

from __future__ import annotations

import os
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
    parent_dirs,
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
