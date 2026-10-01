"""Tests for container bind-mount configuration of worktrees."""

from __future__ import annotations

import subprocess
from pathlib import Path

from datalad_worktree.add import create_nested_worktrees
from datalad_worktree.container import (
    PLACEHOLDER,
    SUBSTITUTION_KEY,
    add_placeholder,
    bind_option,
    check_bindable,
)
from datalad_worktree.core import WorktreeResult

DEFAULT_CMDEXEC = "singularity exec {img} {cmd}"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True
    )


def _git_value(cwd: Path, *args: str) -> str | None:
    """Read a git config value, or None if unset."""
    result = _git(cwd, "config", *args)
    return result.stdout.strip() if result.returncode == 0 else None


def register_container(
    ds_path: Path, name: str = "mycont", cmdexec: str = DEFAULT_CMDEXEC
) -> None:
    """Register a container in a dataset's committed .datalad/config."""
    config_file = ds_path / ".datalad" / "config"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    _git(
        ds_path, "config", "-f", str(config_file),
        f"datalad.containers.{name}.image", f".datalad/environments/{name}/image",
    )
    _git(
        ds_path, "config", "-f", str(config_file),
        f"datalad.containers.{name}.cmdexec", cmdexec,
    )
    _git(ds_path, "add", ".datalad/config")
    commit = _git(ds_path, "commit", "-m", f"register container {name}")
    assert commit.returncode == 0, commit.stderr


# ── Pure helpers ─────────────────────────────────────────────────────────────


class TestAddPlaceholder:
    def test_inserts_before_img_keeping_existing_options(self):
        new, reason = add_placeholder("singularity exec -B {{pwd}} --cleanenv {img} {cmd}")
        assert new == "singularity exec -B {{pwd}} --cleanenv {{bindpaths}} {img} {cmd}"
        assert reason == ""

    def test_already_configured(self):
        new, reason = add_placeholder("singularity exec {{bindpaths}} {img} {cmd}")
        assert new is None
        assert reason == "cmdexec already contains {{bindpaths}}"


def test_unbindable_paths_are_rejected():
    """A bind spec is ``src:dst:ro`` split on whitespace, so neither may appear."""
    assert "whitespace" in check_bindable(Path("/data/my super"))
    assert "':'" in check_bindable(Path("/data/su:per"))


# ── Configuration of created worktrees ───────────────────────────────────────


def _create(superds: dict, branch: str = "wt-branch", **kwargs):
    """Run create_nested_worktrees and return the list of reports."""
    return list(
        create_nested_worktrees(
            superds_path=superds["super"],
            worktree_path=superds["wt_location"],
            branch=branch,
            **kwargs,
        )
    )


class TestConfigureWorktree:

    def test_main_worktree_untouched(self, superds: dict):
        register_container(superds["super"])
        main = superds["super"]
        head_before = _git_value(main, "rev-parse", "HEAD")

        _create(superds)

        # The machine-specific value must not leak into the main worktree,
        # where the dataset path is also the working directory.
        assert _git_value(main, "--get", SUBSTITUTION_KEY) is None
        # ... and the committed cmdexec is the one the user registered.
        assert _git_value(main, "--get", "datalad.containers.mycont.cmdexec") is None
        assert _git_value(main, "rev-parse", "HEAD") == head_before

    def test_commits_fallback_on_worktree_branch(self, superds: dict):
        register_container(superds["super"])
        _create(superds)

        worktree = superds["wt_location"]
        config_file = worktree / ".datalad" / "config"

        # Empty fallback present in the tracked config ...
        result = _git(
            worktree, "config", "-f", str(config_file), "--get", SUBSTITUTION_KEY
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

        # ... committed, so the worktree is left clean.
        status = _git(worktree, "status", "--porcelain", "--", ".datalad/config")
        assert status.stdout.strip() == ""

    def test_skips_cmdexec_without_anchor(self, superds: dict):
        register_container(superds["super"], cmdexec="wrapper.sh {cmd}")
        reports = _create(superds)

        skipped = [
            r for r in reports if r.result == WorktreeResult.SKIPPED_CONTAINER
        ]
        assert len(skipped) == 1
        assert "{img}" in skipped[0].message
        assert _git_value(
            superds["wt_location"], "--get", "datalad.containers.mycont.cmdexec"
        ) is None

    def test_subdataset_containers_are_not_configured(self, superds: dict):
        """
        Only the superdataset is configured (issue #27).

        The value written is the superdataset's own path, so it is a
        superdataset-level concern -- and writing it into a subdataset commits
        `.datalad/config` on that subdataset's branch, moving it past the
        commit the superdataset just recorded and leaving the fresh worktree
        with a dirty gitlink.
        """
        register_container(superds["sub01"], "subcont")
        reports = _create(superds)

        sub_worktree = superds["wt_location"] / "sub-01"
        assert _git_value(sub_worktree, "--get", SUBSTITUTION_KEY) is None
        assert not [r for r in reports
                    if r.result == WorktreeResult.CONFIGURED
                    and r.dataset_path != "."]

    def test_a_subdataset_container_leaves_the_worktree_clean(self, superds: dict):
        """
        The side-effect the rule above exists to prevent.

        Registering the container commits inside the subdataset, which moves it
        past the gitlink the superdataset records -- so that is recorded first,
        making the main checkout clean. Any dirt in the fresh worktree is then
        ours.
        """
        register_container(superds["sub01"], "subcont")
        _git(superds["super"], "commit", "-qam", "record sub-01")
        assert _git(superds["super"], "status", "--short").stdout == ""

        _create(superds)

        status = _git(superds["wt_location"], "status", "--short").stdout
        assert status == "", status

    def test_disabled_by_flag(self, superds: dict):
        register_container(superds["super"])
        _create(superds, configure_containers=False)

        assert _git_value(
            superds["wt_location"], "--get", SUBSTITUTION_KEY
        ) is None


class TestDataladResolution:
    """The config is only useful if DataLad's own resolution picks it up."""

    def test_worktree_override_wins_in_worktree(self, superds: dict):
        from datalad.distribution.dataset import Dataset

        register_container(superds["super"])
        _create(superds)

        worktree_cfg = Dataset(str(superds["wt_location"])).config
        main_cfg = Dataset(str(superds["super"])).config

        assert PLACEHOLDER in worktree_cfg.get("datalad.containers.mycont.cmdexec")
        assert worktree_cfg.get(SUBSTITUTION_KEY) == bind_option(superds["super"])

        # In the main worktree the committed value still stands, and the
        # substitution resolves to the empty fallback (or nothing at all,
        # since the fallback commit lives on the worktree branch).
        assert main_cfg.get("datalad.containers.mycont.cmdexec") == DEFAULT_CMDEXEC
        assert not main_cfg.get(SUBSTITUTION_KEY)
