"""Shared fixtures for datalad-worktree tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from datalad.api import create, install, subdatasets
from datalad.distribution.dataset import Dataset


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in the given directory."""
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture()
def datalad_ds(tmp_path: Path) -> Path:
    """A simple DataLad dataset with one commit."""
    ds_path = tmp_path / "ds"
    create(path=str(ds_path), result_renderer="disabled")
    (ds_path / "README.md").write_text("init\n")
    ds = Dataset(str(ds_path))
    ds.save(message="initial commit", result_renderer="disabled")
    return ds_path


@pytest.fixture()
def superds(tmp_path: Path) -> dict:
    """A DataLad superdataset with nested subdatasets.

    Structure::

        super/
        ├── sub-01/          (subdataset)
        │   └── derivatives/ (subdataset nested inside sub-01)
        └── sub-02/          (subdataset)

    Returns a dict with keys: super, sub01, sub01_deriv, sub02, wt_location.
    """
    origins = tmp_path / "origins"

    # Create standalone datasets that will become subdatasets
    sub01_deriv = Dataset(
        create(path=str(origins / "sub-01-derivatives"), result_renderer="disabled").path
    )
    (sub01_deriv.pathobj / "data.txt").write_text("derivatives\n")
    sub01_deriv.save(message="add data", result_renderer="disabled")

    sub01 = Dataset(
        create(path=str(origins / "sub-01"), result_renderer="disabled").path
    )
    # Install derivatives as a subdataset of sub-01
    install(
        dataset=sub01,
        source=str(sub01_deriv.path),
        path="derivatives",
        result_renderer="disabled",
    )
    sub01.save(message="add derivatives subdataset", result_renderer="disabled")

    sub02 = Dataset(
        create(path=str(origins / "sub-02"), result_renderer="disabled").path
    )
    (sub02.pathobj / "data.txt").write_text("sub02\n")
    sub02.save(message="add data", result_renderer="disabled")

    # Create the superdataset and install subdatasets
    superds = Dataset(
        create(path=str(origins / "super"), result_renderer="disabled").path
    )
    install(
        dataset=superds,
        source=str(sub01.path),
        path="sub-01",
        result_renderer="disabled",
    )
    install(
        dataset=superds,
        source=str(sub02.path),
        path="sub-02",
        result_renderer="disabled",
    )
    superds.save(message="add subdatasets", result_renderer="disabled")

    # Recursively get (install) all nested subdatasets via DataLad
    subdatasets(
        dataset=superds,
        recursive=True,
        fulfilled=False,
        result_renderer="disabled",
        on_failure="ignore",
    )
    # Install the nested derivatives inside super/sub-01/
    sub01_in_super = Dataset(str(superds.pathobj / "sub-01"))
    install(
        dataset=sub01_in_super,
        source=str(sub01_deriv.path),
        path="derivatives",
        result_renderer="disabled",
        on_failure="ignore",
    )

    return {
        "super": superds.pathobj,
        "sub01": superds.pathobj / "sub-01",
        "sub01_deriv": superds.pathobj / "sub-01" / "derivatives",
        "sub02": superds.pathobj / "sub-02",
        "wt_location": tmp_path / "worktrees",
    }
