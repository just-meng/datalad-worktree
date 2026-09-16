"""
End-to-end test: ``datalad containers-run`` inside a nested worktree.

This is the regression test for datalad/datalad-container#288 -- reading an
annexed input from inside a container fails in a worktree, because the annex
objects live in the main repository, outside the worktree directory.

The container runtime is simulated (see ``fake_container_runtime.py``); the
worktree, the dataset hierarchy, the annex symlinks and the mount namespace
are all real.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from datalad.distribution.dataset import Dataset

from datalad_worktree.add import create_nested_worktrees

pytest.importorskip("datalad_container", reason="datalad-container not installed")

RUNTIME = Path(__file__).parent / "fake_container_runtime.py"
CONTAINER = "testcont"
INPUT_FILE = "payload.bin"
OUTPUT_FILE = "copied.bin"
PAYLOAD = b"annexed payload\n"


def _unshare_works() -> bool:
    """User + mount namespaces are unavailable in some sandboxes."""
    try:
        return subprocess.run(
            ["unshare", "-Urm", "true"], capture_output=True
        ).returncode == 0
    except FileNotFoundError:
        return False


requires_namespaces = pytest.mark.skipif(
    not _unshare_works(), reason="unprivileged user/mount namespaces unavailable"
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True
    )


@pytest.fixture()
def container_ds(tmp_path: Path) -> dict:
    """
    A dataset with an annexed input file and a registered container.

    The "image" is a dummy file -- the fake runtime only checks that it
    exists, which is what a real runtime would do first as well.
    """
    from datalad.api import create

    ds_path = tmp_path / "ds"
    create(path=str(ds_path), result_renderer="disabled")

    (ds_path / INPUT_FILE).write_bytes(PAYLOAD)
    assert _git(ds_path, "annex", "add", INPUT_FILE).returncode == 0

    image = ds_path / ".datalad" / "environments" / CONTAINER / "image"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_text("not a real image\n")

    config_file = ds_path / ".datalad" / "config"
    _git(
        ds_path, "config", "-f", str(config_file),
        f"datalad.containers.{CONTAINER}.image",
        f".datalad/environments/{CONTAINER}/image",
    )
    # {img} and {cmd} are placeholders for datalad-container to fill in.
    _git(
        ds_path, "config", "-f", str(config_file),
        f"datalad.containers.{CONTAINER}.cmdexec",
        f"{sys.executable} {RUNTIME} exec {{img}} {{cmd}}",
    )
    _git(ds_path, "add", "-A")
    assert _git(ds_path, "commit", "-m", "dataset with container").returncode == 0

    return {"ds": ds_path, "worktree": tmp_path / "wt"}


def _containers_run(dataset: Path, hide: Path | None = None):
    """
    Run containers-run in ``dataset``, with ``hide`` invisible inside the
    container unless a bind mount brings it back.
    """
    from datalad.api import containers_run

    previous = os.environ.get("FAKE_CONTAINER_HIDE")
    os.environ["FAKE_CONTAINER_HIDE"] = str(hide) if hide else ""
    try:
        return containers_run(
            cmd=f"cp {INPUT_FILE} {OUTPUT_FILE}",
            container_name=CONTAINER,
            dataset=Dataset(str(dataset)),
            on_failure="ignore",
            return_type="list",
            result_renderer="disabled",
        )
    finally:
        if previous is None:
            os.environ.pop("FAKE_CONTAINER_HIDE", None)
        else:
            os.environ["FAKE_CONTAINER_HIDE"] = previous


def _run_status(results) -> str:
    for res in results:
        if res.get("action") in ("run", "containers-run"):
            return res.get("status", "")
    return ""


@requires_namespaces
class TestContainersRunInWorktree:
    def test_succeeds_with_bindpaths(self, container_ds: dict):
        """The whole point: containers-run works in a worktree."""
        list(
            create_nested_worktrees(
                superds_path=container_ds["ds"],
                worktree_path=container_ds["worktree"],
                branch="wt-branch",
            )
        )
        worktree = container_ds["worktree"]

        results = _containers_run(worktree, hide=container_ds["ds"])

        assert _run_status(results) == "ok", results
        assert (worktree / OUTPUT_FILE).read_bytes() == PAYLOAD

    def test_fails_without_bindpaths(self, container_ds: dict):
        """Without the configuration, the annexed input is unreachable.

        This pins the bug: if this ever starts passing, the fake runtime has
        stopped modelling the isolation and the test above proves nothing.
        """
        list(
            create_nested_worktrees(
                superds_path=container_ds["ds"],
                worktree_path=container_ds["worktree"],
                branch="wt-branch",
                configure_containers=False,
            )
        )
        worktree = container_ds["worktree"]

        results = _containers_run(worktree, hide=container_ds["ds"])

        assert _run_status(results) == "error", results
        assert not (worktree / OUTPUT_FILE).exists()

    def test_main_worktree_still_runs(self, container_ds: dict):
        """
        Configuring a worktree must not disturb the main checkout, where the
        dataset directory is also the working directory.
        """
        list(
            create_nested_worktrees(
                superds_path=container_ds["ds"],
                worktree_path=container_ds["worktree"],
                branch="wt-branch",
            )
        )
        results = _containers_run(container_ds["ds"])
        assert _run_status(results) == "ok", results
        assert (container_ds["ds"] / OUTPUT_FILE).read_bytes() == PAYLOAD


@requires_namespaces
class TestRerunPortability:
    def test_run_record_reruns_in_main_worktree(self, container_ds: dict):
        """
        A run record made in the worktree keeps ``{bindpaths}`` unexpanded.
        The committed empty fallback is what lets it rerun elsewhere.
        """
        from datalad.api import rerun

        list(
            create_nested_worktrees(
                superds_path=container_ds["ds"],
                worktree_path=container_ds["worktree"],
                branch="wt-branch",
            )
        )
        worktree = container_ds["worktree"]
        assert _run_status(_containers_run(worktree, hide=container_ds["ds"])) == "ok"

        run_commit = _git(worktree, "rev-parse", "HEAD").stdout.strip()
        record = _git(worktree, "log", "-1", "--format=%B").stdout
        assert "{bindpaths}" in record, "substitution should stay unexpanded"

        # Bring the worktree branch (run record + fallback commit) into the
        # main checkout, as shipping results back would.
        merge = _git(container_ds["ds"], "merge", "--no-edit", "wt-branch")
        assert merge.returncode == 0, merge.stderr

        results = rerun(
            revision=run_commit,
            dataset=Dataset(str(container_ds["ds"])),
            on_failure="ignore",
            return_type="list",
            result_renderer="disabled",
        )
        statuses = [r.get("status") for r in results if r.get("action") == "run"]
        assert "impossible" not in statuses, results
        assert statuses and all(s == "ok" for s in statuses), results
