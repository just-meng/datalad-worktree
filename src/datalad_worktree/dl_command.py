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
            path = res.get("path", "")
            source = res.get("source", "")
            msg = res.get("message", "")
            if status == "ok":
                ui.message("{}: {} -> {}".format(
                    ac.color_word("create", ac.GREEN),
                    source, path,
                ))
            elif status == "notneeded":
                ui.message("{}: {}".format(
                    ac.color_word("skip", ac.YELLOW),
                    msg or path,
                ))
            else:
                ui.message("{}: {}".format(
                    ac.color_word("error", ac.RED),
                    msg or path,
                ))

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
            create_branch=Parameter(
                args=("--create-branch",),
                doc="Create the branch if it doesn't exist (default: True)",
                action="store_true",
                default=True,
            ),
            no_create_branch=Parameter(
                args=("--no-create-branch",),
                doc="Fail if the branch doesn't exist",
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
            create_branch=True,
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
            actual_create_branch = create_branch and not no_create_branch

            result = create_nested_worktrees(
                superds_path=superds_path,
                worktree_path=Path(worktree_path),
                branch=branch,
                create_branch=actual_create_branch,
                force=force,
                dry_run=dry_run,
                prefer_datalad=True,
            )

            # Yield DataLad-style result dicts
            for report in result.reports:
                if report.result in (
                    WorktreeResult.CREATED,
                    WorktreeResult.CREATED_NEW_BRANCH,
                ):
                    status = "ok"
                elif report.result.name.startswith("SKIPPED"):
                    status = "notneeded"
                else:
                    status = "error"

                yield get_status_dict(
                    action="worktree",
                    ds=ds,
                    path=str(report.destination),
                    status=status,
                    message=report.message,
                    source=str(report.source),
                    branch=report.branch,
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
