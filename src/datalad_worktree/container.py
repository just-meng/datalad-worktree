"""
Container bind-mount configuration for worktrees.

``datalad containers-run`` cannot resolve git-annex object symlinks inside a
linked worktree: they point through ``<main>/.git/worktrees/<name>/annex``
into the main repository, which lies outside the worktree and is therefore
not visible inside the container. Binding the main superdataset into the
container fixes it.

See https://github.com/datalad/datalad-container/issues/288

Three config writes per dataset that registers a container:

1. ``datalad.run.substitutions.bindpaths`` -- worktree-scoped, holds the
   machine-specific ``-B`` option.
2. ``datalad.containers.<name>.cmdexec`` -- worktree-scoped, the committed
   value with ``{{bindpaths}}`` inserted so it survives ``containers_run``'s
   ``callspec.format()`` and reaches ``run``'s formatter.
3. ``datalad.run.substitutions.bindpaths = ""`` in the tracked
   ``.datalad/config``, committed. ``run`` records the command with
   substitutions *unexpanded*, so without this fallback every run record
   made in a worktree fails to rerun anywhere else.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

from datalad_worktree.core import WorktreeReport, WorktreeResult

logger = logging.getLogger(__name__)

SUBSTITUTION_KEY = "datalad.run.substitutions.bindpaths"
PLACEHOLDER = "{{bindpaths}}"
IMG_ANCHOR = "{img}"
DATALAD_CONFIG = Path(".datalad") / "config"
FALLBACK_COMMIT_MSG = (
    "Add empty bindpaths substitution\n\n"
    "Keeps run records made in a worktree rerunnable where no worktree\n"
    "bind-mount configuration exists. See datalad/datalad-container#288."
)

_CMDEXEC_RE = re.compile(r"^datalad\.containers\.(?P<name>.+)\.cmdexec$")
_TRUE_VALUES = frozenset({"true", "1", "yes", "on"})


def _git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in ``repo_path``."""
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
    )


def find_containers(dataset_path: Path) -> dict[str, str]:
    """
    Map container name -> cmdexec, read from the dataset's ``.datalad/config``.

    Only containers that declare a ``cmdexec`` are returned. A container
    registered without one (``containers-add`` guesses none when no URL is
    given) has no command line to amend.
    """
    config_file = dataset_path / DATALAD_CONFIG
    if not config_file.is_file():
        return {}

    result = _git(
        dataset_path,
        "config", "-f", str(config_file),
        "--get-regexp", r"^datalad\.containers\..*\.cmdexec$",
    )
    if result.returncode != 0:
        return {}

    containers: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        match = _CMDEXEC_RE.match(key)
        if match:
            containers[match.group("name")] = value
    return containers


def bind_option(main_path: Path) -> str:
    """Build the read-only bind option for the main superdataset."""
    return f"-B {main_path}:{main_path}:ro"


def check_bindable(main_path: Path) -> str:
    """
    Check that a path can be expressed as a bind option.

    Returns an error message, or an empty string if all is well.
    """
    text = str(main_path)
    if any(char.isspace() for char in text):
        return f"path contains whitespace, cannot build a bind option: {text}"
    if ":" in text:
        return f"path contains ':', cannot build a bind option: {text}"
    return ""


def add_placeholder(cmdexec: str) -> tuple[str | None, str]:
    """
    Insert ``{{bindpaths}}`` into a cmdexec, just before ``{img}``.

    Returns ``(new_cmdexec, "")`` on success, or ``(None, reason)`` when
    there is nothing to do or no anchor to insert at.
    """
    if PLACEHOLDER in cmdexec:
        return None, f"cmdexec already contains {PLACEHOLDER}"
    if IMG_ANCHOR not in cmdexec:
        return None, f"no {IMG_ANCHOR} anchor in cmdexec"
    return cmdexec.replace(IMG_ANCHOR, f"{PLACEHOLDER} {IMG_ANCHOR}", 1), ""


def _enable_worktree_config(worktree_path: Path) -> str:
    """
    Enable ``extensions.worktreeConfig`` unless already on.

    Required before ``git config --worktree`` can be used on a repository
    with more than one working tree. Returns an error message, or "".
    """
    current = _git(
        worktree_path, "config", "--local", "--get", "extensions.worktreeConfig"
    )
    if current.returncode == 0 and current.stdout.strip().lower() in _TRUE_VALUES:
        return ""

    result = _git(
        worktree_path, "config", "--local", "extensions.worktreeConfig", "true"
    )
    if result.returncode != 0:
        return f"could not enable extensions.worktreeConfig: {result.stderr.strip()}"
    return ""


def _set_worktree_config(worktree_path: Path, key: str, value: str) -> str:
    """Write a worktree-scoped config value. Returns an error message, or ""."""
    result = _git(worktree_path, "config", "--worktree", key, value)
    if result.returncode != 0:
        return f"could not set {key}: {result.stderr.strip()}"
    return ""


def _commit_fallback(worktree_path: Path) -> tuple[bool, str]:
    """
    Ensure the tracked ``.datalad/config`` defines the bindpaths substitution.

    Writes an empty value and commits it on the worktree's branch, so that
    run records produced here stay rerunnable once merged back.

    Returns ``(committed, error_message)``.
    """
    config_file = worktree_path / DATALAD_CONFIG

    existing = _git(
        worktree_path, "config", "-f", str(config_file), "--get", SUBSTITUTION_KEY
    )
    if existing.returncode == 0:
        return False, ""  # already defined, nothing to commit

    config_file.parent.mkdir(parents=True, exist_ok=True)
    result = _git(
        worktree_path, "config", "-f", str(config_file), SUBSTITUTION_KEY, ""
    )
    if result.returncode != 0:
        return False, f"could not write {config_file}: {result.stderr.strip()}"

    # Path-limited commit: takes the working-tree state of this one file and
    # leaves anything else the user may have staged untouched.
    commit = _git(
        worktree_path,
        "commit", "-m", FALLBACK_COMMIT_MSG, "--", str(DATALAD_CONFIG),
    )
    if commit.returncode != 0:
        return False, f"could not commit {DATALAD_CONFIG}: {commit.stderr.strip()}"
    return True, ""


def configure_dataset(
    dataset_path: str,
    worktree_path: Path,
    main_superds: Path,
    branch: str,
) -> Iterator[WorktreeReport]:
    """
    Configure container bind mounts for a single freshly created worktree.

    Yields nothing at all when the dataset registers no container with a
    ``cmdexec`` -- the overwhelmingly common case, which should stay silent.
    """
    containers = find_containers(worktree_path)
    if not containers:
        return

    def report(result: WorktreeResult, message: str) -> WorktreeReport:
        return WorktreeReport(
            dataset_path=dataset_path,
            source=main_superds,
            destination=worktree_path,
            result=result,
            branch=branch,
            message=message,
        )

    error = check_bindable(main_superds)
    if error:
        yield report(WorktreeResult.FAILED, error)
        return

    error = _enable_worktree_config(worktree_path)
    if error:
        yield report(WorktreeResult.FAILED, error)
        return

    error = _set_worktree_config(
        worktree_path, SUBSTITUTION_KEY, bind_option(main_superds)
    )
    if error:
        yield report(WorktreeResult.FAILED, error)
        return

    for name in sorted(containers):
        new_cmdexec, reason = add_placeholder(containers[name])
        if new_cmdexec is None:
            yield report(WorktreeResult.SKIPPED_CONTAINER, f"{name}: {reason}")
            continue

        error = _set_worktree_config(
            worktree_path, f"datalad.containers.{name}.cmdexec", new_cmdexec
        )
        if error:
            yield report(WorktreeResult.FAILED, error)
            continue

        yield report(
            WorktreeResult.CONFIGURED,
            f"{name}: bound {main_superds} read-only",
        )

    committed, error = _commit_fallback(worktree_path)
    if error:
        yield report(WorktreeResult.FAILED, error)
    elif committed:
        yield report(
            WorktreeResult.CONFIGURED,
            f"committed {SUBSTITUTION_KEY} fallback for reruns",
        )
