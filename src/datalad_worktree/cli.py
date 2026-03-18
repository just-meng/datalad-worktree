"""
Command-line interface for datalad-worktree.

Can be invoked as:
  - ``worktree add <worktree-path> <branch>``
  - ``worktree list``
  - ``worktree remove <path-or-branch>``
  - ``python -m datalad_worktree ...``
"""

from __future__ import annotations

import sys
from pathlib import Path

from datalad_worktree.core import WorktreeReport, WorktreeResult

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


# ─── Rendering ───────────────────────────────────────────────────────────────


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
        WorktreeResult.SKIPPED_NO_WORKTREE,
    ):
        print(f"{C.YELLOW}skip{C.NC}   {label} {C.DIM}({report.message}){C.NC}")
    elif report.result == WorktreeResult.REMOVED:
        print(f"{C.GREEN}remove{C.NC} {label} -> {dest}")
    elif report.result == WorktreeResult.REMOVED_BRANCH:
        print(f"{C.GREEN}remove{C.NC} {label} branch '{report.branch}'")
    elif report.result == WorktreeResult.FAILED:
        print(f"{C.RED}error{C.NC}  {label}: {report.message}", file=sys.stderr)


# ─── Parser ──────────────────────────────────────────────────────────────────


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="worktree",
        description="Manage nested git worktrees for DataLad dataset hierarchies.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help="Disable colored output",
    )

    sub = parser.add_subparsers(dest="command")

    # ── add ──────────────────────────────────────────────────────────────
    add_p = sub.add_parser(
        "add",
        help="Create nested worktrees for all datasets",
    )
    add_p.add_argument(
        "worktree_path", type=Path,
        help="Path for the superdataset worktree",
    )
    add_p.add_argument(
        "branch",
        help="Branch name to create/checkout in every worktree",
    )
    add_p.add_argument(
        "-n", "--dry-run", action="store_true", default=False,
        help="Show what would be done without doing it",
    )
    add_p.add_argument(
        "-f", "--force", action="store_true", default=False,
        help="Pass --force to git worktree add",
    )
    add_p.add_argument(
        "--no-create-branch", action="store_true", default=False,
        help="Don't create new branches; only checkout existing ones",
    )
    add_p.add_argument(
        "-d", "--dataset", type=Path, default=None,
        help="Path to the superdataset root (default: current directory)",
    )

    # ── list ─────────────────────────────────────────────────────────────
    list_p = sub.add_parser(
        "list",
        help="List all worktrees for all datasets in the hierarchy",
    )
    list_p.add_argument(
        "-d", "--dataset", type=Path, default=None,
        help="Path to the superdataset root (default: current directory)",
    )

    # ── remove ───────────────────────────────────────────────────────────
    rm_p = sub.add_parser(
        "remove",
        help="Remove nested worktrees by path or branch name",
    )
    rm_p.add_argument(
        "target",
        help="Worktree path or branch name to remove",
    )
    rm_p.add_argument(
        "--delete-branch", action="store_true", default=False,
        help="Also delete the branch (safe delete; refuses if unmerged)",
    )
    rm_p.add_argument(
        "-f", "--force", action="store_true", default=False,
        help="Force removal even with uncommitted changes; force-delete branch",
    )
    rm_p.add_argument(
        "-d", "--dataset", type=Path, default=None,
        help="Path to the superdataset root (default: current directory)",
    )

    return parser


# ─── Subcommand handlers ────────────────────────────────────────────────────


def _cmd_add(args) -> int:
    from datalad_worktree.add import create_nested_worktrees

    superds_path = (args.dataset or Path.cwd()).resolve()
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


def _cmd_list(args) -> int:
    from datalad_worktree.list_cmd import list_nested_worktrees

    superds_path = (args.dataset or Path.cwd()).resolve()

    try:
        results = list_nested_worktrees(superds_path)
    except ValueError as e:
        print(f"{C.RED}error{C.NC}  {e}", file=sys.stderr)
        return 1

    for ds_wt in results:
        # Skip datasets with only the main worktree (the repo itself)
        extra_worktrees = [w for w in ds_wt.worktrees if not w.bare]
        if len(extra_worktrees) <= 1:
            continue

        print(f"{C.GREEN}{ds_wt.dataset_path}{C.NC}")
        for wt in extra_worktrees:
            branch_str = wt.branch or f"{C.DIM}(detached){C.NC}"
            # Mark the main worktree
            if wt.path.resolve() == ds_wt.source.resolve():
                print(f"  {wt.path} [{branch_str}] {C.DIM}(main){C.NC}")
            else:
                print(f"  {wt.path} [{branch_str}]")

    return 0


def _cmd_remove(args) -> int:
    from datalad_worktree.remove import remove_nested_worktrees

    superds_path = (args.dataset or Path.cwd()).resolve()

    try:
        reports: list[WorktreeReport] = []
        removed = 0
        skipped = 0
        for report in remove_nested_worktrees(
            superds_path=superds_path,
            target=args.target,
            delete_branch=args.delete_branch,
            force=args.force,
        ):
            reports.append(report)
            _render_report(report)
            if report.result == WorktreeResult.REMOVED:
                removed += 1
            elif report.result == WorktreeResult.SKIPPED_NO_WORKTREE:
                skipped += 1
    except ValueError as e:
        print(f"{C.RED}error{C.NC}  {e}", file=sys.stderr)
        return 1

    has_failures = any(r.result == WorktreeResult.FAILED for r in reports)

    parts = [f"{removed} removed"]
    if skipped:
        parts.append(f"{skipped} skipped")
    print(f"\n{', '.join(parts)}")

    return 1 if has_failures else 0


# ─── Main ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.no_color or not sys.stdout.isatty():
        _Colors.disable()

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "add":
        return _cmd_add(args)
    elif args.command == "list":
        return _cmd_list(args)
    elif args.command == "remove":
        return _cmd_remove(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
