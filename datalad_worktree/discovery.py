"""
Subdataset discovery strategies.

Provides multiple backends for discovering nested subdatasets:
  1. DataLad API (preferred when datalad is available)
  2. .gitmodules recursive parsing (fallback)
"""

from __future__ import annotations

import configparser
import logging
import os
import subprocess
from dataclasses import dataclass, field
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


def discover_via_datalad(superds_path: Path) -> list[SubDataset]:
    """
    Discover subdatasets using the DataLad Python API.

    This is the most robust method as it handles all DataLad-specific edge cases.
    """
    try:
        from datalad.api import subdatasets as dl_subdatasets
        from datalad.distribution.dataset import Dataset
    except ImportError:
        raise RuntimeError(
            "DataLad is not installed. Install it with: pip install datalad"
        )

    logger.info("Discovering subdatasets via DataLad API")

    ds = Dataset(str(superds_path))
    results = dl_subdatasets(
        dataset=ds,
        recursive=True,
        recursion_limit=None,
        result_renderer="disabled",
        on_failure="ignore",
        return_type="generator",
    )

    subdatasets = []
    for res in results:
        if res.get("status") not in ("ok", "notneeded"):
            logger.debug("Skipping result with status %s: %s", res.get("status"), res)
            continue

        if res.get("type") != "dataset":
            continue

        sub_path = Path(res["path"])
        rel_path = sub_path.relative_to(superds_path)

        # Determine depth from the relative path
        depth = len(rel_path.parts) - 1  # approximate nesting

        installed = sub_path.exists() and (sub_path / ".git").exists()

        subdatasets.append(
            SubDataset(
                rel_path=str(rel_path),
                abs_path=sub_path,
                installed=installed,
                depth=depth,
            )
        )

    # Sort by path to ensure parents come before children
    subdatasets.sort(key=lambda sd: sd.rel_path)
    logger.info("Discovered %d subdataset(s) via DataLad", len(subdatasets))
    return subdatasets


def discover_via_gitmodules(
    superds_path: Path,
    _prefix: str = "",
    _depth: int = 0,
) -> list[SubDataset]:
    """
    Discover subdatasets by recursively parsing .gitmodules files.

    This is the fallback when DataLad is not available.
    """
    if _depth == 0:
        logger.info("Discovering subdatasets via .gitmodules parsing")

    subdatasets = []
    gitmodules_path = superds_path / ".gitmodules"

    if not gitmodules_path.is_file():
        return subdatasets

    # Parse .gitmodules using configparser
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
            # Recurse into this subdataset
            children = discover_via_gitmodules(
                full_path,
                _prefix=overall_rel,
                _depth=_depth + 1,
            )
            subdatasets.extend(children)
        else:
            logger.debug(
                "Subdataset '%s' is not installed; skipping recursive discovery",
                overall_rel,
            )

    if _depth == 0:
        logger.info(
            "Discovered %d subdataset(s) via .gitmodules", len(subdatasets)
        )

    return subdatasets


def discover_subdatasets(
    superds_path: Path,
    prefer_datalad: bool = True,
) -> list[SubDataset]:
    """
    Discover all subdatasets under a superdataset.

    Tries the DataLad API first, falls back to .gitmodules parsing.

    Parameters
    ----------
    superds_path : Path
        Absolute path to the superdataset root.
    prefer_datalad : bool
        Whether to try DataLad first (default True).

    Returns
    -------
    list[SubDataset]
        Sorted list of discovered subdatasets (parents before children).
    """
    if prefer_datalad:
        try:
            return discover_via_datalad(superds_path)
        except RuntimeError:
            logger.info("DataLad not available, falling back to .gitmodules parsing")
        except Exception as e:
            logger.warning(
                "DataLad discovery failed (%s), falling back to .gitmodules parsing",
                e,
            )

    gitmodules_path = superds_path / ".gitmodules"
    if not gitmodules_path.exists():
        logger.info("No .gitmodules found; assuming no subdatasets")
        return []

    return discover_via_gitmodules(superds_path)
