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
from datalad_worktree.mtimes import (
    SNAKEMAKE_TIMESTAMP,
    copy_mtimes,
    dirty_paths,
    resolve_worktree_target,
    tracked_blobs,
    unlocked_annex_paths,
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


def _commit_unlocked(ds_path: Path, rel: str) -> None:
    """
    Re-commit ``rel`` as an *unlocked* annexed file.

    ``git add`` (rather than ``git annex add`` or ``datalad save``, both of
    which re-lock) sends the content through git-annex's clean filter, which
    stores it in the annex and leaves a pointer blob in the tree. That is how
    unlocked files arise in practice -- the other way being a repo-global
    ``git annex config --set annex.addunlocked <glob>``.
    """
    _git(ds_path, "annex", "unlock", rel)
    _git(ds_path, "add", rel)
    _git(ds_path, "commit", "-qm", f"unlock {rel}")


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


class TestDirtyPaths:

    def test_reports_both_sides_of_a_rename(self, pipeline_ds: dict):
        _git(pipeline_ds["super"], "mv", "out.txt", "renamed.txt")
        dirty = dirty_paths(pipeline_ds["super"])
        assert {"out.txt", "renamed.txt"} <= dirty


class TestUnlockedAnnexFiles:
    """
    Unlocked annexed files must never be stamped.

    Stamping one invalidates its index stat-cache entry, so the next
    ``git status`` re-hashes the whole file through git-annex's clean filter.
    On the dataset this was found on, that was 152 s for 3.71 GB -- against
    0.11 s for the 10099 locked symlinks beside them.
    """

    def test_detects_a_pointer_blob(self, tmp_path: Path):
        """A pointer blob is recognised without git-annex being involved."""
        repo = tmp_path / "plain"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "pointer.pkl").write_text(
            "/annex/objects/MD5E-s289878220--9959612438e19297b29e640ccff2dc61.pkl\n"
        )
        (repo / "plain.txt").write_text("just text\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "one pointer, one plain file")

        assert unlocked_annex_paths(repo) == {"pointer.pkl"}

    def test_locked_symlinks_are_not_reported(self, pipeline_ds: dict):
        """The fixture's annexed files are locked, so none should match."""
        assert unlocked_annex_paths(pipeline_ds["super"]) == set()

    def test_copy_mtimes_leaves_the_unlocked_file_alone(self, pipeline_ds: dict):
        """
        The regression: the unlocked file keeps whatever the checkout gave it,
        while its locked sibling is still stamped.

        Asserting on "the two sides differ" would not work -- git-annex
        materialises an unlocked file with the source's mtime anyway -- so the
        reference gets a distinctive mtime and the worktree is checked against
        the value it had *before* the copy.
        """
        _commit_unlocked(pipeline_ds["super"], "out.txt")
        reference = pipeline_ds["super"]
        worktree = _add(pipeline_ds, "wt", "feat/unlocked", preserve_mtimes=False)

        _set_mtime(reference / "out.txt", SCRIPT_MTIME_NS)
        _set_mtime(reference / "results" / "table.csv", SCRIPT_MTIME_NS)
        unstamped = os.lstat(worktree / "out.txt").st_mtime_ns

        copy_mtimes(reference, worktree)

        assert os.lstat(worktree / "out.txt").st_mtime_ns == unstamped
        assert os.lstat(worktree / "results" / "table.csv").st_mtime_ns == \
            SCRIPT_MTIME_NS


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

    def test_snakemake_touch_survives_into_the_worktree(self, pipeline_ds: dict):
        """
        `snakemake --touch` on a directory() output carries into a worktree.

        The reported case: `code/` was edited after `match_cells` ran, and
        `--touch` marked its `by-cell/` output up to date again -- by stamping
        the gitignored `.snakemake_timestamp` inside it, not the directory.
        A fresh worktree without that file reran the job.
        """
        main = pipeline_ds["super"]
        marker = main / "results" / SNAKEMAKE_TIMESTAMP
        marker.touch()
        _set_mtime(marker, OUTPUT_MTIME_NS)
        # The directory's own mtime predates the code edit; --touch leaves it.
        _set_mtime(main / "results", SCRIPT_MTIME_NS - 1)

        def snakemake_mtime(directory: Path) -> int:
            """The mtime Snakemake reads for a directory() output."""
            marker = directory / SNAKEMAKE_TIMESTAMP
            return os.stat(marker if marker.exists() else directory).st_mtime_ns

        script_ns = os.lstat(main / "code" / "script.py").st_mtime_ns
        assert snakemake_mtime(main / "results") > script_ns, "main must be up to date"
        assert os.lstat(main / "results").st_mtime_ns < script_ns, (
            "without the marker the directory must look stale, or this proves nothing"
        )

        worktree = _add(pipeline_ds, "wt", "feat/touched")

        assert snakemake_mtime(worktree / "results") > \
            os.lstat(worktree / "code" / "script.py").st_mtime_ns
        # Creating the marker must not have disturbed the directory stamp.
        assert os.lstat(worktree / "results").st_mtime_ns == \
            os.lstat(main / "results").st_mtime_ns

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
        # A worktree of another state comes from --follow-parent: an existing
        # branch is otherwise reset to the checkout's HEAD.
        _git(superds, "checkout", "-q", "-b", "variant")
        _git(superds, "annex", "unlock", "out.txt")
        (superds / "out.txt").write_text("a different result\n")
        Dataset(str(superds)).save(message="diverge", result_renderer="disabled")
        _git(superds, "checkout", "-q", "master")
        _set_mtime(superds / "out.txt", OUTPUT_MTIME_NS)

        worktree = _add(pipeline_ds, "wt", "wt-variant",
                        follow_parent=True, at_commit="variant")

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

    def test_dirty_target_file_is_skipped(self, pipeline_ds: dict):
        """
        The mirror case: stamping a path the *target* has modified would give
        local work a timestamp claiming the reference's content.

        ``worktree update`` copies into a checkout that is allowed to be
        dirty, which makes this routine rather than hypothetical.
        """
        superds = pipeline_ds["super"]
        worktree = _add(pipeline_ds, "wt", "feat/mtimes", preserve_mtimes=False)

        _git(worktree, "annex", "unlock", "out.txt")
        (worktree / "out.txt").write_text("edited in the target\n")
        untouched = os.lstat(worktree / "out.txt").st_mtime_ns
        _set_mtime(superds / "out.txt", SCRIPT_MTIME_NS)

        copy_mtimes(superds, worktree)

        assert os.lstat(worktree / "out.txt").st_mtime_ns == untouched
        # a clean sibling is still carried, so this is not a blanket skip
        assert os.lstat(worktree / "results" / "table.csv").st_mtime_ns == \
            os.lstat(superds / "results" / "table.csv").st_mtime_ns


class TestResolveWorktreeTarget:
    """A target is a worktree path or a branch name, as `delete` reads it."""

    def test_existing_path_is_taken_as_a_path(self, pipeline_ds: dict):
        worktree = _add(pipeline_ds, "wt", "runs")

        assert resolve_worktree_target(target=str(worktree)) == worktree.resolve()

    def test_stale_worktree_is_pruned_before_lookup(self, pipeline_ds: dict):
        """A directory removed with rm -rf must not resolve."""
        worktree = _add(pipeline_ds, "wt", "runs")
        shutil.rmtree(worktree)

        with pytest.raises(ValueError, match="no worktree on branch 'runs'"):
            resolve_worktree_target(target="runs", dataset=pipeline_ds["super"])
