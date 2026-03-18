"""
Add command: create nested git worktrees.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

from datalad_worktree.core import (
    WorktreeReport,
    WorktreeResult,
    git_branch_checked_out_at,
    git_branch_exists,
    validate_superds,
)
from datalad_worktree.discovery import SubDataset, discover_subdatasets, is_git_repo

logger = logging.getLogger(__name__)


def _git_worktree_add(
    repo_path: Path,
    dest_path: Path,
    branch: str,
    create_branch: bool = True,
    force: bool = False,
) -> tuple[WorktreeResult, str]:
    """Run `git worktree add` for a single repository."""
    cmd = ["git", "-C", str(repo_path), "worktree", "add"]

    if force:
        cmd.append("--force")

    if git_branch_exists(repo_path, branch):
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


def _preflight_check(
    superds_path: Path,
    worktree_root: Path,
    branch: str,
    subdatasets: list[SubDataset],
    create_branch: bool,
    force: bool,
) -> list[str]:
    """
    Check all datasets before creating any worktrees.

    Returns a list of error messages. Empty means all clear.
    """
    errors: list[str] = []

    # Check superds
    if worktree_root.exists() and not force:
        errors.append(
            f".: worktree root already exists: {worktree_root}"
        )
    else:
        conflict = git_branch_checked_out_at(superds_path, branch)
        if conflict is not None:
            errors.append(
                f".: branch '{branch}' is already checked out at {conflict}"
            )
        elif not create_branch and not git_branch_exists(superds_path, branch):
            errors.append(
                f".: branch '{branch}' does not exist and --no-create-branch was set"
            )

    # Check subdatasets
    for subds in subdatasets:
        if not subds.installed or not is_git_repo(subds.abs_path):
            continue  # will be skipped, no conflict possible

        conflict = git_branch_checked_out_at(subds.abs_path, branch)
        if conflict is not None:
            errors.append(
                f"{subds.rel_path}: branch '{branch}' is already checked out"
                f" at {conflict}"
            )
        elif not create_branch and not git_branch_exists(subds.abs_path, branch):
            errors.append(
                f"{subds.rel_path}: branch '{branch}' does not exist"
                " and --no-create-branch was set"
            )

    return errors


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

    Runs a pre-flight check before creating anything. If any non-skipped
    dataset would fail (e.g. branch already checked out elsewhere), no
    worktrees are created and errors are yielded as FAILED reports.

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

    # ── Discover subdatasets ─────────────────────────────────────────────
    subdatasets = discover_subdatasets(superds_path)

    # ── Pre-flight check ─────────────────────────────────────────────────
    if not dry_run:
        errors = _preflight_check(
            superds_path, worktree_root, branch, subdatasets,
            create_branch, force,
        )
        if errors:
            for err in errors:
                dataset_path, _, msg = err.partition(": ")
                source = superds_path if dataset_path == "." else superds_path / dataset_path
                dest = worktree_root if dataset_path == "." else worktree_root / dataset_path
                yield WorktreeReport(
                    dataset_path=dataset_path,
                    source=source,
                    destination=dest,
                    result=WorktreeResult.FAILED,
                    branch=branch,
                    message=msg,
                )
            return

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
