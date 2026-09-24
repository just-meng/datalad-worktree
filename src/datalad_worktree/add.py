"""
Add command: create nested git worktrees.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

from datalad_worktree.container import configure_dataset
from datalad_worktree.core import (
    WorktreeReport,
    WorktreeResult,
    git_branch_checked_out_at,
    git_branch_exists,
    git_current_branch,
    git_worktree_list,
    validate_superds,
)
from datalad_worktree.discovery import SubDataset, discover_subdatasets, is_git_repo
from datalad_worktree.fetch import fast_forward_state
from datalad_worktree.mtimes import sync_dataset

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


def existing_worktrees(
    superds_path: Path,
    worktree_root: Path,
    subdatasets: list[SubDataset],
) -> list[tuple[str, Path, Path]]:
    """
    ``(dataset_path, repo, worktree)`` for destinations git already knows.

    Only paths git reports as worktrees are listed. A stray directory that
    merely happens to sit at the destination is deliberately not included:
    ``--force`` replaces worktrees, it does not delete arbitrary directories.
    """
    found: list[tuple[str, Path, Path]] = []
    candidates = [(".", superds_path, worktree_root)]
    candidates += [
        (s.rel_path, s.abs_path, worktree_root / s.rel_path)
        for s in subdatasets if s.installed and is_git_repo(s.abs_path)
    ]
    for dataset_path, repo, destination in candidates:
        known = {entry.path.resolve() for entry in git_worktree_list(repo)}
        if destination.exists() and destination.resolve() in known:
            found.append((dataset_path, repo, destination))
    return found


def unmerged_worktrees(
    existing: list[tuple[str, Path, Path]],
) -> list[tuple[str, str]]:
    """
    Which existing worktrees still hold commits the main checkout lacks.

    Replacing one of those throws work away, so ``--force`` refuses and
    ``--force-unmerged`` is required. Merged means the worktree's tip is
    contained in its main checkout's HEAD -- ``up-to-date`` or ``behind`` --
    which is the state a ``worktree fetch`` leaves behind. A detached HEAD
    counts as unmerged, since there is no branch to reason about.
    """
    unmerged: list[tuple[str, str]] = []
    for dataset_path, repo, worktree in existing:
        state, branch = fast_forward_state(repo, worktree)
        if state in ("up-to-date", "behind"):
            continue
        detail = (
            "detached HEAD" if state == "no-branch"
            else f"'{branch}' has commits not in {git_current_branch(repo) or 'HEAD'}"
        )
        unmerged.append((
            dataset_path,
            f"{worktree} still holds unmerged work ({detail}); "
            f"fetch it first, or use --force-unmerged to discard it",
        ))
    return unmerged


def _preflight_check(
    superds_path: Path,
    worktree_root: Path,
    branch: str,
    subdatasets: list[SubDataset],
    create_branch: bool,
    replaced_root: Path | None = None,
) -> list[tuple[str, str]]:
    """
    Check all datasets before creating any worktrees.

    ``replaced_root`` names a worktree root that is being replaced, so that
    its own path and its own branch checkouts are not reported as conflicts.

    Returns a list of (dataset_path, error_message) pairs. Empty means all clear.
    """
    errors: list[tuple[str, str]] = []
    replacing = replaced_root is not None and replaced_root == worktree_root

    def conflicts_elsewhere(repo: Path, relative: str) -> Path | None:
        """A checkout of ``branch`` that is not the worktree being replaced."""
        at = git_branch_checked_out_at(repo, branch)
        if at is None:
            return None
        if replacing:
            mine = worktree_root if relative == "." else worktree_root / relative
            if at.resolve() == mine.resolve():
                return None
        return at

    # Check superds
    if worktree_root.exists() and not replacing:
        errors.append((".", f"worktree root already exists: {worktree_root}"))
    else:
        conflict = conflicts_elsewhere(superds_path, ".")
        if conflict is not None:
            errors.append(
                (".", f"branch '{branch}' is already checked out at {conflict}")
            )
        elif not create_branch and not git_branch_exists(superds_path, branch):
            errors.append(
                (".", f"branch '{branch}' does not exist and --no-create-branch was set")
            )

    # Check subdatasets
    for subds in subdatasets:
        if not subds.installed or not is_git_repo(subds.abs_path):
            continue  # will be skipped, no conflict possible

        conflict = conflicts_elsewhere(subds.abs_path, subds.rel_path)
        if conflict is not None:
            errors.append((
                subds.rel_path,
                f"branch '{branch}' is already checked out at {conflict}",
            ))
        elif not create_branch and not git_branch_exists(subds.abs_path, branch):
            errors.append((
                subds.rel_path,
                f"branch '{branch}' does not exist and --no-create-branch was set",
            ))

    return errors


def create_nested_worktrees(
    superds_path: Path,
    worktree_path: Path,
    branch: str,
    create_branch: bool = True,
    force: bool = False,
    force_unmerged: bool = False,
    dry_run: bool = False,
    configure_containers: bool = True,
    preserve_mtimes: bool = True,
) -> Iterator[WorktreeReport]:
    """
    Create nested git worktrees for a DataLad superdataset and all its subdatasets.

    Runs a pre-flight check before creating anything. If any non-skipped
    dataset would fail (e.g. branch already checked out elsewhere), no
    worktrees are created and errors are yielded as FAILED reports.

    ``force`` replaces worktrees that already exist at the destination,
    deleting their branches too so the recreate starts from the main
    checkout's state -- worktrees are meant to be disposable. It refuses,
    changing nothing, if any of them still holds commits the main checkout
    lacks; ``force_unmerged`` discards those as well. Uncommitted changes are
    always discarded, as ``worktree delete --force`` does.

    Unless ``configure_containers`` is False, every created worktree that
    registers a container gets bind-mount configuration so that
    ``datalad containers-run`` can reach the git-annex object store, which
    lives in the main repository outside the worktree.

    Unless ``preserve_mtimes`` is False, every created worktree then has the
    mtimes of its unchanged files copied over from the working tree it was
    made from. This runs last, and across all datasets at once, because the
    ordering it repairs is the *cross-dataset* one: checking the
    superdataset out before its subdatasets leaves every file in a ``code/``
    subdataset newer than every output derived from it.

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

    # (dataset_path, source working tree, worktree) for every one created
    created_worktrees: list[tuple[str, Path, Path]] = []

    # ── Discover subdatasets ─────────────────────────────────────────────
    subdatasets = discover_subdatasets(superds_path)

    # ── Replace an existing worktree ─────────────────────────────────────
    # Done before the pre-flight so that the path and branch conflicts it
    # would otherwise report are already gone.
    replaced_root: Path | None = None
    if force or force_unmerged:
        existing = existing_worktrees(superds_path, worktree_root, subdatasets)
        if existing:
            replaced_root = worktree_root
            if not force_unmerged:
                blocked = unmerged_worktrees(existing)
                if blocked:
                    for dataset_path, message in blocked:
                        yield WorktreeReport(
                            dataset_path=dataset_path,
                            source=superds_path,
                            destination=worktree_root / dataset_path,
                            result=WorktreeResult.FAILED,
                            branch=branch,
                            message=message,
                        )
                    return
            if dry_run:
                for dataset_path, _repo, destination in reversed(existing):
                    yield WorktreeReport(
                        dataset_path=dataset_path,
                        source=superds_path,
                        destination=destination,
                        result=WorktreeResult.SKIPPED_DRY_RUN,
                        branch=branch,
                        message=f"would replace the worktree at {destination}",
                    )
            else:
                from datalad_worktree.delete import delete_nested_worktrees
                yield from delete_nested_worktrees(
                    superds_path=superds_path,
                    target=str(worktree_root),
                    delete_branch=True,
                    force=True,
                )

    # ── Pre-flight check ─────────────────────────────────────────────────
    if not dry_run:
        errors = _preflight_check(
            superds_path, worktree_root, branch, subdatasets,
            create_branch, replaced_root,
        )
        if errors:
            for dataset_path, msg in errors:
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
        yield WorktreeReport(
            dataset_path=".",
            source=superds_path,
            destination=worktree_root,
            result=WorktreeResult.STARTING,
            branch=branch,
        )

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

        created_worktrees.append((".", superds_path, worktree_root))

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

        yield WorktreeReport(
            dataset_path=subds.rel_path,
            source=subds.abs_path,
            destination=dest_subds,
            result=WorktreeResult.STARTING,
            branch=branch,
        )

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

        if wt_result != WorktreeResult.FAILED:
            created_worktrees.append((subds.rel_path, subds.abs_path, dest_subds))

    # ── Configure container bind mounts ──────────────────────────────────
    if configure_containers and not dry_run:
        for dataset_path, _source, dest in created_worktrees:
            yield from configure_dataset(
                dataset_path=dataset_path,
                worktree_path=dest,
                main_superds=superds_path,
                branch=branch,
            )

    # ── Copy mtimes from the source working trees ────────────────────────
    # Last, so that anything the steps above wrote (the container step
    # commits .datalad/config) is already in place.
    if preserve_mtimes and not dry_run:
        for dataset_path, source, dest in created_worktrees:
            yield from sync_dataset(
                dataset_path=dataset_path,
                source=source,
                worktree_path=dest,
                branch=branch,
            )
