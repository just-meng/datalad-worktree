"""
DataLad command interfaces for worktree-add, worktree-list, worktree-delete
and worktree-sync-mtimes.

Requires DataLad to be installed.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import datalad.support.ansi_colors as ac
    from datalad.interface.base import Interface, build_doc, eval_results
    from datalad.interface.results import get_status_dict
    from datalad.interface.utils import default_result_renderer
    from datalad.support.constraints import EnsureNone, EnsureStr
    from datalad.support.param import Parameter
    from datalad.ui import ui

    # ── worktree-add ─────────────────────────────────────────────────────

    @build_doc
    class WorktreeAdd(Interface):
        """Create nested git worktrees for a DataLad dataset hierarchy.

        Creates a git worktree for the superdataset and every installed
        subdataset, mirroring the nested structure under a new root
        directory. Each worktree checks out (or creates) the specified
        branch.

        Runs a pre-flight check before creating anything. If any dataset
        would fail (e.g. branch already checked out elsewhere), no
        worktrees are created.

        Examples::

            # Create worktrees under /tmp/wt on branch 'feature/x'
            datalad worktree-add feature/x /tmp/wt

            # Dry run
            datalad worktree-add --dry-run dev/experiment /tmp/wt
        """

        @staticmethod
        def custom_result_renderer(res, **kwargs):
            if res["action"] != "worktree-add":
                default_result_renderer(res)
                return
            status = res.get("status", "")
            dest = res.get("path", "")
            label = res.get("dataset_path", "") or "."
            skip_reason = res.get("skip_reason", "")
            dry_run = res.get("dry_run", False)

            if res.get("container_config"):
                ui.message("{} {} ({})".format(
                    ac.color_word("config", ac.GREEN),
                    label, res.get("message", ""),
                ))
            elif res.get("mtimes"):
                ui.message("{} {} ({})".format(
                    ac.color_word("mtimes", ac.GREEN),
                    label, res.get("message", ""),
                ))
            elif status == "ok":
                extra = ""
                if res.get("new_branch"):
                    extra = ac.color_word(" (new branch)", ac.YELLOW)
                ui.message("{} {} -> {}{}".format(
                    ac.color_word("create", ac.GREEN),
                    label, dest, extra,
                ))
            elif status == "notneeded":
                if dry_run:
                    ui.message("{} {} {} -> {}".format(
                        ac.color_word("create", ac.GREEN),
                        ac.color_word("[DRY-RUN]", ac.WHITE),
                        label, dest,
                    ))
                else:
                    ui.message("{}   {} ({})".format(
                        ac.color_word("skip", ac.YELLOW),
                        label, skip_reason,
                    ))
            else:
                ui.message("{} {}: {}".format(
                    ac.color_word("error", ac.RED),
                    label, res.get("message", ""),
                ))

        @staticmethod
        def custom_result_summary_renderer(results):
            worktree_root = None
            created = 0
            skipped = 0
            dry_run_count = 0
            is_dry_run = False
            for res in results:
                if res.get("action") != "worktree-add":
                    continue
                if worktree_root is None and res.get("worktree_root"):
                    worktree_root = res["worktree_root"]
                if res.get("dry_run"):
                    is_dry_run = True
                if res.get("container") or res.get("mtimes"):
                    continue  # not a worktree, don't count it as one
                if res.get("status") == "ok":
                    created += 1
                if res.get("status") == "notneeded":
                    if res.get("dry_run"):
                        dry_run_count += 1
                    else:
                        skipped += 1

            if worktree_root:
                if is_dry_run:
                    parts = [f"{dry_run_count} would be created"]
                else:
                    parts = [f"{created} created"]
                if skipped:
                    parts.append(f"{skipped} skipped")
                ui.message(f"{', '.join(parts)} at {worktree_root}")

        _params_ = dict(
            branch=Parameter(
                args=("branch",),
                doc="Branch name to create/checkout in every worktree",
                constraints=EnsureStr(),
            ),
            worktree_path=Parameter(
                args=("worktree_path",),
                doc="Full path for the superdataset worktree",
                constraints=EnsureStr(),
            ),
            dataset=Parameter(
                args=("-d", "--dataset"),
                doc="Path to the superdataset (default: current directory)",
                constraints=EnsureStr() | EnsureNone(),
            ),
            no_create_branch=Parameter(
                args=("--no-create-branch",),
                doc="Fail if the branch doesn't exist instead of creating it",
                action="store_true",
                default=False,
            ),
            force=Parameter(
                args=("-f", "--force"),
                doc="Pass --force to git worktree add",
                action="store_true",
                default=False,
            ),
            dry_run=Parameter(
                args=("-n", "--dry-run"),
                doc="Show what would be done without doing it",
                action="store_true",
                default=False,
            ),
            no_bindpaths=Parameter(
                args=("--no-bindpaths",),
                doc="""Don't configure container bind mounts for
                datalad containers-run""",
                action="store_true",
                default=False,
            ),
            no_mtimes=Parameter(
                args=("--no-mtimes",),
                doc="""Don't copy file mtimes from the source working
                trees into the created worktrees""",
                action="store_true",
                default=False,
            ),
        )

        @staticmethod
        @eval_results
        def __call__(
            branch,
            worktree_path,
            dataset=None,
            no_create_branch=False,
            force=False,
            dry_run=False,
            no_bindpaths=False,
            no_mtimes=False,
        ):
            from datalad.distribution.dataset import require_dataset

            from datalad_worktree.add import create_nested_worktrees
            from datalad_worktree.core import SKIPPED_RESULTS, WorktreeResult

            ds = require_dataset(
                dataset,
                check_installed=True,
                purpose="create nested worktrees",
            )

            superds_path = Path(ds.path)
            worktree_root = Path(worktree_path).resolve()

            for report in create_nested_worktrees(
                superds_path=superds_path,
                worktree_path=Path(worktree_path),
                branch=branch,
                create_branch=not no_create_branch,
                force=force,
                dry_run=dry_run,
                configure_containers=not no_bindpaths,
                preserve_mtimes=not no_mtimes,
            ):
                if report.result == WorktreeResult.STARTING:
                    # Progress indicator — render directly, don't yield
                    # as a DataLad result (it's not a final state)
                    ui.message(f"  ...  {report.dataset_path}")
                    continue

                if report.result in (
                    WorktreeResult.CREATED,
                    WorktreeResult.CREATED_NEW_BRANCH,
                    WorktreeResult.CONFIGURED,
                    WorktreeResult.MTIMES_SYNCED,
                ):
                    status = "ok"
                elif report.result in SKIPPED_RESULTS:
                    status = "notneeded"
                else:
                    status = "error"

                skip_reason = ""
                if report.result == WorktreeResult.SKIPPED_NOT_INSTALLED:
                    skip_reason = "not installed"
                elif report.result == WorktreeResult.SKIPPED_NOT_GIT_REPO:
                    skip_reason = "not a git repo"
                elif report.result == WorktreeResult.SKIPPED_CONTAINER:
                    skip_reason = report.message

                yield get_status_dict(
                    action="worktree-add",
                    ds=ds,
                    path=str(report.destination),
                    status=status,
                    message=report.message,
                    source=str(report.source),
                    dataset_path=report.dataset_path,
                    branch=report.branch,
                    new_branch=report.result == WorktreeResult.CREATED_NEW_BRANCH,
                    skip_reason=skip_reason,
                    dry_run=report.result == WorktreeResult.SKIPPED_DRY_RUN,
                    container_config=report.result == WorktreeResult.CONFIGURED,
                    mtimes=report.result == WorktreeResult.MTIMES_SYNCED,
                    container=report.result in (
                        WorktreeResult.CONFIGURED,
                        WorktreeResult.SKIPPED_CONTAINER,
                    ),
                    worktree_root=str(worktree_root),
                    type="dataset",
                )

    # ── worktree-list ────────────────────────────────────────────────────

    @build_doc
    class WorktreeList(Interface):
        """List all worktrees across a DataLad dataset hierarchy.

        Shows all git worktrees for the superdataset and every installed
        subdataset. Only datasets with additional worktrees (beyond the
        main working directory) are shown.

        Examples::

            datalad worktree-list
            datalad worktree-list -d /data/my-superdataset
        """

        @staticmethod
        def custom_result_renderer(res, **kwargs):
            # Grouped output is handled entirely by the summary renderer.
            if res["action"] != "worktree-list":
                default_result_renderer(res)

        @staticmethod
        def custom_result_summary_renderer(results):
            from datalad_worktree.list_cmd import column_width, group_by_branch

            entries = (
                (
                    res.get("dataset_path", "."),
                    res.get("path", ""),
                    res.get("branch", "") or "(detached)",
                    res.get("is_main", False),
                )
                for res in results
                if res.get("action") == "worktree-list"
            )
            main_group, branch_groups, super_branch = group_by_branch(entries)

            if not main_group and not branch_groups:
                return

            col_width = column_width(main_group, branch_groups)

            if main_group:
                header = super_branch or "(unknown)"
                ui.message(ac.color_word(header, ac.GREEN))
                for ds_path, wt_path, branch in main_group:
                    annotation = ""
                    if branch != super_branch:
                        annotation = ac.color_word(
                            f" ({branch})", ac.WHITE,
                        )
                    ui.message("  {:<{}}{}{}".format(
                        ds_path, col_width, wt_path, annotation,
                    ))

            for branch in sorted(branch_groups):
                ui.message(ac.color_word(branch, ac.GREEN))
                for ds_path, wt_path in branch_groups[branch]:
                    ui.message("  {:<{}}{}".format(
                        ds_path, col_width, wt_path,
                    ))

        _params_ = dict(
            dataset=Parameter(
                args=("-d", "--dataset"),
                doc="Path to the superdataset (default: current directory)",
                constraints=EnsureStr() | EnsureNone(),
            ),
        )

        @staticmethod
        @eval_results
        def __call__(dataset=None):
            from datalad.distribution.dataset import require_dataset

            from datalad_worktree.list_cmd import list_nested_worktrees

            ds = require_dataset(
                dataset,
                check_installed=True,
                purpose="list worktrees",
            )

            for ds_wt in list_nested_worktrees(Path(ds.path)):
                extra_worktrees = [w for w in ds_wt.worktrees if not w.bare]
                if len(extra_worktrees) <= 1:
                    continue

                for wt in extra_worktrees:
                    is_main = wt.path.resolve() == ds_wt.source.resolve()
                    yield get_status_dict(
                        action="worktree-list",
                        ds=ds,
                        path=str(wt.path),
                        status="ok",
                        dataset_path=ds_wt.dataset_path,
                        branch=wt.branch or "",
                        commit=wt.commit,
                        is_main=is_main,
                        type="dataset",
                    )

    # ── worktree-delete ──────────────────────────────────────────────────

    @build_doc
    class WorktreeDelete(Interface):
        """Delete nested worktrees by path or branch name.

        Accepts either a worktree path or a branch name. Deletes the
        corresponding worktree from each dataset in the hierarchy.
        Datasets that don't have a matching worktree are skipped.

        Examples::

            # Delete by path
            datalad worktree-delete /tmp/wt/my-feature

            # Delete by branch name
            datalad worktree-delete feature/x

            # Also delete the branch
            datalad worktree-delete --delete-branch feature/x
        """

        @staticmethod
        def custom_result_renderer(res, **kwargs):
            if res["action"] != "worktree-delete":
                default_result_renderer(res)
                return
            status = res.get("status", "")
            label = res.get("dataset_path", ".")
            dest = res.get("path", "")

            if status == "ok":
                if res.get("branch_deleted"):
                    ui.message("{} {} branch '{}'".format(
                        ac.color_word("delete", ac.GREEN),
                        label, res.get("branch", ""),
                    ))
                else:
                    ui.message("{} {} -> {}".format(
                        ac.color_word("delete", ac.GREEN),
                        label, dest,
                    ))
            elif status == "notneeded":
                ui.message("{}   {} ({})".format(
                    ac.color_word("skip", ac.YELLOW),
                    label, res.get("message", ""),
                ))
            else:
                ui.message("{} {}: {}".format(
                    ac.color_word("error", ac.RED),
                    label, res.get("message", ""),
                ))

        @staticmethod
        def custom_result_summary_renderer(results):
            deleted = 0
            skipped = 0
            for res in results:
                if res.get("action") != "worktree-delete":
                    continue
                if res.get("status") == "ok" and not res.get("branch_deleted"):
                    deleted += 1
                elif res.get("status") == "notneeded":
                    skipped += 1
            parts = [f"{deleted} deleted"]
            if skipped:
                parts.append(f"{skipped} skipped")
            ui.message(", ".join(parts))

        _params_ = dict(
            target=Parameter(
                args=("target",),
                doc="Worktree path or branch name to delete",
                constraints=EnsureStr(),
            ),
            dataset=Parameter(
                args=("-d", "--dataset"),
                doc="Path to the superdataset (default: current directory)",
                constraints=EnsureStr() | EnsureNone(),
            ),
            delete_branch=Parameter(
                args=("--delete-branch",),
                doc="Also delete the branch (safe delete; refuses if unmerged)",
                action="store_true",
                default=False,
            ),
            force=Parameter(
                args=("-f", "--force"),
                doc="Force deletion even with uncommitted changes; "
                    "force-delete branch",
                action="store_true",
                default=False,
            ),
        )

        @staticmethod
        @eval_results
        def __call__(
            target,
            dataset=None,
            delete_branch=False,
            force=False,
        ):
            from datalad.distribution.dataset import require_dataset

            from datalad_worktree.core import WorktreeResult
            from datalad_worktree.delete import delete_nested_worktrees

            ds = require_dataset(
                dataset,
                check_installed=True,
                purpose="delete nested worktrees",
            )

            for report in delete_nested_worktrees(
                superds_path=Path(ds.path),
                target=target,
                delete_branch=delete_branch,
                force=force,
            ):
                if report.result == WorktreeResult.DELETED:
                    status = "ok"
                elif report.result == WorktreeResult.DELETED_BRANCH:
                    status = "ok"
                elif report.result == WorktreeResult.SKIPPED_NO_WORKTREE:
                    status = "notneeded"
                else:
                    status = "error"

                yield get_status_dict(
                    action="worktree-delete",
                    ds=ds,
                    path=str(report.destination),
                    status=status,
                    message=report.message,
                    dataset_path=report.dataset_path,
                    branch=report.branch,
                    branch_deleted=report.result == WorktreeResult.DELETED_BRANCH,
                    type="dataset",
                )

    # ── worktree-sync-mtimes ─────────────────────────────────────────────

    @build_doc
    class WorktreeSyncMtimes(Interface):
        """Copy file mtimes from the main working trees into a worktree.

        ``worktree-add`` does this once at creation. Anything that rewrites
        files afterwards -- ``datalad get``, a merge, a ``git checkout`` --
        gives them fresh mtimes again, which makes a make-style pipeline
        (Snakemake, Make, redo) rerun work that is already done. This
        restores them.

        Only content that is byte-identical in both trees is touched, so a
        file that genuinely differs keeps its own timestamp.

        Examples::

            # Sync the worktree on branch 'runs', from the superdataset
            datalad worktree-sync-mtimes runs

            # Sync the worktree in the current directory
            datalad worktree-sync-mtimes

            # Name both sides explicitly
            datalad worktree-sync-mtimes --from /data/super /tmp/wt
        """

        @staticmethod
        def custom_result_renderer(res, **kwargs):
            if res["action"] != "worktree-sync-mtimes":
                default_result_renderer(res)
                return

            label = res.get("dataset_path", "") or "."
            if res.get("status") == "ok":
                ui.message("{} {} ({})".format(
                    ac.color_word("mtimes", ac.GREEN),
                    label, res.get("message", ""),
                ))
            elif res.get("status") == "notneeded":
                ui.message("{}   {} ({})".format(
                    ac.color_word("skip", ac.YELLOW),
                    label, res.get("message", ""),
                ))
            else:
                ui.message("{} {}: {}".format(
                    ac.color_word("error", ac.RED),
                    label, res.get("message", ""),
                ))

        @staticmethod
        def custom_result_summary_renderer(results):
            synced = sum(
                1 for res in results
                if res.get("action") == "worktree-sync-mtimes"
                and res.get("status") == "ok"
            )
            ui.message(f"{synced} datasets synced")

        _params_ = dict(
            target=Parameter(
                args=("target",),
                nargs="?",
                doc="""Worktree path or branch name (default: current
                directory)""",
                constraints=EnsureStr() | EnsureNone(),
            ),
            dataset=Parameter(
                args=("-d", "--dataset"),
                doc="""Dataset to resolve a branch name against (default:
                current directory)""",
                constraints=EnsureStr() | EnsureNone(),
            ),
            reference=Parameter(
                args=("--from",),
                dest="reference",
                doc="""Reference working tree to copy from (default: the
                main working tree this worktree was created from)""",
                constraints=EnsureStr() | EnsureNone(),
            ),
        )

        @staticmethod
        @eval_results
        def __call__(target=None, dataset=None, reference=None):
            from datalad_worktree.core import SKIPPED_RESULTS, WorktreeResult
            from datalad_worktree.mtimes import (
                resolve_worktree_target,
                sync_nested_mtimes,
            )

            root = resolve_worktree_target(
                target=target,
                dataset=Path(dataset) if dataset else None,
            )

            for report in sync_nested_mtimes(
                worktree_path=root,
                reference=Path(reference) if reference else None,
            ):
                if report.result == WorktreeResult.MTIMES_SYNCED:
                    status = "ok"
                elif report.result in SKIPPED_RESULTS:
                    status = "notneeded"
                else:
                    status = "error"

                yield get_status_dict(
                    action="worktree-sync-mtimes",
                    path=str(report.destination),
                    status=status,
                    message=report.message,
                    source=str(report.source),
                    dataset_path=report.dataset_path,
                    branch=report.branch,
                    type="dataset",
                )

except ImportError:
    logger.debug(
        "DataLad not available; datalad worktree commands not registered"
    )

    class WorktreeAdd:
        """Placeholder when DataLad is not installed."""
        def __call__(self, *args, **kwargs):
            raise RuntimeError(
                "DataLad is not installed. Use the standalone CLI: worktree"
            )

    class WorktreeList:
        """Placeholder when DataLad is not installed."""
        def __call__(self, *args, **kwargs):
            raise RuntimeError(
                "DataLad is not installed. Use the standalone CLI: worktree"
            )

    class WorktreeDelete:
        """Placeholder when DataLad is not installed."""
        def __call__(self, *args, **kwargs):
            raise RuntimeError(
                "DataLad is not installed. Use the standalone CLI: worktree"
            )

    class WorktreeSyncMtimes:
        """Placeholder when DataLad is not installed."""
        def __call__(self, *args, **kwargs):
            raise RuntimeError(
                "DataLad is not installed. Use the standalone CLI: worktree"
            )
