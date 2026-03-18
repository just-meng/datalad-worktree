"""
Core logic for creating nested git worktrees.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from datalad_worktree.discovery import SubDataset, discover_subdatasets, is_git_repo

logger = logging.getLogger(__name__)


class WorktreeResult(Enum):
    """Outcome of a single worktree creation attempt."""
    CREATED = auto()
    CREATED_NEW_BRANCH = auto()
    SKIPPED_NOT_INSTALLED = auto()
    SKIPPED_NOT_GIT_REPO = auto()
    SKIPPED_DRY_RUN = auto()
    FAILED = auto()


@dataclass
class WorktreeReport:
    """Report for a single worktree operation."""
    dataset_path: str  # relative path (or "." for superds)
    source: Path
    destination: Path
    result: WorktreeResult
    branch: str
    message: str = ""


@dataclass
class WorktreeCreateResult:
    """Aggregate result for the entire nested worktree creation."""
    worktree_root: Path
    branch: str
    reports: list[WorktreeReport] = field(default_factory=list)

    @property
    def succeeded(self) -> list[WorktreeReport]:
        return [
            r for r in self.reports
            if r.result in (WorktreeResult.CREATED, WorktreeResult.CREATED_NEW_BRANCH)
        ]

    @property
    def skipped(self) -> list[WorktreeReport]:
        return [
            r for r in self.reports
            if r.result.name.startswith("SKIPPED")
        ]

    @property
    def failed(self) -> list[WorktreeReport]:
        return [r for r in self.reports if r.result == WorktreeResult.FAILED]

    @property
    def all_ok(self) -> bool:
        return len(self.failed) == 0

    def summary(self) -> str:
        lines = [
            f"Nested worktree creation summary:",
            f"  Root:       {self.worktree_root}",
            f"  Branch:     {self.branch}",
            f"  Succeeded:  {len(self.succeeded)}",
            f"  Skipped:    {len(self.skipped)}",
            f"  Failed:     {len(self.failed)}",
        ]
        if self.failed:
            lines.append("  Failures:")
            for r in self.failed:
                lines.append(f"    ✗ {r.dataset_path}: {r.message}")
        return "\n".join(lines)


def collect_worktree_reports(
    reports: Iterable[WorktreeReport],
    worktree_root: Path,
    branch: str,
) -> WorktreeCreateResult:
    """Collect an iterable of WorktreeReport into a WorktreeCreateResult."""
    result = WorktreeCreateResult(worktree_root=worktree_root, branch=branch)
    result.reports = list(reports)
    return result


def _branch_exists(repo_path: Path, branch: str) -> bool:
    """Check whether a branch exists in the given repository."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", branch],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _git_worktree_add(
    repo_path: Path,
    dest_path: Path,
    branch: str,
    create_branch: bool = True,
    force: bool = False,
) -> tuple[WorktreeResult, str]:
    """
    Run `git worktree add` for a single repository.

    Returns
    -------
    tuple[WorktreeResult, str]
        The result enum and a human-readable message.
    """
    cmd = ["git", "-C", str(repo_path), "worktree", "add"]

    if force:
        cmd.append("--force")

    branch_exists = _branch_exists(repo_path, branch)

    if branch_exists:
        cmd.extend([str(dest_path), branch])
        result_type = WorktreeResult.CREATED
    elif create_branch:
        cmd.extend(["-b", branch, str(dest_path)])
        result_type = WorktreeResult.CREATED_NEW_BRANCH
    else:
        return (
            WorktreeResult.FAILED,
            f"Branch '{branch}' does not exist and --no-create-branch was set",
        )

    logger.debug("Running: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return (WorktreeResult.FAILED, "git worktree add timed out after 120s")
    except FileNotFoundError:
        return (WorktreeResult.FAILED, "git executable not found")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        return (WorktreeResult.FAILED, f"git worktree add failed: {stderr}")

    return (result_type, "")


def _prepare_destination(dest_path: Path) -> None:
    """
    Prepare the destination path for a subdataset worktree.

    When the parent worktree is created, git may place a gitlink file or an
    empty directory at the subdataset mount point. We need to remove that
    so `git worktree add` can create its own directory.
    """
    if not dest_path.exists():
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        return

    if dest_path.is_file():
        content = dest_path.read_text().strip()
        if content.startswith("gitdir:"):
            logger.debug("Removing gitlink placeholder at %s", dest_path)
            dest_path.unlink()
        else:
            logger.debug("Removing file at %s (not a gitlink)", dest_path)
            dest_path.unlink()
    elif dest_path.is_dir():
        entries = list(dest_path.iterdir())
        if len(entries) == 0:
            logger.debug("Removing empty directory at %s", dest_path)
            dest_path.rmdir()
        elif len(entries) == 1 and entries[0].name == ".git":
            logger.debug("Removing .git entry and empty dir at %s", dest_path)
            if entries[0].is_file():
                entries[0].unlink()
            else:
                shutil.rmtree(entries[0])
            dest_path.rmdir()
        else:
            logger.warning(
                "Destination %s exists and is non-empty; git worktree add may fail",
                dest_path,
            )


def validate_superds(path: Path) -> Path:
    """
    Validate that the given path is the root of a git repository.

    Returns the resolved absolute path.

    Raises
    ------
    ValueError
        If the path is not a git repo root.
    """
    path = path.resolve()

    if not is_git_repo(path):
        raise ValueError(f"Not a git repository: {path}")

    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    toplevel = Path(result.stdout.strip()).resolve()
    if toplevel != path:
        raise ValueError(
            f"Not at repository root.\n"
            f"  Given path: {path}\n"
            f"  Repo root:  {toplevel}"
        )

    return path


def create_nested_worktrees(
    superds_path: Path,
    worktree_path: Path,
    branch: str,
    create_branch: bool = True,
    force: bool = False,
    dry_run: bool = False,
) -> Iterator[WorktreeReport]:
    """
    Create nested git worktrees for a DataLad superdataset and all its subdatasets.

    Yields WorktreeReport objects as each dataset is processed, enabling
    real-time progress display by callers.

    Parameters
    ----------
    superds_path : Path
        Path to the root of the superdataset. Must be the git repo root.
    worktree_path : Path
        Full path for the superdataset worktree. Subdataset worktrees are
        created as subdirectories mirroring the dataset hierarchy.
    branch : str
        Branch name to create or check out in each worktree.
    create_branch : bool
        If True (default), create the branch if it doesn't exist.
        If False, fail if the branch doesn't exist.
    force : bool
        Pass ``--force`` to ``git worktree add``.
    dry_run : bool
        If True, don't actually create anything; just report what would happen.

    Yields
    ------
    WorktreeReport
        One report per dataset (superdataset + each subdataset).

    Raises
    ------
    ValueError
        If ``superds_path`` is not a valid git repository root.
    """
    superds_path = validate_superds(superds_path)
    worktree_root = worktree_path.resolve()

    # ── Check existing worktree ──────────────────────────────────────────
    if worktree_root.exists() and not force and not dry_run:
        yield WorktreeReport(
            dataset_path=".",
            source=superds_path,
            destination=worktree_root,
            result=WorktreeResult.FAILED,
            branch=branch,
            message=f"Worktree root already exists: {worktree_root}. Use --force to overwrite.",
        )
        return

    # ── Discover subdatasets ─────────────────────────────────────────────
    subdatasets = discover_subdatasets(superds_path)

    # ── Create super dataset worktree ────────────────────────────────────
    if dry_run:
        yield WorktreeReport(
            dataset_path=".",
            source=superds_path,
            destination=worktree_root,
            result=WorktreeResult.SKIPPED_DRY_RUN,
            branch=branch,
        )
    else:
        worktree_root.parent.mkdir(parents=True, exist_ok=True)

        wt_result, wt_msg = _git_worktree_add(
            repo_path=superds_path,
            dest_path=worktree_root,
            branch=branch,
            create_branch=create_branch,
            force=force,
        )
        yield WorktreeReport(
            dataset_path=".",
            source=superds_path,
            destination=worktree_root,
            result=wt_result,
            branch=branch,
            message=wt_msg,
        )

        if wt_result == WorktreeResult.FAILED:
            return

    # ── Create subdataset worktrees ──────────────────────────────────────
    for subds in subdatasets:
        dest_subds = worktree_root / subds.rel_path

        if not subds.installed:
            yield WorktreeReport(
                dataset_path=subds.rel_path,
                source=subds.abs_path,
                destination=dest_subds,
                result=WorktreeResult.SKIPPED_NOT_INSTALLED,
                branch=branch,
                message="not installed",
            )
            continue

        if not is_git_repo(subds.abs_path):
            yield WorktreeReport(
                dataset_path=subds.rel_path,
                source=subds.abs_path,
                destination=dest_subds,
                result=WorktreeResult.SKIPPED_NOT_GIT_REPO,
                branch=branch,
                message="not a git repo",
            )
            continue

        if dry_run:
            yield WorktreeReport(
                dataset_path=subds.rel_path,
                source=subds.abs_path,
                destination=dest_subds,
                result=WorktreeResult.SKIPPED_DRY_RUN,
                branch=branch,
            )
            continue

        _prepare_destination(dest_subds)

        wt_result, wt_msg = _git_worktree_add(
            repo_path=subds.abs_path,
            dest_path=dest_subds,
            branch=branch,
            create_branch=create_branch,
            force=force,
        )

        yield WorktreeReport(
            dataset_path=subds.rel_path,
            source=subds.abs_path,
            destination=dest_subds,
            result=wt_result,
            branch=branch,
            message=wt_msg,
        )
