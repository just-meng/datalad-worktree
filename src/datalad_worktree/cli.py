"""
Command-line interface for datalad-worktree.

Can be invoked as:
  - ``worktree <worktree-path> <branch>``
  - ``python -m datalad_worktree <worktree-path> <branch>``
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from datalad_worktree.core import (
    WorktreeResult,
    create_nested_worktrees,
)

# ─── ANSI colors ─────────────────────────────────────────────────────────────
class _Colors:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    BOLD = "\033[1m"
    NC = "\033[0m"

    @classmethod
    def disable(cls):
        cls.RED = cls.GREEN = cls.YELLOW = cls.BLUE = cls.BOLD = cls.NC = ""


C = _Colors


def _info(msg: str) -> None:
    print(f"{C.BLUE}[INFO]{C.NC}    {msg}")


def _ok(msg: str) -> None:
    print(f"{C.GREEN}[OK]{C.NC}      {msg}")


def _warn(msg: str) -> None:
    print(f"{C.YELLOW}[WARN]{C.NC}    {msg}")


def _error(msg: str) -> None:
    print(f"{C.RED}[ERROR]{C.NC}   {msg}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="worktree",
        description=(
            "Create nested git worktrees for DataLad dataset hierarchies.\n\n"
            "Run from the root of a DataLad superdataset. This tool discovers\n"
            "all subdatasets and creates a matching worktree tree at the\n"
            "specified location."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s /tmp/worktrees/my-feature feature/new-analysis\n"
            "  %(prog)s --dry-run ~/wt/experiment1 experiment/baseline\n"
            "  %(prog)s --force --no-create-branch /wt/hotfix release/1.0\n"
        ),
    )

    parser.add_argument(
        "worktree_path",
        type=Path,
        help="Path for the superdataset worktree (parent dir + name)",
    )
    parser.add_argument(
        "branch",
        help="Branch name to create/checkout in every worktree",
    )

    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        default=False,
        help="Show what would be done without doing it",
    )
    parser.add_argument(
        "-f", "--force",
        action="store_true",
        default=False,
        help="Pass --force to git worktree add",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="Verbose output",
    )
    parser.add_argument(
        "--no-create-branch",
        action="store_true",
        default=False,
        help="Don't create new branches; only checkout existing ones",
    )
    parser.add_argument(
        "--no-datalad",
        action="store_true",
        default=False,
        help="Skip DataLad API discovery; use .gitmodules parsing only",
    )
    parser.add_argument(
        "--dataset", "-d",
        type=Path,
        default=None,
        help="Path to the superdataset root (default: current directory)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help="Disable colored output",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.no_color or not sys.stdout.isatty():
        _Colors.disable()

    # Set up logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format=f"{C.BLUE}%(levelname)-8s{C.NC} %(message)s",
    )

    # Determine superdataset path
    superds_path = args.dataset if args.dataset else Path.cwd()
    superds_path = superds_path.resolve()

    worktree_path = args.worktree_path.resolve()

    _info(f"Super dataset:     {superds_path}")
    _info(f"Worktree path:     {worktree_path}")
    _info(f"Branch:            {args.branch}")

    if args.dry_run:
        _warn("DRY RUN — no changes will be made")

    print()

    try:
        result = create_nested_worktrees(
            superds_path=superds_path,
            worktree_path=worktree_path,
            branch=args.branch,
            create_branch=not args.no_create_branch,
            force=args.force,
            dry_run=args.dry_run,
            prefer_datalad=not args.no_datalad,
        )
    except ValueError as e:
        _error(str(e))
        return 1

    # ── Pretty-print results ─────────────────────────────────────────────
    print()
    _info("═" * 60)
    _info("Results")
    _info("═" * 60)

    for report in result.reports:
        label = report.dataset_path
        if label == ".":
            label = "superdataset"

        if report.result == WorktreeResult.CREATED:
            _ok(f"{label} → {report.destination}")
        elif report.result == WorktreeResult.CREATED_NEW_BRANCH:
            _ok(f"{label} → {report.destination}  {C.YELLOW}(new branch){C.NC}")
        elif report.result == WorktreeResult.SKIPPED_DRY_RUN:
            _info(f"[DRY-RUN] {label} → {report.destination}")
        elif report.result in (
            WorktreeResult.SKIPPED_NOT_INSTALLED,
            WorktreeResult.SKIPPED_NOT_GIT_REPO,
        ):
            _warn(f"{label}: {report.message}")
        elif report.result == WorktreeResult.FAILED:
            _error(f"{label}: {report.message}")

    # ── Summary ──────────────────────────────────────────────────────────
    print()
    _info("═" * 60)
    _ok(f"Worktree root:   {result.worktree_root}")
    _ok(f"Succeeded:       {len(result.succeeded)}")

    if result.skipped:
        _warn(f"Skipped:         {len(result.skipped)}")
    if result.failed:
        _error(f"Failed:          {len(result.failed)}")

    if result.all_ok:
        print()
        _ok(f"{C.BOLD}Done! Nested worktree is ready at: {result.worktree_root}{C.NC}")
    else:
        print()
        _error("Some worktrees failed to create. See errors above.")

    return 0 if result.all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
