"""Tests for subdataset discovery."""

from __future__ import annotations

from pathlib import Path

from datalad_worktree.discovery import (
    is_git_repo,
    is_git_repo_root,
)


class TestIsGitRepoRoot:

    def test_false_inside_a_repo(self, datalad_ds: Path):
        """
        The distinction from is_git_repo, which git answers by walking up.

        An uninstalled subdataset is an empty mount point; asking
        is_git_repo there reports the enclosing superdataset, which would
        silently resolve one dataset against another.
        """
        inside = datalad_ds / "subdir"
        inside.mkdir()

        assert is_git_repo(inside)          # git walked up to datalad_ds
        assert not is_git_repo_root(inside)
