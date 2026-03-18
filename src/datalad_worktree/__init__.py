"""DataLad extension for managing nested git worktrees across dataset hierarchies."""

__version__ = "0.3.0"

# DataLad extension registration
command_suite = (
    "Manage nested git worktrees for DataLad dataset hierarchies",
    [
        ("datalad_worktree.dl_command", "WorktreeAdd", "worktree-add"),
        ("datalad_worktree.dl_command", "WorktreeList", "worktree-list"),
        ("datalad_worktree.dl_command", "WorktreeRemove", "worktree-remove"),
    ],
)
