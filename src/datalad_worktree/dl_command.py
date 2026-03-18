"""
DataLad command interfaces for worktree-add, worktree-list, worktree-remove.

Requires DataLad to be installed.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import datalad.support.ansi_colors as ac
    from datalad.interface.base import Interface, build_doc
    from datalad.interface.results import get_status_dict
    from datalad.interface.utils import default_result_renderer, eval_results
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
            datalad worktree-add /tmp/wt feature/x

            # Dry run
            datalad worktree-add --dry-run /tmp/wt dev/experiment
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

            if status == "ok":
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
            worktree_path=Parameter(
                args=("worktree_path",),
                doc="Full path for the superdataset worktree",
                constraints=EnsureStr(),
            ),
            branch=Parameter(
                args=("branch",),
                doc="Branch name to create/checkout in every worktree",
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
        )

        @staticmethod
        @eval_results
        def __call__(
            worktree_path,
            branch,
            dataset=None,
            no_create_branch=False,
            force=False,
            dry_run=False,
        ):
            from datalad.distribution.dataset import require_dataset

            from datalad_worktree.add import create_nested_worktrees
            from datalad_worktree.core import WorktreeResult

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
            ):
                if report.result in (
                    WorktreeResult.CREATED,
                    WorktreeResult.CREATED_NEW_BRANCH,
                ):
                    status = "ok"
                elif report.result.name.startswith("SKIPPED"):
                    status = "notneeded"
                else:
                    status = "error"

                skip_reason = ""
                if report.result == WorktreeResult.SKIPPED_NOT_INSTALLED:
                    skip_reason = "not installed"
                elif report.result == WorktreeResult.SKIPPED_NOT_GIT_REPO:
                    skip_reason = "not a git repo"

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
            if res["action"] != "worktree-list":
                default_result_renderer(res)
                return
            label = res.get("dataset_path", ".")
            wt_path = res.get("path", "")
            branch = res.get("branch", "")
            is_main = res.get("is_main", False)

            branch_str = branch or "(detached)"
            main_tag = ac.color_word(" (main)", ac.WHITE) if is_main else ""
            ui.message("  {} [{}]{}".format(wt_path, branch_str, main_tag))

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

    # ── worktree-remove ──────────────────────────────────────────────────

    @build_doc
    class WorktreeRemove(Interface):
        """Remove nested worktrees by path or branch name.

        Accepts either a worktree path or a branch name. Removes the
        corresponding worktree from each dataset in the hierarchy.
        Datasets that don't have a matching worktree are skipped.

        Examples::

            # Remove by path
            datalad worktree-remove /tmp/wt/my-feature

            # Remove by branch name
            datalad worktree-remove feature/x

            # Also delete the branch
            datalad worktree-remove --delete-branch feature/x
        """

        @staticmethod
        def custom_result_renderer(res, **kwargs):
            if res["action"] != "worktree-remove":
                default_result_renderer(res)
                return
            status = res.get("status", "")
            label = res.get("dataset_path", ".")
            dest = res.get("path", "")

            if status == "ok":
                if res.get("branch_deleted"):
                    ui.message("{} {} branch '{}'".format(
                        ac.color_word("remove", ac.GREEN),
                        label, res.get("branch", ""),
                    ))
                else:
                    ui.message("{} {} -> {}".format(
                        ac.color_word("remove", ac.GREEN),
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
            removed = 0
            skipped = 0
            for res in results:
                if res.get("action") != "worktree-remove":
                    continue
                if res.get("status") == "ok" and not res.get("branch_deleted"):
                    removed += 1
                elif res.get("status") == "notneeded":
                    skipped += 1
            parts = [f"{removed} removed"]
            if skipped:
                parts.append(f"{skipped} skipped")
            ui.message(", ".join(parts))

        _params_ = dict(
            target=Parameter(
                args=("target",),
                doc="Worktree path or branch name to remove",
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
                doc="Force removal even with uncommitted changes; "
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
            from datalad_worktree.remove import remove_nested_worktrees

            ds = require_dataset(
                dataset,
                check_installed=True,
                purpose="remove nested worktrees",
            )

            for report in remove_nested_worktrees(
                superds_path=Path(ds.path),
                target=target,
                delete_branch=delete_branch,
                force=force,
            ):
                if report.result == WorktreeResult.REMOVED:
                    status = "ok"
                elif report.result == WorktreeResult.REMOVED_BRANCH:
                    status = "ok"
                elif report.result == WorktreeResult.SKIPPED_NO_WORKTREE:
                    status = "notneeded"
                else:
                    status = "error"

                yield get_status_dict(
                    action="worktree-remove",
                    ds=ds,
                    path=str(report.destination),
                    status=status,
                    message=report.message,
                    dataset_path=report.dataset_path,
                    branch=report.branch,
                    branch_deleted=report.result == WorktreeResult.REMOVED_BRANCH,
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

    class WorktreeRemove:
        """Placeholder when DataLad is not installed."""
        def __call__(self, *args, **kwargs):
            raise RuntimeError(
                "DataLad is not installed. Use the standalone CLI: worktree"
            )
