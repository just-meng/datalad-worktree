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

Snakemake's ``.snakemake_timestamp`` marker is carried along with the
directory it sits in, although it is untracked; see ``_timestamp_markers``.
So are Snakemake's provenance records; see ``_snakemake_records``. All of it
is carried only for paths both sides provably share; see ``shared_paths``.

Reconstructing mtimes from commit dates (the ``git-restore-mtime`` approach)
is deliberately not used: these repositories are routinely rewritten by
``jj squash``/rebase housekeeping, which would make every file look new.

See https://blog.datalad.org/posts/snakemake-datalad-worktree/
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import shutil
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

# Where Snakemake keeps a directory() output's mtime; see `_timestamp_markers`.
SNAKEMAKE_TIMESTAMP = ".snakemake_timestamp"

# Snakemake's per-output provenance records and the markers of jobs that
# started but never finished, relative to the directory it was run from; see
# `_snakemake_records`.
SNAKEMAKE_METADATA = Path(".snakemake", "metadata")
SNAKEMAKE_INCOMPLETE = Path(".snakemake", "incomplete")


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


def shared_paths(reference: Path, worktree: Path) -> tuple[list[str], list[str]]:
    """
    ``(files, dirs)`` whose state can be carried from ``reference`` to ``worktree``.

    A file qualifies when it is tracked in both, holds the identical blob in
    both, is clean on *both* sides, and is not an unlocked annexed file.

    Both sides have to be clean because a dirty path's mtime describes
    content that side's HEAD does not have. In the reference that would
    export a timestamp for content the target never received; in the target
    it would overwrite the timestamp of uncommitted local work with one that
    claims the reference's content. ``worktree fetch`` ships into a checkout
    that is allowed to be dirty, so the second case is routine.

    A directory qualifies when it holds a qualifying file and no tracked file
    that does not -- because Snakemake judges a ``directory()`` output by the
    directory alone, so its state vouches for everything inside. Directories
    are listed deepest first, and the root is excluded: it holds a ``.git``
    file and subdataset mounts the reference root does not.
    """
    ref_entries = _tracked_entries(reference)
    if not ref_entries:
        return [], []

    ref_blobs = {path: oid for _mode, oid, _size, path in ref_entries}
    wt_blobs = tracked_blobs(worktree)
    dirty = dirty_paths(reference) | dirty_paths(worktree)
    # Same blob on both sides, so the reference's lock state is the worktree's.
    unlocked = _unlocked_from_entries(reference, ref_entries)

    identical = {
        path for path, oid in ref_blobs.items()
        if path not in dirty and wt_blobs.get(path) == oid
    }
    files = sorted(identical - unlocked)
    # An unlocked file is skipped for cost, not because it differs.
    tainted = set(parent_dirs((ref_blobs.keys() | wt_blobs.keys()) - identical))
    dirs = [d for d in parent_dirs(identical) if d not in tainted]
    return files, dirs


def parent_dirs(paths: Iterable[str]) -> list[str]:
    """Every directory containing one of ``paths``, deepest first, root excluded."""
    dirs: set[str] = set()
    for path in paths:
        parent = PurePosixPath(path).parent
        while str(parent) != ".":
            dirs.add(str(parent))
            parent = parent.parent
    return sorted(dirs, key=lambda d: (d.count("/"), d), reverse=True)


def _stamp(source: Path, target: Path) -> bool:
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


def _copy(source: Path, target: Path) -> bool:
    """Copy one untracked file, mtime included. Returns whether it happened."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
    except OSError as error:
        logger.debug("Could not copy %s -> %s: %s", source, target, error)
        return False
    return True


def _timestamp_markers(reference: Path, dirs: list[str]) -> list[str]:
    """
    Snakemake's ``.snakemake_timestamp`` markers in the shared directories.

    For a ``directory()`` output Snakemake reads this file's mtime instead of
    the directory's own whenever the file exists, and ``snakemake --touch``
    stamps only the file. It is conventionally gitignored, so a worktree never
    has it: Snakemake there falls back to the directory mtime, which ``--touch``
    never moved, and reruns a job the reference considers up to date.
    """
    return [
        f"{d}/{SNAKEMAKE_TIMESTAMP}" for d in dirs
        if (reference / d / SNAKEMAKE_TIMESTAMP).is_file()
    ]


def _record_key(record: Path, records_dir: Path) -> str | None:
    """
    The output path a Snakemake record file is about, or ``None``.

    Snakemake names the file after the urlsafe-base64 output path, split into
    ``@``-prefixed parent directories when that exceeds the filesystem's name
    limit (``FilePersistence._record_path``).
    """
    encoded = "".join(
        part.removeprefix("@") for part in record.relative_to(records_dir).parts
    )
    try:
        return base64.urlsafe_b64decode(encoded).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def _snakemake_records(reference: Path, worktree: Path, shared: set[str]) -> list[str]:
    """
    Snakemake's provenance records for the shared outputs.

    Besides mtimes, Snakemake reruns a job when the code, input set or params
    stored in its output's record differ from the current rule. Git does not
    carry ``.snakemake/metadata/``, so each checkout keeps the record of its
    *own* last run: in the reported case, 17 fetched jobs whose outputs were
    newer than all their inputs reran for "code has changed since last
    execution". Outputs marked incomplete on either side are left out. Only
    Snakemake's default file backend is read, run from the dataset root.
    """
    records_dir = reference / SNAKEMAKE_METADATA
    selected: list[str] = []
    for record in records_dir.rglob("*"):
        key = _record_key(record, records_dir) if record.is_file() else None
        if key is None or key.rstrip("/") not in shared:
            continue
        relative = record.relative_to(records_dir)
        if any((side / SNAKEMAKE_INCOMPLETE / relative).exists()
               for side in (reference, worktree)):
            continue
        selected.append(str(SNAKEMAKE_METADATA / relative))
    return selected


def copy_mtimes(reference: Path, worktree: Path) -> dict[str, int]:
    """
    Carry the state a make-style pipeline reads from one working tree to another.

    Every kind -- file and directory mtimes, timestamp markers, provenance
    records -- is carried only for ``shared_paths``. Returns how many of each
    were carried. Order matters: creating a marker or a record moves its
    parent directory's mtime, so directories are stamped last.

    Raises
    ------
    OSError
        If ``os.utime`` cannot leave symlinks alone on this platform.
        Degrading to following them would silently rewrite the shared annex
        object store.
    """
    if os.utime not in os.supports_follow_symlinks:
        raise OSError("os.utime does not support follow_symlinks on this platform")

    files, dirs = shared_paths(reference, worktree)
    steps = [
        ("snakemake records", _copy,
         _snakemake_records(reference, worktree, {*files, *dirs})),
        ("markers", _copy, _timestamp_markers(reference, dirs)),
        ("files", _stamp, files),
        ("dirs", _stamp, dirs),
    ]
    return {
        name: sum(carry(reference / path, worktree / path) for path in paths)
        for name, carry, paths in steps
    }


def sync_dataset(
    dataset_path: str,
    source: Path,
    worktree_path: Path,
    branch: str,
) -> Iterator[WorktreeReport]:
    """Copy mtimes into one working tree and report the result."""
    def report(result: WorktreeResult, message: str) -> WorktreeReport:
        return WorktreeReport(
            dataset_path=dataset_path,
            source=source,
            destination=worktree_path,
            result=result,
            branch=branch,
            message=message,
        )

    try:
        counts = copy_mtimes(source, worktree_path)
    except OSError as error:
        yield report(WorktreeResult.FAILED, str(error))
        return

    shown = ", ".join(f"{n} {name}" for name, n in counts.items()
                      if n or name in ("files", "dirs"))
    yield report(WorktreeResult.MTIMES_SYNCED, f"{shown} from {source}")


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
