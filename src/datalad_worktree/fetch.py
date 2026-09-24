"""
Ship a worktree's commits back into the main checkout.

The counterpart of ``add``: you create a worktree, run the long pipeline
there, and then want the results in the main checkout with the mtimes the
pipeline produced.

Plain ``git merge``/``git rebase`` gets the *content* right and the mtimes
wrong. Git moves only what it has to rewrite, so:

- an output whose content changed has its file and its immediate parent
  directory updated -- git wrote them;
- the grandparent directory is never updated, because a directory's mtime
  tracks only its own entries and a pipeline usually writes two levels down;
- an output that came out byte-identical produces no commit at all, so
  nothing is shipped and nothing moves.

Snakemake (and Make, and redo) decide staleness by mtime, so in the second
and third cases freshly computed results look stale and re-run. This module
therefore does the transport and then copies the mtimes back out of the
worktree, reusing ``mtimes.sync_dataset`` with the direction reversed: the
worktree is the reference, the main checkout is the target. Matching on blob
OID means only byte-identical content inherits an mtime, so the update can
never claim freshness for content the main checkout does not have.

**Fast-forward only, for now.** A real merge across a hierarchy can conflict
on submodule gitlinks, and such a conflict does not resolve by re-saving the
superdataset -- it leaves the superdataset pointing at a commit its own
subdataset checkout does not have. Divergence is therefore refused, and every
dataset is judged up front so that a refusal touches nothing.

Fast-forward is also what makes shipping into a *dirty* checkout safe, which
``git rebase`` -- the obvious alternative -- cannot do. Rebase replays
commits and so demands a clean tree unconditionally, even when the modified
files have nothing to do with it. A fast-forward only moves ``HEAD`` and
rewrites the paths that differ, so it needs only *those* paths clean, and it
refuses without changing anything when they are not. When the checkout is
strictly behind, both land on the same commit anyway. The pre-flight
therefore checks for that overlap rather than for a clean tree, which lets
work continue in the main checkout -- development in a ``code/``
subdataset, say -- while results are shipped in from a worktree.

See https://github.com/just-meng/datalad-worktree/issues/21
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from datalad_worktree.core import (
    WorktreeReport,
    WorktreeResult,
    git_current_branch,
    validate_superds,
)
from datalad_worktree.discovery import discover_subdatasets, is_git_repo_root
from datalad_worktree.mtimes import dirty_paths, sync_dataset

logger = logging.getLogger(__name__)


def _git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in ``repo_path``."""
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
    )


@dataclass
class DatasetPair:
    """One dataset, as it exists on both sides of a fetch."""
    dataset_path: str  # "." for the superdataset, else the relative path
    main: Path         # the checkout being fetched into
    worktree: Path     # the worktree being shipped from


def dataset_pairs(main_root: Path, worktree_root: Path) -> list[DatasetPair]:
    """
    Every dataset to fetch, superdataset first then subdatasets by path.

    Subdatasets are discovered from the *worktree*, since that is the side
    that knows what the pipeline actually worked on. A subdataset missing or
    uninitialised on either side is dropped: there is nothing to ship.
    """
    pairs = [DatasetPair(".", main_root, worktree_root)]
    for subds in discover_subdatasets(worktree_root):
        main = main_root / subds.rel_path
        worktree = worktree_root / subds.rel_path
        if not subds.installed or not is_git_repo_root(worktree):
            continue
        if not is_git_repo_root(main):
            continue
        pairs.append(DatasetPair(subds.rel_path, main, worktree))
    return pairs


def incoming_paths(main: Path, branch: str) -> set[str]:
    """
    Tracked paths that bringing ``branch`` into ``main`` would rewrite.

    Compared against the merge base rather than ``HEAD``, so that for a
    diverged pair it lists what the *branch* changed and not what this side
    changed. For a fast-forward the merge base is ``HEAD`` and the two are
    the same thing.
    """
    base = _git(main, "merge-base", "HEAD", branch).stdout.strip() or "HEAD"
    result = _git(main, "diff", "--name-only", "-z", f"{base}..{branch}")
    if result.returncode != 0:
        return set()
    return {path for path in result.stdout.split("\0") if path}


def fast_forward_state(main: Path, worktree: Path) -> tuple[str, str]:
    """
    How ``main`` stands relative to what ``worktree`` has checked out.

    Returns ``(state, branch)``:

    ``ready``
        The worktree is strictly ahead; a fast-forward will bring it in.
    ``up-to-date``
        Both sides are on the same commit.
    ``behind``
        The worktree's tip is already contained in ``main`` -- typically
        because work continued in the main checkout while the worktree sat
        still. There is nothing to ship, which is not an error.
    ``diverged``
        Both sides have commits the other lacks. Needs a real merge, which
        this command does not attempt.
    ``no-branch``
        The worktree is on a detached HEAD; there is no branch to bring in.
    """
    branch = git_current_branch(worktree)
    if not branch:
        return "no-branch", ""

    tip = _git(worktree, "rev-parse", "HEAD")
    head = _git(main, "rev-parse", "HEAD")
    if tip.returncode != 0 or head.returncode != 0:
        return "no-branch", branch
    tip_oid, head_oid = tip.stdout.strip(), head.stdout.strip()

    if tip_oid == head_oid:
        return "up-to-date", branch

    # Fast-forwardable exactly when the main tip is an ancestor of the
    # worktree tip: the worktree is strictly ahead.
    if _git(main, "merge-base", "--is-ancestor", head_oid, tip_oid).returncode == 0:
        return "ready", branch

    # The mirror case: the worktree is strictly behind, so it has nothing
    # this checkout lacks. Common with a `code/` subdataset that is consumed
    # in the worktree while development continues in the main checkout.
    if _git(main, "merge-base", "--is-ancestor", tip_oid, head_oid).returncode == 0:
        return "behind", branch

    return "diverged", branch


def commits_ahead(main: Path, branch: str) -> int:
    """How many commits ``branch`` carries that ``main``'s HEAD does not."""
    result = _git(main, "rev-list", "--count", f"HEAD..{branch}")
    return int(result.stdout.strip()) if result.returncode == 0 else 0


def collisions(main: Path, branch: str) -> set[str]:
    """
    Paths the fetch would rewrite that also have uncommitted changes.

    A dirty working tree is *not* by itself a reason to refuse. ``git merge
    --ff-only`` updates a dirty checkout happily as long as the incoming
    commits do not touch the modified paths, and it refuses without changing
    anything when they do. Only that overlap is a real obstacle, and only it
    is reported -- which is what lets the main checkout carry unrelated
    work-in-progress (say, development in a ``code/`` subdataset) while
    results are shipped in from a worktree.
    """
    return incoming_paths(main, branch) & dirty_paths(main)


def preflight(pairs: list[DatasetPair]) -> list[tuple[str, str]]:
    """
    Check every dataset before touching any of them.

    Returns ``(dataset_path, error_message)`` pairs; empty means all clear.
    """
    errors: list[tuple[str, str]] = []
    for pair in pairs:
        state, branch = fast_forward_state(pair.main, pair.worktree)

        if state == "no-branch":
            errors.append((
                pair.dataset_path,
                f"{pair.worktree} has a detached HEAD, no branch to bring in",
            ))
            continue

        if state in ("up-to-date", "behind"):
            continue  # nothing to bring in, so nothing can collide

        if state == "diverged":
            errors.append((
                pair.dataset_path,
                f"'{branch}' has diverged from "
                f"{git_current_branch(pair.main) or 'HEAD'}; merge it "
                f"yourself, then re-run to refresh mtimes",
            ))
            continue

        clash = collisions(pair.main, branch)
        if clash:
            errors.append((
                pair.dataset_path,
                f"fetching would overwrite uncommitted changes in: "
                f"{_listed(clash)}; commit, stash or discard them first",
            ))
    return errors


def _listed(paths: set[str], limit: int = 5) -> str:
    """Render a path set for an error message, without dumping hundreds."""
    shown = ", ".join(sorted(paths)[:limit])
    extra = len(paths) - limit
    return f"{shown} (+{extra} more)" if extra > 0 else shown


def fetch_nested_worktrees(
    main_path: Path,
    worktree_path: Path,
    dry_run: bool = False,
    preserve_mtimes: bool = True,
) -> Iterator[WorktreeReport]:
    """
    Fast-forward a worktree's commits into the main checkout, mtimes included.

    Datasets are merged deepest-first so that when a superdataset's gitlink
    arrives, the subdataset commit it names is already present locally.
    The mtime refresh runs afterwards, across the whole hierarchy.

    Parameters
    ----------
    main_path : Path
        Root of the checkout to fetch into.
    worktree_path : Path
        Root of the worktree to ship from.
    dry_run : bool
        Report what would happen, change nothing.
    preserve_mtimes : bool
        Copy mtimes from the worktree afterwards. Without this the update is
        content-correct but leaves results looking stale.

    Yields
    ------
    WorktreeReport
        One or more per dataset.

    Raises
    ------
    ValueError
        If either side is not a dataset root, or they are the same tree.
    """
    main_root = validate_superds(main_path)
    worktree_root = validate_superds(worktree_path)
    if main_root == worktree_root:
        raise ValueError(f"{worktree_root} is its own target; nothing to fetch")

    pairs = dataset_pairs(main_root, worktree_root)

    def report(pair: DatasetPair, result: WorktreeResult, branch: str,
               message: str = "") -> WorktreeReport:
        return WorktreeReport(
            dataset_path=pair.dataset_path,
            source=pair.worktree,
            destination=pair.main,
            result=result,
            branch=branch,
            message=message,
        )

    # ── Pre-flight: all-or-nothing, before anything is touched ───────────
    errors = preflight(pairs)
    if errors:
        by_path = {pair.dataset_path: pair for pair in pairs}
        for dataset_path, message in errors:
            yield report(by_path[dataset_path], WorktreeResult.FAILED, "", message)
        return

    # ── Transport, deepest-first so gitlink targets exist locally ────────
    for pair in reversed(pairs):
        state, branch = fast_forward_state(pair.main, pair.worktree)
        if state in ("up-to-date", "behind"):
            # "behind" is the normal state of a dataset the worktree only
            # consumed -- a `code/` subdataset developed further in the main
            # checkout while the run was going on. Nothing to bring in.
            message = ("already up to date" if state == "up-to-date"
                       else "nothing to ship; this checkout is ahead")
            yield report(pair, WorktreeResult.SKIPPED_UP_TO_DATE, branch, message)
            continue

        ahead = commits_ahead(pair.main, branch)
        if dry_run:
            yield report(pair, WorktreeResult.SKIPPED_DRY_RUN, branch,
                         f"would fast-forward {ahead} commits from '{branch}'")
            continue

        merged = _git(pair.main, "merge", "--ff-only", branch)
        if merged.returncode != 0:
            # Pre-flight cleared this, so the state changed underneath us.
            # Stop rather than leave the hierarchy part-shipped.
            detail = (merged.stderr or merged.stdout).strip().replace("\n", " ")
            yield report(pair, WorktreeResult.FAILED, branch,
                         f"fast-forward failed: {detail[:160]}")
            return

        yield report(pair, WorktreeResult.FETCHED, branch,
                     f"{ahead} commits from '{branch}'")

    # ── Refresh mtimes from the worktree ────────────────────────────────
    # Reversed direction: the worktree is the reference, the main checkout is
    # the target. This is what makes byte-identical results stop looking
    # stale, since the transport moved nothing for them.
    if preserve_mtimes and not dry_run:
        for pair in pairs:
            yield from sync_dataset(
                dataset_path=pair.dataset_path,
                source=pair.worktree,
                worktree_path=pair.main,
                branch=git_current_branch(pair.main),
            )
