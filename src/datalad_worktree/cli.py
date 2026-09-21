"""
Command-line interface for datalad-worktree.

Can be invoked as:
  - ``worktree add <branch> <worktree-path>``
  - ``worktree`` or ``worktree list``
  - ``worktree delete <path-or-branch>``
  - ``worktree sync-mtimes [path-or-branch]``
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
    is_tty = sys.stdout.isatty()

    if report.result == WorktreeResult.STARTING:
        if is_tty:
            # Overwritable progress line
            print(f"{C.DIM}  ...  {label}{C.NC}", end="\r", flush=True)
        # Non-TTY: skip STARTING lines entirely (no partial output)
    elif report.result == WorktreeResult.CREATED:
        if is_tty:
            # Clear the STARTING line
            print("\033[2K", end="")
        print(f"{C.GREEN}create{C.NC} {label} -> {dest}")
    elif report.result == WorktreeResult.CREATED_NEW_BRANCH:
        if is_tty:
            print("\033[2K", end="")
        print(f"{C.GREEN}create{C.NC} {label} -> {dest} {C.YELLOW}(new branch){C.NC}")
    elif report.result == WorktreeResult.SKIPPED_DRY_RUN:
        print(f"{C.GREEN}create{C.NC} {C.DIM}[DRY-RUN]{C.NC} {label} -> {dest}")
    elif report.result in (
        WorktreeResult.SKIPPED_NOT_INSTALLED,
        WorktreeResult.SKIPPED_NOT_GIT_REPO,
        WorktreeResult.SKIPPED_NO_WORKTREE,
        WorktreeResult.SKIPPED_CONTAINER,
    ):
        print(f"{C.YELLOW}skip{C.NC}   {label} {C.DIM}({report.message}){C.NC}")
    elif report.result == WorktreeResult.CONFIGURED:
        print(f"{C.GREEN}config{C.NC} {label} {C.DIM}({report.message}){C.NC}")
    elif report.result == WorktreeResult.MTIMES_SYNCED:
        print(f"{C.GREEN}mtimes{C.NC} {label} {C.DIM}({report.message}){C.NC}")
    elif report.result == WorktreeResult.DELETED:
        print(f"{C.GREEN}delete{C.NC} {label} -> {dest}")
    elif report.result == WorktreeResult.DELETED_BRANCH:
        print(f"{C.GREEN}delete{C.NC} {label} branch '{report.branch}'")
    elif report.result == WorktreeResult.FAILED:
        if is_tty:
            print("\033[2K", end="")
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
        help="disable colored output",
    )

    sub = parser.add_subparsers(dest="command")

    # ── add ──────────────────────────────────────────────────────────────
    add_p = sub.add_parser(
        "add",
        help="create nested worktrees for all datasets",
    )
    add_p.add_argument(
        "branch",
        help="branch name to create/checkout in every worktree",
    )
    add_p.add_argument(
        "worktree_path", type=Path,
        help="path for the superdataset worktree",
    )
    add_p.add_argument(
        "-n", "--dry-run", action="store_true", default=False,
        help="show what would be done without doing it",
    )
    add_p.add_argument(
        "-f", "--force", action="store_true", default=False,
        help="pass --force to git worktree add",
    )
    add_p.add_argument(
        "--no-create-branch", action="store_true", default=False,
        help="don't create new branches; only checkout existing ones",
    )
    add_p.add_argument(
        "--no-bindpaths", action="store_true", default=False,
        help="don't configure container bind mounts for datalad containers-run",
    )
    add_p.add_argument(
        "--no-mtimes", action="store_true", default=False,
        help="don't copy file mtimes from the source working trees",
    )
    add_p.add_argument(
        "-d", "--dataset", type=Path, default=None,
        help="path to the superdataset root (default: current directory)",
    )

    # ── list ─────────────────────────────────────────────────────────────
    list_p = sub.add_parser(
        "list",
        help="list all worktrees for all datasets in the hierarchy",
    )
    list_p.add_argument(
        "-d", "--dataset", type=Path, default=None,
        help="path to the superdataset root (default: current directory)",
    )

    # ── sync-mtimes ──────────────────────────────────────────────────────
    sync_p = sub.add_parser(
        "sync-mtimes",
        help="copy file mtimes from the main working trees into a worktree",
    )
    # Target is a path or a branch name, resolved the same way delete does.
    sync_p.add_argument(
        "target", nargs="?", default=None,
        help="worktree path or branch name (default: current directory)",
    )
    sync_p.add_argument(
        "--from", dest="reference", type=Path, default=None,
        help="reference working tree (default: the one this worktree came from)",
    )
    sync_p.add_argument(
        "-d", "--dataset", type=Path, default=None,
        help="path to the superdataset root (default: current directory)",
    )

    # ── delete ───────────────────────────────────────────────────────────
    del_p = sub.add_parser(
        "delete",
        help="delete nested worktrees by path or branch name",
    )
    del_p.add_argument(
        "target",
        help="worktree path or branch name to delete",
    )
    del_p.add_argument(
        "--delete-branch", action="store_true", default=False,
        help="also delete the branch (safe delete; refuses if unmerged)",
    )
    del_p.add_argument(
        "-f", "--force", action="store_true", default=False,
        help="force deletion even with uncommitted changes; force-delete branch",
    )
    del_p.add_argument(
        "-y", "--yes", action="store_true", default=False,
        help="skip confirmation prompt",
    )
    del_p.add_argument(
        "-d", "--dataset", type=Path, default=None,
        help="path to the superdataset root (default: current directory)",
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
            configure_containers=not args.no_bindpaths,
            preserve_mtimes=not args.no_mtimes,
        ):
            _render_report(report)
            if report.result == WorktreeResult.STARTING:
                continue  # progress indicator, not a final result
            reports.append(report)
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


def _cmd_sync_mtimes(args) -> int:
    from datalad_worktree.mtimes import resolve_worktree_target, sync_nested_mtimes

    reports: list[WorktreeReport] = []
    try:
        worktree_path = resolve_worktree_target(
            target=args.target,
            dataset=args.dataset,
        )
        for report in sync_nested_mtimes(
            worktree_path=worktree_path,
            reference=args.reference,
        ):
            _render_report(report)
            reports.append(report)
    except ValueError as e:
        print(f"{C.RED}error{C.NC}  {e}", file=sys.stderr)
        return 1

    synced = sum(1 for r in reports if r.result == WorktreeResult.MTIMES_SYNCED)
    print(f"\n{synced} datasets synced at {worktree_path}")

    return 1 if any(r.result == WorktreeResult.FAILED for r in reports) else 0


def _cmd_list(args) -> int:
    from datalad_worktree.list_cmd import (
        column_width,
        group_by_branch,
        list_nested_worktrees,
    )

    superds_path = (args.dataset or Path.cwd()).resolve()

    try:
        results = list_nested_worktrees(superds_path)
    except ValueError as e:
        print(f"{C.RED}error{C.NC}  {e}", file=sys.stderr)
        return 1

    # Only consider datasets that have extra worktrees
    datasets_with_extras = [
        ds_wt for ds_wt in results
        if sum(1 for w in ds_wt.worktrees if not w.bare) > 1
    ]
    if not datasets_with_extras:
        return 0

    entries = (
        (
            ds_wt.dataset_path,
            wt.path,
            wt.branch or "(detached)",
            wt.path.resolve() == ds_wt.source.resolve(),
        )
        for ds_wt in datasets_with_extras
        for wt in ds_wt.worktrees
        if not wt.bare
    )
    main_group, branch_groups, super_branch = group_by_branch(entries)
    col_width = column_width(main_group, branch_groups)

    # Print main worktrees group
    if main_group:
        header = super_branch or "(unknown)"
        print(f"{C.GREEN}{header}{C.NC}")
        for ds_path, wt_path, branch in main_group:
            annotation = ""
            if branch != super_branch:
                annotation = f" {C.DIM}({branch}){C.NC}"
            print(f"  {ds_path:<{col_width}}{wt_path}{annotation}")

    # Print extra branch groups
    for branch in sorted(branch_groups):
        print(f"{C.GREEN}{branch}{C.NC}")
        for ds_path, wt_path in branch_groups[branch]:
            print(f"  {ds_path:<{col_width}}{wt_path}")

    return 0


def _cmd_delete(args) -> int:
    from datalad_worktree.delete import (
        delete_nested_worktrees,
        resolve_delete_targets,
    )

    superds_path = (args.dataset or Path.cwd()).resolve()

    # ── Resolve targets ─────────────────────────────────────────────────
    try:
        targets, skipped = resolve_delete_targets(superds_path, args.target)
    except ValueError as e:
        print(f"{C.RED}error{C.NC}  {e}", file=sys.stderr)
        return 1

    if not targets:
        for report in skipped:
            _render_report(report)
        print(f"\n0 deleted, {len(skipped)} skipped")
        return 0

    # ── Show preview and confirm ────────────────────────────────────────
    col_width = max(len(t.dataset_path) for t in targets) + 2

    print(f"Will delete {len(targets)} worktree(s):")
    for t in targets:
        print(f"  {t.dataset_path:<{col_width}}{t.worktree_path}")
    if args.delete_branch:
        branches = sorted({t.branch for t in targets if t.branch})
        if branches:
            print(f"Will also delete branch: {', '.join(branches)}")

    if not args.yes:
        try:
            answer = input("\nProceed? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        if answer.strip().lower() != "y":
            print("Aborted.")
            return 1

    # ── Delete ──────────────────────────────────────────────────────────
    try:
        reports: list[WorktreeReport] = []
        deleted = 0
        skipped_count = len(skipped)
        for report in delete_nested_worktrees(
            superds_path=superds_path,
            target=args.target,
            delete_branch=args.delete_branch,
            force=args.force,
        ):
            reports.append(report)
            _render_report(report)
            if report.result == WorktreeResult.DELETED:
                deleted += 1
            elif report.result == WorktreeResult.SKIPPED_NO_WORKTREE:
                skipped_count += 1
    except ValueError as e:
        print(f"{C.RED}error{C.NC}  {e}", file=sys.stderr)
        return 1

    has_failures = any(r.result == WorktreeResult.FAILED for r in reports)

    parts = [f"{deleted} deleted"]
    if skipped_count:
        parts.append(f"{skipped_count} skipped")
    print(f"\n{', '.join(parts)}")

    return 1 if has_failures else 0


# ─── Main ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.no_color or not sys.stdout.isatty():
        _Colors.disable()

    if args.command is None:
        args.dataset = None
        return _cmd_list(args)

    if args.command == "add":
        return _cmd_add(args)
    elif args.command == "list":
        return _cmd_list(args)
    elif args.command == "delete":
        return _cmd_delete(args)
    elif args.command == "sync-mtimes":
        return _cmd_sync_mtimes(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
