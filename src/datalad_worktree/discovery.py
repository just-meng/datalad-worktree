"""
Subdataset discovery via recursive .gitmodules parsing.
"""

from __future__ import annotations

import configparser
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SubDataset:
    """Represents a discovered subdataset."""

    # Path relative to the superdataset root
    rel_path: str
    # Absolute path to the subdataset
    abs_path: Path
    # Whether the subdataset is actually installed (has .git)
    installed: bool = True
    # Nested depth (0 = direct child of superds)
    depth: int = 0

    @property
    def git_dir(self) -> Path | None:
        """Return the .git dir/file path if installed."""
        if not self.installed:
            return None
        git_path = self.abs_path / ".git"
        if git_path.exists():
            return git_path
        return None

    def __repr__(self) -> str:
        status = "installed" if self.installed else "not installed"
        return f"SubDataset({self.rel_path!r}, {status}, depth={self.depth})"


def is_git_repo(path: Path) -> bool:
    """Check if a path is a valid git repository."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def is_git_repo_root(path: Path) -> bool:
    """
    Check whether a path is the *root* of a working tree.

    ``is_git_repo`` only asks whether git can find a repository from here,
    and git walks up -- so it answers True for an empty submodule mount
    point, reporting the enclosing superdataset. Anything that resolves one
    dataset against another needs this stricter question.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, NotADirectoryError):
        return False

    if result.returncode != 0:
        return False
    return Path(result.stdout.strip()).resolve() == path.resolve()


def discover_subdatasets(superds_path: Path) -> list[SubDataset]:
    """
    Discover all subdatasets under a superdataset by recursively
    parsing .gitmodules files.

    Parameters
    ----------
    superds_path : Path
        Absolute path to the superdataset root.

    Returns
    -------
    list[SubDataset]
        Sorted list of discovered subdatasets (parents before children).
    """
    gitmodules_path = superds_path / ".gitmodules"
    if not gitmodules_path.exists():
        return []

    return _discover_via_gitmodules(superds_path)


def _discover_via_gitmodules(
    superds_path: Path,
    _prefix: str = "",
    _depth: int = 0,
) -> list[SubDataset]:
    """
    Discover subdatasets by recursively parsing .gitmodules files.
    """
    subdatasets = []
    gitmodules_path = superds_path / ".gitmodules"

    if not gitmodules_path.is_file():
        return subdatasets

    config = configparser.ConfigParser()
    try:
        config.read(str(gitmodules_path))
    except configparser.Error as e:
        logger.warning("Failed to parse %s: %s", gitmodules_path, e)
        return subdatasets

    for section in config.sections():
        if not section.startswith('submodule "'):
            continue

        sub_rel = config.get(section, "path", fallback=None)
        if sub_rel is None:
            continue

        full_path = superds_path / sub_rel
        if _prefix:
            overall_rel = f"{_prefix}/{sub_rel}"
        else:
            overall_rel = sub_rel

        installed = full_path.exists() and (full_path / ".git").exists()

        subdatasets.append(
            SubDataset(
                rel_path=overall_rel,
                abs_path=full_path,
                installed=installed,
                depth=_depth,
            )
        )

        if installed:
            children = _discover_via_gitmodules(
                full_path,
                _prefix=overall_rel,
                _depth=_depth + 1,
            )
            subdatasets.extend(children)

    return subdatasets


# ── Discovery from a commit, rather than from the working tree ────────────────


@dataclass
class RecordedDataset:
    """A dataset as some *commit* records it, not as the checkout has it."""

    # Path relative to the superdataset root
    rel_path: str
    # Where its repository sits in the main checkout
    abs_path: Path
    # The commit its parent dataset records for it
    commit: str
    # rel_path of the dataset that registers it; "." is the superdataset
    parent: str
    # Nested depth (0 = direct child of the superdataset)
    depth: int
    # Whether a repository is actually present at abs_path, i.e. whether a
    # worktree can be made of it at all
    available: bool

    def __repr__(self) -> str:
        state = "available" if self.available else "not available"
        return (
            f"RecordedDataset({self.rel_path} @ {self.commit[:8]}, "
            f"parent={self.parent}, {state})"
        )


def _gitmodules_at(repo_path: Path, commit: str) -> list[str]:
    """Submodule paths registered in ``.gitmodules`` at ``commit``."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "show", f"{commit}:.gitmodules"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []  # no .gitmodules at that commit

    config = configparser.ConfigParser()
    try:
        config.read_string(result.stdout)
    except configparser.Error as exc:
        logger.warning("could not parse .gitmodules at %s: %s", commit, exc)
        return []

    paths = []
    for section in config.sections():
        if not section.startswith('submodule "'):
            continue
        path = config.get(section, "path", fallback=None)
        if path is not None:
            paths.append(path)
    return paths


def gitlink_at(repo_path: Path, commit: str, path: str) -> str | None:
    """
    The commit a dataset records for the subdataset at ``path``.

    ``<commit>:<path>`` resolves the gitlink -- a tree entry of mode 160000
    whose hash is a commit in the *other* repository. Returns None when the
    path is not registered at that commit.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", f"{commit}:{path}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def recorded_hierarchy(
    superds_path: Path,
    commit: str,
    _rel_prefix: str = "",
    _depth: int = 0,
) -> list[RecordedDataset]:
    """
    The subdatasets a superdataset commit records, and the commits it pins.

    Walks the *commit*, not the working tree: a subdataset added after
    ``commit`` is absent here, and one recorded at ``commit`` is included even
    if the checkout has moved on. The gitlink has to be read from each dataset's
    own parent, because ``<commit>:a/b`` cannot see inside the submodule tree
    at ``a`` -- so the walk descends one dataset at a time, using each child's
    recorded commit to read that child's own ``.gitmodules``.

    A child whose repository is missing from the checkout is still listed, with
    ``available=False``: the caller reports it rather than silently narrowing
    the hierarchy.
    """
    found: list[RecordedDataset] = []
    parent_rel = _rel_prefix or "."

    for path in _gitmodules_at(superds_path, commit):
        child_commit = gitlink_at(superds_path, commit, path)
        if child_commit is None:
            # Registered in .gitmodules but no gitlink in the tree.
            logger.debug("no gitlink for %s at %s", path, commit)
            continue

        rel_path = f"{_rel_prefix}/{path}" if _rel_prefix else path
        # abs_path is always relative to the *top* superdataset, which is where
        # _rel_prefix is rooted; superds_path here may be a nested dataset.
        abs_path = superds_path / path
        available = is_git_repo_root(abs_path)

        found.append(RecordedDataset(
            rel_path=rel_path,
            abs_path=abs_path,
            commit=child_commit,
            parent=parent_rel,
            depth=_depth,
            available=available,
        ))

        if available:
            found.extend(recorded_hierarchy(
                abs_path, child_commit, _rel_prefix=rel_path, _depth=_depth + 1,
            ))

    return found


def commit_present(repo_path: Path, commit: str) -> bool:
    """Whether ``commit`` is an object this repository actually has."""
    return subprocess.run(
        ["git", "-C", str(repo_path), "cat-file", "-e", f"{commit}^{{commit}}"],
        capture_output=True,
        text=True,
    ).returncode == 0
