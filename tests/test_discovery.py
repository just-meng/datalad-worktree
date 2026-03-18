"""Tests for subdataset discovery."""

from __future__ import annotations

import shutil
from pathlib import Path

from datalad_worktree.discovery import (
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


class TestDiscoverSubdatasets:
    def test_discovers_all(self, superds: dict):
        subs = discover_subdatasets(superds["super"])
        rel_paths = [s.rel_path for s in subs]
        assert "sub-01" in rel_paths
        assert "sub-02" in rel_paths
        assert "sub-01/derivatives" in rel_paths

    def test_all_installed(self, superds: dict):
        subs = discover_subdatasets(superds["super"])
        assert all(s.installed for s in subs)

    def test_parents_before_children(self, superds: dict):
        subs = discover_subdatasets(superds["super"])
        rel_paths = [s.rel_path for s in subs]
        assert rel_paths.index("sub-01") < rel_paths.index("sub-01/derivatives")

    def test_depth_values(self, superds: dict):
        subs = discover_subdatasets(superds["super"])
        by_path = {s.rel_path: s for s in subs}
        assert by_path["sub-01"].depth == 0
        assert by_path["sub-02"].depth == 0
        assert by_path["sub-01/derivatives"].depth == 1

    def test_no_subdatasets(self, datalad_ds: Path):
        subs = discover_subdatasets(datalad_ds)
        assert subs == []

    def test_uninstalled_subdataset(self, superds: dict):
        """A subdataset whose directory has no .git is marked not installed."""
        sub02 = superds["sub02"]
        git_entry = sub02 / ".git"
        if git_entry.is_file():
            git_entry.unlink()
        elif git_entry.is_dir():
            shutil.rmtree(git_entry)

        subs = discover_subdatasets(superds["super"])
        by_path = {s.rel_path: s for s in subs}
        assert by_path["sub-02"].installed is False
