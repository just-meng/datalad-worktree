"""Tests for container bind-mount configuration of worktrees."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from datalad_worktree.add import create_nested_worktrees
from datalad_worktree.container import (
    PLACEHOLDER,
    SUBSTITUTION_KEY,
    add_placeholder,
    bind_option,
    check_bindable,
    find_containers,
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
    def test_inserts_before_img(self):
        new, reason = add_placeholder("singularity exec {img} {cmd}")
        assert new == "singularity exec {{bindpaths}} {img} {cmd}"
        assert reason == ""

    def test_inserts_only_once(self):
        new, _ = add_placeholder("run {img} then {img} again")
        assert new.count(PLACEHOLDER) == 1
        assert new.startswith("run {{bindpaths}} {img}")

    def test_preserves_existing_options(self):
        new, _ = add_placeholder("singularity exec -B {{pwd}} --cleanenv {img} {cmd}")
        assert new == "singularity exec -B {{pwd}} --cleanenv {{bindpaths}} {img} {cmd}"

    def test_already_configured(self):
        new, reason = add_placeholder("singularity exec {{bindpaths}} {img} {cmd}")
        assert new is None
        assert reason == "already configured"

    def test_no_anchor(self):
        new, reason = add_placeholder("mycontainer-wrapper {cmd}")
        assert new is None
        assert "{img}" in reason


class TestBindOption:
    def test_read_only_bind(self):
        assert bind_option(Path("/data/super")) == "-B /data/super:/data/super:ro"

    def test_plain_path_is_bindable(self):
        assert check_bindable(Path("/data/super")) == ""

    def test_whitespace_rejected(self):
        assert "whitespace" in check_bindable(Path("/data/my super"))

    def test_colon_rejected(self):
        assert "':'" in check_bindable(Path("/data/su:per"))


class TestFindContainers:
    def test_no_datalad_config(self, datalad_ds: Path):
        assert find_containers(datalad_ds) == {}

    def test_finds_registered_container(self, datalad_ds: Path):
        register_container(datalad_ds, "fissa")
        assert find_containers(datalad_ds) == {"fissa": DEFAULT_CMDEXEC}

    def test_finds_multiple(self, datalad_ds: Path):
        register_container(datalad_ds, "one")
        register_container(datalad_ds, "two", "podman run {img} {cmd}")
        assert set(find_containers(datalad_ds)) == {"one", "two"}

    def test_ignores_container_without_cmdexec(self, datalad_ds: Path):
        config_file = datalad_ds / ".datalad" / "config"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        _git(
            datalad_ds, "config", "-f", str(config_file),
            "datalad.containers.bare.image", ".datalad/environments/bare/image",
        )
        assert find_containers(datalad_ds) == {}


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
    def test_writes_worktree_scoped_config(self, superds: dict):
        register_container(superds["super"])
        _create(superds)

        worktree = superds["wt_location"]
        main = superds["super"]

        assert _git_value(worktree, "--get", SUBSTITUTION_KEY) == (
            f"-B {main}:{main}:ro"
        )
        assert _git_value(
            worktree, "--get", "datalad.containers.mycont.cmdexec"
        ) == f"singularity exec {PLACEHOLDER} {{img}} {{cmd}}"

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

    def test_enables_worktree_config_extension(self, superds: dict):
        register_container(superds["super"])
        _create(superds)
        assert _git_value(
            superds["wt_location"], "--local", "--get", "extensions.worktreeConfig"
        ) == "true"

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

    def test_fallback_not_committed_twice(self, superds: dict):
        register_container(superds["super"])
        _create(superds)
        worktree = superds["wt_location"]
        head = _git_value(worktree, "rev-parse", "HEAD")

        # Re-running configuration on the same worktree must be idempotent.
        from datalad_worktree.container import configure_dataset

        reports = list(
            configure_dataset(
                dataset_path=".",
                worktree_path=worktree,
                main_superds=superds["super"],
                branch="wt-branch",
            )
        )
        assert _git_value(worktree, "rev-parse", "HEAD") == head
        assert all(r.result != WorktreeResult.FAILED for r in reports)

    def test_reports_configured(self, superds: dict):
        register_container(superds["super"])
        reports = _create(superds)

        configured = [r for r in reports if r.result == WorktreeResult.CONFIGURED]
        messages = " ".join(r.message for r in configured)
        assert "mycont" in messages
        assert "fallback" in messages

    def test_silent_without_containers(self, superds: dict):
        reports = _create(superds)
        assert not [
            r for r in reports
            if r.result in (WorktreeResult.CONFIGURED, WorktreeResult.SKIPPED_CONTAINER)
        ]

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

    def test_configures_subdataset_worktrees(self, superds: dict):
        register_container(superds["sub01"], "subcont")
        _create(superds)

        sub_worktree = superds["wt_location"] / "sub-01"
        main = superds["super"]
        assert _git_value(sub_worktree, "--get", SUBSTITUTION_KEY) == (
            f"-B {main}:{main}:ro"
        )

    def test_disabled_by_flag(self, superds: dict):
        register_container(superds["super"])
        _create(superds, configure_containers=False)

        assert _git_value(
            superds["wt_location"], "--get", SUBSTITUTION_KEY
        ) is None

    def test_dry_run_writes_nothing(self, superds: dict):
        register_container(superds["super"])
        reports = _create(superds, dry_run=True)
        assert not [r for r in reports if r.result == WorktreeResult.CONFIGURED]
        assert not superds["wt_location"].exists()


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
