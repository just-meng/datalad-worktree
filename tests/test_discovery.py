"""Tests for subdataset discovery."""

from __future__ import annotations

import shutil
from pathlib import Path

from datalad_worktree.discovery import (
    discover_via_datalad,
    discover_via_gitmodules,
    discover_subdatasets,
    is_git_repo,
)


class TestIsGitRepo:
    def test_valid_dataset(self, datalad_ds: Path):
        assert is_git_repo(datalad_ds) is True

    def test_not_a_repo(self, tmp_path: Path):
        assert is_git_repo(tmp_path) is False

    def test_nonexistent_path(self, tmp_path: Path):
        assert is_git_repo(tmp_path / "nope") is False


class TestDiscoverViaGitmodules:
    def test_no_subdatasets(self, datalad_ds: Path):
        result = discover_via_gitmodules(datalad_ds)
        assert result == []

    def test_flat_subdatasets(self, superds: dict):
        subs = discover_via_gitmodules(superds["super"])
        rel_paths = [s.rel_path for s in subs]
        assert "sub-01" in rel_paths
        assert "sub-02" in rel_paths

    def test_nested_subdataset_discovered(self, superds: dict):
        subs = discover_via_gitmodules(superds["super"])
        rel_paths = [s.rel_path for s in subs]
        assert "sub-01/derivatives" in rel_paths

    def test_all_marked_installed(self, superds: dict):
        subs = discover_via_gitmodules(superds["super"])
        assert all(s.installed for s in subs)

    def test_parents_before_children(self, superds: dict):
        subs = discover_via_gitmodules(superds["super"])
        rel_paths = [s.rel_path for s in subs]
        assert rel_paths.index("sub-01") < rel_paths.index("sub-01/derivatives")

    def test_depth_values(self, superds: dict):
        subs = discover_via_gitmodules(superds["super"])
        by_path = {s.rel_path: s for s in subs}
        assert by_path["sub-01"].depth == 0
        assert by_path["sub-02"].depth == 0
        assert by_path["sub-01/derivatives"].depth == 1

    def test_uninstalled_subdataset(self, superds: dict):
        """A subdataset whose directory has no .git is marked not installed."""
        sub02 = superds["sub02"]
        git_entry = sub02 / ".git"
        if git_entry.is_file():
            git_entry.unlink()
        elif git_entry.is_dir():
            shutil.rmtree(git_entry)

        subs = discover_via_gitmodules(superds["super"])
        by_path = {s.rel_path: s for s in subs}
        assert by_path["sub-02"].installed is False


class TestDiscoverViaDatalad:
    def test_discovers_all(self, superds: dict):
        subs = discover_via_datalad(superds["super"])
        rel_paths = [s.rel_path for s in subs]
        assert "sub-01" in rel_paths
        assert "sub-02" in rel_paths
        assert "sub-01/derivatives" in rel_paths

    def test_all_installed(self, superds: dict):
        subs = discover_via_datalad(superds["super"])
        assert all(s.installed for s in subs)

    def test_sorted_parents_first(self, superds: dict):
        subs = discover_via_datalad(superds["super"])
        rel_paths = [s.rel_path for s in subs]
        assert rel_paths.index("sub-01") < rel_paths.index("sub-01/derivatives")

    def test_no_subdatasets(self, datalad_ds: Path):
        subs = discover_via_datalad(datalad_ds)
        assert subs == []


class TestDiscoverSubdatasets:
    def test_prefers_datalad(self, superds: dict):
        subs = discover_subdatasets(superds["super"], prefer_datalad=True)
        rel_paths = [s.rel_path for s in subs]
        assert "sub-01" in rel_paths
        assert "sub-02" in rel_paths
        assert "sub-01/derivatives" in rel_paths

    def test_gitmodules_fallback(self, superds: dict):
        subs = discover_subdatasets(superds["super"], prefer_datalad=False)
        rel_paths = [s.rel_path for s in subs]
        assert "sub-01" in rel_paths
        assert "sub-02" in rel_paths
        assert "sub-01/derivatives" in rel_paths

    def test_both_backends_agree(self, superds: dict):
        """Both discovery backends find the same subdatasets."""
        dl = discover_subdatasets(superds["super"], prefer_datalad=True)
        gm = discover_subdatasets(superds["super"], prefer_datalad=False)
        assert sorted(s.rel_path for s in dl) == sorted(s.rel_path for s in gm)

    def test_no_subdatasets(self, datalad_ds: Path):
        subs = discover_subdatasets(datalad_ds, prefer_datalad=False)
        assert subs == []
