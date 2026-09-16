#!/usr/bin/env python3
"""
A stand-in for ``singularity exec``, for tests.

Real container runtimes are not available in every dev environment, but the
mechanism behind datalad/datalad-container#288 needs nothing more than a
mount namespace: inside a worktree, ``.git`` is a symlink into the main
repository, so git-annex object symlinks resolve to paths that live outside
the worktree directory. A container that does not bind those paths cannot
read any annexed file.

This script models exactly that. It parses the singularity-style options we
care about and runs the command under ``unshare -Urm``, masking every path
listed in ``$FAKE_CONTAINER_HIDE`` with an empty tmpfs -- unless a ``-B``
bind makes that path available, in which case it is left alone.

Usage mirrors singularity::

    fake_container_runtime.py exec [-B src:dst[:opts]]... IMAGE COMMAND...
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys

MASKED_OPTIONS = ("--cleanenv", "--containall", "--no-home")
HIDE_ENV = "FAKE_CONTAINER_HIDE"


def parse_args(argv: list[str]) -> tuple[list[str], str, list[str]]:
    """Return (bind specs, image, command)."""
    if argv and argv[0] == "exec":
        argv = argv[1:]

    binds: list[str] = []
    while argv:
        if argv[0] == "-B" and len(argv) > 1:
            binds.append(argv[1])
            argv = argv[2:]
        elif argv[0] in MASKED_OPTIONS:
            argv = argv[1:]
        else:
            break

    if not argv:
        raise SystemExit("fake runtime: no image given")
    return binds, argv[0], argv[1:]


def is_covered(path: str, bind_sources: list[str]) -> bool:
    """True if ``path`` is inside (or equal to) one of the bound sources."""
    for source in bind_sources:
        source = source.rstrip("/")
        if path == source or path.startswith(source + "/"):
            return True
    return False


def main(argv: list[str]) -> int:
    binds, image, command = parse_args(argv)
    if not command:
        raise SystemExit("fake runtime: no command given")
    if not os.path.exists(image):
        raise SystemExit(f"fake runtime: image not found: {image}")

    bind_sources = [spec.split(":")[0] for spec in binds]
    to_hide = [
        path
        for path in os.environ.get(HIDE_ENV, "").split(os.pathsep)
        if path and not is_covered(path, bind_sources)
    ]

    script = "".join(f"mount -t tmpfs none {shlex.quote(p)} || exit 1\n" for p in to_hide)
    script += " ".join(shlex.quote(part) for part in command)

    return subprocess.call(["unshare", "-Urm", "sh", "-c", script])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
