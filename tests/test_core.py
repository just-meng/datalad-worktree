"""Tests for core types and git helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from datalad_worktree.core import (
    validate_superds,
)


class TestValidateSuperds:

    def test_subdirectory_of_dataset(self, datalad_ds: Path):
        subdir = datalad_ds / "subdir"
        subdir.mkdir()
        with pytest.raises(ValueError, match="Not at repository root"):
            validate_superds(subdir)
