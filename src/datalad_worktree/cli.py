"""
Command-line interface for datalad-worktree.

Can be invoked as:
  - ``worktree <worktree-path> <branch>``
  - ``python -m datalad_worktree <worktree-path> <branch>``
"""

from __future__ import annotations

import sys
from pathlib import Path

from datalad_worktree.core import (
    WorktreeReport,
    WorktreeResult,
    create_nested_worktrees,
)

# ─── ANSI colors ─────────────────────────────────────────────────────────────
class _Colors:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    DIM = "\033[2m"
    NC = "\033[0m"

    @classmethod
    def disable(cls):
        cls.RED = cls.GREEN = cls.YELLOW = cls.DIM = cls.NC = ""


C = _Colors


def _render_report(report: WorktreeReport) -> None:
    """Render a single worktree report to stdout."""
    label = report.dataset_path
    dest = report.destination

    if report.result == WorktreeResult.CREATED:
        print(f"{C.GREEN}create{C.NC} {label} -> {dest}")
    elif report.result == WorktreeResult.CREATED_NEW_BRANCH:
        print(f"{C.GREEN}create{C.NC} {label} -> {dest} {C.YELLOW}(new branch){C.NC}")
    elif report.result == WorktreeResult.SKIPPED_DRY_RUN:
        print(f"{C.GREEN}create{C.NC} {C.DIM}[DRY-RUN]{C.NC} {label} -> {dest}")
    elif report.result in (
        WorktreeResult.SKIPPED_NOT_INSTALLED,
        WorktreeResult.SKIPPED_NOT_GIT_REPO,
    ):
        print(f"{C.YELLOW}skip{C.NC}   {label} -> {dest} {C.DIM}({report.message}){C.NC}")
    elif report.result == WorktreeResult.FAILED:
        print(f"{C.RED}error{C.NC}  {label}: {report.message}", file=sys.stderr)


def build_parser():
    import argparse

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
        "--no-create-branch",
        action="store_true",
        default=False,
        help="Don't create new branches; only checkout existing ones",
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

    superds_path = args.dataset if args.dataset else Path.cwd()
    superds_path = superds_path.resolve()
    worktree_path = args.worktree_path.resolve()

    try:
        reports: list[WorktreeReport] = []
        created = 0
        skipped = 0
        for report in create_nested_worktrees(
            superds_path=superds_path,
            worktree_path=worktree_path,
            branch=args.branch,
            create_branch=not args.no_create_branch,
            force=args.force,
            dry_run=args.dry_run,
        ):
            reports.append(report)
            _render_report(report)
            if report.result in (
                WorktreeResult.CREATED,
                WorktreeResult.CREATED_NEW_BRANCH,
            ):
                created += 1
            elif report.result in (
                WorktreeResult.SKIPPED_NOT_INSTALLED,
                WorktreeResult.SKIPPED_NOT_GIT_REPO,
            ):
                skipped += 1
    except ValueError as e:
        print(f"{C.RED}error{C.NC}  {e}", file=sys.stderr)
        return 1

    # ── Summary ──────────────────────────────────────────────────────────
    has_failures = any(r.result == WorktreeResult.FAILED for r in reports)

    if args.dry_run:
        would_create = sum(
            1 for r in reports if r.result == WorktreeResult.SKIPPED_DRY_RUN
        )
        parts = [f"{would_create} would be created"]
        if skipped:
            parts.append(f"{skipped} skipped")
        print(f"\n{', '.join(parts)} at {worktree_path}")
    else:
        parts = [f"{created} created"]
        if skipped:
            parts.append(f"{skipped} skipped")
        print(f"\n{', '.join(parts)} at {worktree_path}")

    return 1 if has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
