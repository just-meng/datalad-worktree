"""
Preserve file mtimes when creating a worktree.

A freshly checked-out worktree gives every file the time it was written, so
the mtime *ordering* between inputs and outputs -- which for a make-style
pipeline (Snakemake, Make, redo) *is* the up-to-date state -- is lost. It is
not merely lost but systematically inverted: ``worktree add`` checks out the
superdataset first and each subdataset after, so every file in a ``code/``
subdataset ends up newer than every derived output in the superdataset, i.e.
exactly the "all your code changed" signal.

This module copies each file's mtime from the working tree the worktree was
created from, as the final step of ``worktree add``.

Two properties keep it safe:

- Paths are matched on **blob OID**, not on name. A worktree created from a
  different ref only inherits mtimes for content that is byte-identical;
  anything that genuinely differs keeps its checkout mtime, so the operation
  can never claim freshness for content the worktree does not have.
- ``follow_symlinks=False`` is required, not stylistic. Annexed files are
  symlinks into ``.git/annex/objects``, and that object store is shared with
  the main repository -- the same inode. Writing through the link would
  mutate the source dataset's (mode 444) annex objects, and would be a no-op
  for the consumer anyway: Snakemake reads the symlink's own mtime.

Reconstructing mtimes from commit dates (the ``git-restore-mtime`` approach)
is deliberately not used: these repositories are routinely rewritten by
``jj squash``/rebase housekeeping, which would make every file look new.

See https://blog.datalad.org/posts/snakemake-datalad-worktree/
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath

from datalad_worktree.core import WorktreeReport, WorktreeResult

logger = logging.getLogger(__name__)

# `ls-tree` modes worth stamping. 160000 (a submodule gitlink) is excluded:
# it is not a file in this worktree, it is where another one is mounted.
BLOB_MODES = frozenset({"100644", "100755", "120000"})

# `status --porcelain` index codes that carry a second, NUL-separated path.
_TWO_PATH_CODES = frozenset({"R", "C"})


def _git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in ``repo_path``."""
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
    )


def tracked_blobs(repo_path: Path) -> dict[str, str]:
    """
    Map tracked path -> blob OID at HEAD, for one working tree.

    Submodule gitlinks are dropped; everything left is a regular file, an
    executable, or a symlink -- all of which have an mtime of their own.
    """
    result = _git(repo_path, "ls-tree", "-r", "-z", "HEAD")
    if result.returncode != 0:
        logger.debug("ls-tree failed in %s: %s", repo_path, result.stderr.strip())
        return {}

    blobs: dict[str, str] = {}
    for record in result.stdout.split("\0"):
        if not record:
            continue
        # "<mode> SP <type> SP <oid> TAB <path>"; -z leaves paths unquoted.
        meta, _, path = record.partition("\t")
        mode, _, rest = meta.partition(" ")
        if mode not in BLOB_MODES:
            continue
        blobs[path] = rest.rpartition(" ")[2]
    return blobs


def dirty_paths(repo_path: Path) -> set[str]:
    """
    Tracked paths whose working-tree content differs from HEAD.

    Their mtimes describe content the worktree does not have, so they must
    not be copied. OID matching alone does not catch this: a file modified
    in the working tree still has the *committed* blob that the worktree
    checked out.
    """
    result = _git(
        repo_path,
        "status", "--porcelain", "-z", "-uno", "--ignore-submodules=all",
    )
    if result.returncode != 0:
        logger.debug("status failed in %s: %s", repo_path, result.stderr.strip())
        return set()

    fields = [f for f in result.stdout.split("\0") if f]
    dirty: set[str] = set()
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if len(record) < 4:
            continue
        dirty.add(record[3:])
        # A rename or copy is reported as "<new>\0<old>": both are suspect.
        if record[0] in _TWO_PATH_CODES and index < len(fields):
            dirty.add(fields[index])
            index += 1
    return dirty


def transferable_paths(reference: Path, worktree: Path) -> list[str]:
    """
    Paths whose mtime can be carried from ``reference`` to ``worktree``.

    A path qualifies when it is tracked in both, holds the identical blob in
    both, and is clean in the reference working tree.
    """
    ref_blobs = tracked_blobs(reference)
    if not ref_blobs:
        return []

    wt_blobs = tracked_blobs(worktree)
    dirty = dirty_paths(reference)

    return sorted(
        path for path, oid in ref_blobs.items()
        if path not in dirty and wt_blobs.get(path) == oid
    )


def parent_dirs(paths: Iterable[str]) -> list[str]:
    """
    Every directory containing one of ``paths``, deepest first.

    Snakemake supports ``directory()`` outputs, whose staleness is read off
    the directory's own mtime, so directories need the same treatment as
    files. The worktree root is excluded: it holds a ``.git`` file and
    subdataset mounts that the reference root does not, so its mtime is not
    a property the two trees share.
    """
    dirs: set[str] = set()
    for path in paths:
        parent = PurePosixPath(path).parent
        while str(parent) != ".":
            dirs.add(str(parent))
            parent = parent.parent
    return sorted(dirs, key=lambda d: (d.count("/"), d), reverse=True)


def _copy_one(source: Path, target: Path) -> bool:
    """
    Copy one path's atime/mtime. Returns whether it happened.

    Either side may be missing -- a worktree checked out from another ref,
    a reference file dropped by ``datalad drop`` -- which is not an error.
    """
    try:
        st = os.lstat(source)
        os.utime(
            target,
            ns=(st.st_atime_ns, st.st_mtime_ns),
            follow_symlinks=False,  # never write through an annex symlink
        )
    except OSError as error:
        logger.debug("Could not copy mtime %s -> %s: %s", source, target, error)
        return False
    return True


def copy_mtimes(reference: Path, worktree: Path) -> tuple[int, int, str]:
    """
    Copy mtimes from a working tree to a worktree made from it.

    Returns ``(files, dirs, error_message)``. A non-empty error message
    means nothing was copied.
    """
    if os.utime not in os.supports_follow_symlinks:
        # Degrading to follow_symlinks=True would silently rewrite the
        # shared annex object store, so refuse instead.
        return 0, 0, "os.utime does not support follow_symlinks on this platform"

    paths = transferable_paths(reference, worktree)

    files = sum(_copy_one(reference / path, worktree / path) for path in paths)
    # Files first: directories inherit whatever the reference says, and
    # stamping a child never disturbs the parent we already set.
    dirs = sum(
        _copy_one(reference / path, worktree / path)
        for path in parent_dirs(paths)
    )
    return files, dirs, ""


def sync_dataset(
    dataset_path: str,
    source: Path,
    worktree_path: Path,
    branch: str,
) -> Iterator[WorktreeReport]:
    """Copy mtimes into one freshly created worktree and report the result."""
    files, dirs, error = copy_mtimes(source, worktree_path)

    def report(result: WorktreeResult, message: str) -> WorktreeReport:
        return WorktreeReport(
            dataset_path=dataset_path,
            source=source,
            destination=worktree_path,
            result=result,
            branch=branch,
            message=message,
        )

    if error:
        yield report(WorktreeResult.FAILED, error)
        return

    yield report(
        WorktreeResult.MTIMES_SYNCED,
        f"{files} files, {dirs} dirs from {source}",
    )
