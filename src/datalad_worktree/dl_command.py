"""
DataLad command interface for worktree.

This allows calling the tool as:
  datalad worktree <worktree-path> <branch>

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

    @build_doc
    class WorktreeCreate(Interface):
        """Create nested git worktrees for a DataLad dataset hierarchy.

        This command creates a git worktree for the superdataset and every
        installed subdataset, mirroring the nested structure under a new
        root directory. Each worktree checks out (or creates) the specified
        branch.

        Examples::

            # Create worktrees under /tmp/wt/my-feature on branch 'feature/x'
            datalad worktree /tmp/wt feature/x

            # Dry run
            datalad worktree --dry-run /tmp/wt dev/experiment
        """

        @staticmethod
        def custom_result_renderer(res, **kwargs):
            if res["action"] != "worktree":
                default_result_renderer(res)
                return
            status = res.get("status", "")
            dest = res.get("path", "")
            dataset_path = res.get("dataset_path", "")
            label = dataset_path or "."
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
                    ui.message("{}   {} -> {} ({})".format(
                        ac.color_word("skip", ac.YELLOW),
                        label, dest, skip_reason,
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
                if res.get("action") != "worktree":
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

            from datalad_worktree.core import (
                WorktreeResult,
                create_nested_worktrees,
            )

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

                # Determine skip reason for display
                skip_reason = ""
                if report.result == WorktreeResult.SKIPPED_NOT_INSTALLED:
                    skip_reason = "not installed"
                elif report.result == WorktreeResult.SKIPPED_NOT_GIT_REPO:
                    skip_reason = "not a git repo"

                yield get_status_dict(
                    action="worktree",
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

except ImportError:
    logger.debug(
        "DataLad not available; datalad worktree command not registered"
    )

    class WorktreeCreate:
        """Placeholder when DataLad is not installed."""

        def __call__(self, *args, **kwargs):
            raise RuntimeError(
                "DataLad is not installed. Use the standalone CLI: worktree"
            )
