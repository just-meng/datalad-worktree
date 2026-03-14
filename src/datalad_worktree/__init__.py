"""DataLad extension for creating nested git worktrees across dataset hierarchies."""

__version__ = "0.2.1"

# DataLad extension registration
# This tuple tells datalad where to find the command suite:
#   (description, list of (module_path, class_name, command_name))
command_suite = (
    "Create nested git worktrees for DataLad dataset hierarchies",
    [
        (
            "datalad_worktree.dl_command",
            "WorktreeCreate",
            "worktree",
        ),
    ],
)
