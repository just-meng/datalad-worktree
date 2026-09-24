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

Three properties keep it safe:

- Paths are matched on **blob OID**, not on name. A worktree created from a
  different ref only inherits mtimes for content that is byte-identical;
  anything that genuinely differs keeps its checkout mtime, so the operation
  can never claim freshness for content the worktree does not have.
- ``follow_symlinks=False`` is required, not stylistic. Annexed files are
  symlinks into ``.git/annex/objects``, and that object store is shared with
  the main repository -- the same inode. Writing through the link would
  mutate the source dataset's (mode 444) annex objects, and would be a no-op
  for the consumer anyway: Snakemake reads the symlink's own mtime.
- **Unlocked** annexed files are skipped entirely; see
  ``unlocked_annex_paths``. Stamping them is what makes git re-hash gigabytes
  on the next ``git status``, and under ``annex.thin`` it reaches the shared
  object store that the point above is careful to protect.

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

from datalad_worktree.core import (
    WorktreeReport,
    WorktreeResult,
    git_branch_checked_out_at,
    git_worktree_prune,
    validate_superds,
)
from datalad_worktree.discovery import is_git_repo_root

logger = logging.getLogger(__name__)

# `ls-tree` modes worth stamping. 160000 (a submodule gitlink) is excluded:
# it is not a file in this worktree, it is where another one is mounted.
BLOB_MODES = frozenset({"100644", "100755", "120000"})

# Modes that are a real file rather than a symlink -- the only ones that can
# be an unlocked annexed file.
_FILE_MODES = frozenset({"100644", "100755"})

# An annex pointer file is one short line, so the blob size from `ls-tree -l`
# rules almost every blob out without reading any content.
_POINTER_PREFIX = b"/annex/objects/"
_POINTER_MAX_BYTES = 1024

# `status --porcelain` index codes that carry a second, NUL-separated path.
_TWO_PATH_CODES = frozenset({"R", "C"})


def _git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in ``repo_path``."""
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
    )


def _tracked_entries(repo_path: Path) -> list[tuple[str, str, int, str]]:
    """
    ``(mode, oid, size, path)`` for every tracked blob at HEAD.

    ``-l`` adds the blob size, which is what lets the annex-pointer check
    below reject almost everything without reading any content.
    """
    result = _git(repo_path, "ls-tree", "-r", "-l", "-z", "HEAD")
    if result.returncode != 0:
        logger.debug("ls-tree failed in %s: %s", repo_path, result.stderr.strip())
        return []

    entries: list[tuple[str, str, int, str]] = []
    for record in result.stdout.split("\0"):
        if not record:
            continue
        # "<mode> SP <type> SP <oid> SP <size> TAB <path>"; -z leaves paths
        # unquoted, and <size> is space-padded (and "-" for a gitlink).
        meta, _, path = record.partition("\t")
        fields = meta.split()
        if len(fields) != 4:
            continue
        mode, _type, oid, raw_size = fields
        if mode not in BLOB_MODES:
            continue
        entries.append((mode, oid, int(raw_size) if raw_size.isdigit() else 0, path))
    return entries


def tracked_blobs(repo_path: Path) -> dict[str, str]:
    """
    Map tracked path -> blob OID at HEAD, for one working tree.

    Submodule gitlinks are dropped; everything left is a regular file, an
    executable, or a symlink -- all of which have an mtime of their own.
    """
    return {path: oid for _mode, oid, _size, path in _tracked_entries(repo_path)}


def _blob_heads(repo_path: Path, oids: list[str]) -> dict[str, bytes]:
    """
    First bytes of each blob, in one ``cat-file --batch`` call.

    Only enough bytes to recognise a pointer are kept, so the result stays
    small no matter how many candidates are passed in.
    """
    if not oids:
        return {}

    proc = subprocess.run(
        ["git", "-C", str(repo_path), "cat-file", "--batch"],
        input=("\n".join(oids) + "\n").encode(),
        capture_output=True,
    )
    if proc.returncode != 0:
        logger.debug("cat-file --batch failed in %s", repo_path)
        return {}

    out = proc.stdout
    heads: dict[str, bytes] = {}
    pos = 0
    for oid in oids:
        newline = out.find(b"\n", pos)
        if newline == -1:
            break
        header = out[pos:newline].split()
        if len(header) != 3:
            # "<oid> missing" -- no content follows, so only the line is used.
            pos = newline + 1
            continue
        start = newline + 1
        heads[oid] = out[start:start + len(_POINTER_PREFIX)]
        pos = start + int(header[2]) + 1  # content, then its trailing newline
    return heads


def _unlocked_from_entries(
    repo_path: Path,
    entries: list[tuple[str, str, int, str]],
) -> set[str]:
    """``unlocked_annex_paths`` given an already-read entry list."""
    candidates = [
        (oid, path)
        for mode, oid, size, path in entries
        if mode in _FILE_MODES and 0 < size <= _POINTER_MAX_BYTES
    ]
    heads = _blob_heads(repo_path, [oid for oid, _ in candidates])
    return {
        path for oid, path in candidates
        if heads.get(oid, b"").startswith(_POINTER_PREFIX)
    }


def unlocked_annex_paths(repo_path: Path) -> set[str]:
    """
    Tracked paths that are *unlocked* annexed files.

    git-annex tracks a file either locked -- a symlink into
    ``.git/annex/objects`` -- or unlocked, committed as a regular file whose
    blob is a one-line pointer while the working tree holds the content.

    Their mtimes must not be copied, for two independent reasons.

    Git's index is a stat cache: each entry records the mtime at which the
    content was last verified, and ``git status`` skips hashing while that
    still matches. Re-stamping a *symlink* is free to re-verify, because git
    only re-reads the link target. Re-stamping an unlocked annexed file
    invalidates its entry, so git must re-read and re-hash the whole file
    through git-annex's clean filter. Measured on one real dataset: stamping
    10099 symlinks cost the next ``git status`` 0.11 s, while stamping 28
    unlocked files totalling 3.71 GB cost it 152 s.

    It is also the one case where stamping can reach the shared annex object
    store. Under ``annex.thin`` an unlocked file *is* a hardlink to its annex
    object, so ``os.utime`` would move the object's own mtime -- precisely
    what ``follow_symlinks=False`` protects against for locked files.
    """
    return _unlocked_from_entries(repo_path, _tracked_entries(repo_path))


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
    both, is clean on *both* sides, and is not an unlocked annexed file.

    Both sides have to be clean because a dirty path's mtime describes
    content that side's HEAD does not have. In the reference that would
    export a timestamp for content the target never received; in the target
    it would overwrite the timestamp of uncommitted local work with one that
    claims the reference's content. ``worktree update`` ships into a
    checkout that is allowed to be dirty, so the second case is routine.
    """
    ref_entries = _tracked_entries(reference)
    if not ref_entries:
        return []

    ref_blobs = {path: oid for _mode, oid, _size, path in ref_entries}
    wt_blobs = tracked_blobs(worktree)
    dirty = dirty_paths(reference) | dirty_paths(worktree)
    # Same blob on both sides, so the reference's lock state is the worktree's.
    unlocked = _unlocked_from_entries(reference, ref_entries)

    return sorted(
        path for path, oid in ref_blobs.items()
        if path not in dirty
        and path not in unlocked
        and wt_blobs.get(path) == oid
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


def main_working_tree(worktree_path: Path) -> Path | None:
    """
    The working tree a *superdataset* worktree was created from.

    Only sound for the superdataset. For a subdataset whose ``.git`` is a
    gitlink into ``<super>/.git/modules/<name>``, git reports that git
    directory as the repository's main worktree -- it is not a working tree
    at all, and its parent is not the subdataset. Subdatasets are therefore
    mapped by relative path against the resolved superdataset instead.
    """
    result = _git(worktree_path, "rev-parse", "--git-common-dir")
    if result.returncode != 0:
        return None

    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = (worktree_path / common).resolve()
    if common.name != ".git":
        return None

    candidate = common.parent
    return candidate if is_git_repo_root(candidate) else None


def resolve_worktree_target(
    target: str | None = None,
    dataset: Path | None = None,
) -> Path:
    """
    Resolve a worktree path *or* a branch name to a worktree root.

    Mirrors how ``worktree delete`` reads its target: an existing path is
    taken as one, anything else is looked up as a branch. The branch lookup
    runs against ``dataset`` (default: the current directory) and works
    from inside another worktree too, since ``git worktree list`` reports
    every worktree of the repository.

    Only the worktree is resolved here, never the reference to copy from.
    Looking a branch up from a *sibling* worktree would otherwise make that
    sibling the reference, when what is wanted is always the main working
    tree -- which ``main_working_tree`` detects from the target itself.

    Raises
    ------
    ValueError
        If no worktree can be found for ``target``.
    """
    if target is None:
        return Path.cwd()

    if Path(target).exists():
        return Path(target).resolve()

    # Not a path, so read it as a branch name.
    dataset_root = validate_superds(dataset or Path.cwd())
    # A directory removed with `rm -rf` lingers in git's administrative
    # data; drop it before trusting the lookup.
    git_worktree_prune(dataset_root)

    worktree_root = git_branch_checked_out_at(dataset_root, target)
    if worktree_root is None:
        raise ValueError(
            f"no worktree on branch '{target}' in {dataset_root}, "
            f"and no such path"
        )

    return worktree_root.resolve()
