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
    tracked_blobs,
    transferable_paths,
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

    def test_detects_a_really_unlocked_file(self, pipeline_ds: dict):
        _commit_unlocked(pipeline_ds["super"], "out.txt")

        assert "out.txt" in unlocked_annex_paths(pipeline_ds["super"])

    def test_transferable_paths_excludes_it(self, pipeline_ds: dict):
        _commit_unlocked(pipeline_ds["super"], "out.txt")
        worktree = _add(pipeline_ds, "wt", "feat/unlocked", preserve_mtimes=False)

        paths = transferable_paths(pipeline_ds["super"], worktree)

        assert "out.txt" not in paths
        # The locked sibling is still carried, so this is not a blanket skip.
        assert "results/table.csv" in paths

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
        # The branch has to exist in *every* dataset, otherwise `add` reads it
        # as a leftover from an earlier run: present in only some datasets and
        # holding a commit the checkout lacks is refused, not checked out.
        # See branches_to_reset() in add.py.
        _git(pipeline_ds["code"], "branch", "variant")
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


class TestMainWorkingTree:
    def test_reference_is_auto_detected(self, pipeline_ds: dict):
        """The super worktree knows the working tree it was created from."""
        worktree = _add(pipeline_ds, "wt", "feat/mtimes")

        assert main_working_tree(worktree) == pipeline_ds["super"].resolve()

    def test_a_main_checkout_resolves_to_itself(self, pipeline_ds: dict):
        """
        Not None: --git-common-dir in a main checkout is its own .git, so the
        answer is the checkout itself. Useless as a source, which is why
        resolve_fetch_source rejects it rather than trusting this to be None.
        """
        assert main_working_tree(pipeline_ds["super"]) == \
            pipeline_ds["super"].resolve()


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

    def test_resolves_a_sibling_from_inside_a_worktree(self, pipeline_ds: dict):
        """git worktree list reports siblings, so worktree->worktree works."""
        first = _add(pipeline_ds, "wt-a", "runs")
        second = _add(pipeline_ds, "wt-b", "other")

        assert resolve_worktree_target(target="other", dataset=first) == \
            second.resolve()

    def test_stale_worktree_is_pruned_before_lookup(self, pipeline_ds: dict):
        """A directory removed with rm -rf must not resolve."""
        worktree = _add(pipeline_ds, "wt", "runs")
        shutil.rmtree(worktree)

        with pytest.raises(ValueError, match="no worktree on branch 'runs'"):
            resolve_worktree_target(target="runs", dataset=pipeline_ds["super"])
