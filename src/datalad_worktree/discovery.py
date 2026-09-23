"""
Subdataset discovery via recursive .gitmodules parsing.
"""

from __future__ import annotations

import configparser
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
    def git_dir(self) -> Optional[Path]:
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
