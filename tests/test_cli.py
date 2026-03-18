"""Tests for the CLI entry point."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import patch

from datalad_worktree.cli import _Colors, _render_report, build_parser, main
from datalad_worktree.core import WorktreeReport, WorktreeResult


class TestBuildParser:
    def test_add_required_args(self):
        parser = build_parser()
        args = parser.parse_args(["add", "/tmp/wt/my-feature", "main"])
        assert args.command == "add"
        assert str(args.worktree_path) == "/tmp/wt/my-feature"
        assert args.branch == "main"

    def test_add_flags_default_false(self):
        parser = build_parser()
        args = parser.parse_args(["add", "/tmp/wt", "b"])
        assert args.dry_run is False
        assert args.force is False
        assert args.no_create_branch is False

    def test_add_all_flags(self):
        parser = build_parser()
        args = parser.parse_args([
            "--no-color",
            "add", "-n", "-f",
            "--no-create-branch",
            "-d", "/data/ds",
            "/tmp/wt", "b",
        ])
        assert args.dry_run is True
        assert args.force is True
        assert args.no_create_branch is True
        assert args.no_color is True
        assert str(args.dataset) == "/data/ds"

    def test_list_command(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert args.command == "list"

    def test_remove_command(self):
        parser = build_parser()
        args = parser.parse_args(["remove", "feat/x"])
        assert args.command == "remove"
        assert args.target == "feat/x"
        assert args.delete_branch is False
        assert args.force is False

    def test_remove_with_flags(self):
        parser = build_parser()
        args = parser.parse_args(["remove", "--delete-branch", "-f", "feat/x"])
        assert args.delete_branch is True
        assert args.force is True


class TestRenderReport:
    """Test _render_report output for all result types."""

    def setup_method(self):
        # Disable colors for predictable output
        _Colors.disable()

    def _make_report(self, result: WorktreeResult, **kwargs) -> WorktreeReport:
        defaults = dict(
            dataset_path="sub-01",
            source=Path("/src/sub-01"),
            destination=Path("/dst/sub-01"),
            branch="feat/x",
            message="",
        )
        defaults.update(kwargs)
        return WorktreeReport(result=result, **defaults)

    def test_created(self, capsys):
        _render_report(self._make_report(WorktreeResult.CREATED))
        out = capsys.readouterr().out
        assert "create" in out
        assert "sub-01" in out
        assert "/dst/sub-01" in out

    def test_created_new_branch(self, capsys):
        _render_report(self._make_report(WorktreeResult.CREATED_NEW_BRANCH))
        out = capsys.readouterr().out
        assert "create" in out
        assert "(new branch)" in out

    def test_skipped_dry_run(self, capsys):
        _render_report(self._make_report(WorktreeResult.SKIPPED_DRY_RUN))
        out = capsys.readouterr().out
        assert "create" in out
        assert "[DRY-RUN]" in out

    def test_skipped_not_installed(self, capsys):
        _render_report(self._make_report(
            WorktreeResult.SKIPPED_NOT_INSTALLED,
            message="not installed",
        ))
        out = capsys.readouterr().out
        assert "skip" in out
        assert "not installed" in out

    def test_skipped_not_git_repo(self, capsys):
        _render_report(self._make_report(
            WorktreeResult.SKIPPED_NOT_GIT_REPO,
            message="not a git repo",
        ))
        out = capsys.readouterr().out
        assert "skip" in out
        assert "not a git repo" in out

    def test_skipped_no_worktree(self, capsys):
        _render_report(self._make_report(
            WorktreeResult.SKIPPED_NO_WORKTREE,
            message="no worktree at /tmp/x",
        ))
        out = capsys.readouterr().out
        assert "skip" in out
        assert "no worktree" in out

    def test_removed(self, capsys):
        _render_report(self._make_report(WorktreeResult.REMOVED))
        out = capsys.readouterr().out
        assert "remove" in out
        assert "sub-01" in out
        assert "/dst/sub-01" in out

    def test_removed_branch(self, capsys):
        _render_report(self._make_report(WorktreeResult.REMOVED_BRANCH))
        out = capsys.readouterr().out
        assert "remove" in out
        assert "branch" in out
        assert "feat/x" in out

    def test_failed(self, capsys):
        _render_report(self._make_report(
            WorktreeResult.FAILED,
            message="git worktree add failed: fatal error",
        ))
        captured = capsys.readouterr()
        assert "error" in captured.err
        assert "fatal error" in captured.err


class TestMainCLI:
    def test_no_subcommand_returns_1(self):
        exit_code = main(["--no-color"])
        assert exit_code == 1

    def test_add_dry_run_succeeds(self, superds: dict):
        exit_code = main([
            "--no-color", "add",
            "--dry-run",
            "-d", str(superds["super"]),
            str(superds["wt_location"] / "test-cli"), "feat/cli",
        ])
        assert exit_code == 0

    def test_add_not_a_repo_returns_1(self, tmp_path):
        exit_code = main([
            "--no-color", "add",
            "-d", str(tmp_path),
            str(tmp_path / "wt"), "b",
        ])
        assert exit_code == 1

    def test_add_full_run(self, superds: dict):
        exit_code = main([
            "--no-color", "add",
            "-d", str(superds["super"]),
            str(superds["wt_location"] / "cli-full"), "feat/cli-full",
        ])
        assert exit_code == 0
        wt = superds["wt_location"] / "cli-full"
        assert wt.is_dir()
        assert (wt / "sub-01").is_dir()
        assert (wt / "sub-01" / "derivatives").is_dir()
        assert (wt / "sub-02").is_dir()

    def test_list_succeeds(self, superds: dict):
        exit_code = main([
            "--no-color", "list",
            "-d", str(superds["super"]),
        ])
        assert exit_code == 0

    def test_remove_by_branch_succeeds(self, superds: dict):
        # First create worktrees
        main([
            "--no-color", "add",
            "-d", str(superds["super"]),
            str(superds["wt_location"] / "rm-test"), "feat/rm-test",
        ])
        # Then remove them
        exit_code = main([
            "--no-color", "remove", "--yes",
            "-d", str(superds["super"]),
            "feat/rm-test",
        ])
        assert exit_code == 0
        assert not (superds["wt_location"] / "rm-test").exists()

    def test_remove_by_path_succeeds(self, superds: dict):
        wt_path = superds["wt_location"] / "rm-path-test"
        main([
            "--no-color", "add",
            "-d", str(superds["super"]),
            str(wt_path), "feat/rm-path",
        ])
        exit_code = main([
            "--no-color", "remove", "--yes",
            "-d", str(superds["super"]),
            str(wt_path),
        ])
        assert exit_code == 0
        assert not wt_path.exists()


class TestCLISummaryOutput:
    """Test that summary lines are printed correctly."""

    def test_add_summary_line(self, superds: dict, capsys):
        main([
            "--no-color", "add",
            "-d", str(superds["super"]),
            str(superds["wt_location"] / "sum-test"), "feat/sum",
        ])
        out = capsys.readouterr().out
        # Summary should mention "4 created"
        assert "4 created" in out

    def test_add_dry_run_summary(self, superds: dict, capsys):
        main([
            "--no-color", "add",
            "--dry-run",
            "-d", str(superds["super"]),
            str(superds["wt_location"] / "sum-dry"), "feat/sum-dry",
        ])
        out = capsys.readouterr().out
        assert "4 would be created" in out

    def test_add_summary_with_skipped(self, superds: dict, capsys):
        """When a subdataset is uninstalled, summary shows skip count."""
        import shutil
        sub02 = superds["sub02"]
        git_entry = sub02 / ".git"
        if git_entry.is_file():
            git_entry.unlink()
        elif git_entry.is_dir():
            shutil.rmtree(git_entry)

        main([
            "--no-color", "add",
            "-d", str(superds["super"]),
            str(superds["wt_location"] / "sum-skip"), "feat/sum-skip",
        ])
        out = capsys.readouterr().out
        assert "skipped" in out

    def test_remove_summary_line(self, superds: dict, capsys):
        main([
            "--no-color", "add",
            "-d", str(superds["super"]),
            str(superds["wt_location"] / "sum-rm"), "feat/sum-rm",
        ])
        # Clear the add output
        capsys.readouterr()

        main([
            "--no-color", "remove", "--yes",
            "-d", str(superds["super"]),
            "feat/sum-rm",
        ])
        out = capsys.readouterr().out
        assert "4 removed" in out

    def test_remove_summary_with_skipped(self, superds: dict, capsys):
        main([
            "--no-color", "remove", "--yes",
            "-d", str(superds["super"]),
            "nonexistent/branch/xyz",
        ])
        out = capsys.readouterr().out
        assert "0 removed" in out
        assert "skipped" in out


class TestRemoveConfirmation:
    """Test the removal confirmation prompt."""

    def test_preview_shown(self, superds: dict, capsys, monkeypatch):
        """Preview lists directories before prompting."""
        main([
            "--no-color", "add",
            "-d", str(superds["super"]),
            str(superds["wt_location"] / "confirm-test"), "feat/confirm",
        ])
        capsys.readouterr()

        # Simulate user typing "n"
        monkeypatch.setattr("builtins.input", lambda _: "n")
        exit_code = main([
            "--no-color", "remove",
            "-d", str(superds["super"]),
            "feat/confirm",
        ])
        out = capsys.readouterr().out
        assert "Will remove" in out
        assert "Proceed?" not in out  # input() swallows the prompt
        assert "Aborted" in out
        assert exit_code == 1
        # Worktrees should still exist
        assert (superds["wt_location"] / "confirm-test").exists()

    def test_confirm_yes_proceeds(self, superds: dict, capsys, monkeypatch):
        main([
            "--no-color", "add",
            "-d", str(superds["super"]),
            str(superds["wt_location"] / "confirm-y"), "feat/confirm-y",
        ])
        capsys.readouterr()

        monkeypatch.setattr("builtins.input", lambda _: "y")
        exit_code = main([
            "--no-color", "remove",
            "-d", str(superds["super"]),
            "feat/confirm-y",
        ])
        assert exit_code == 0
        assert not (superds["wt_location"] / "confirm-y").exists()

    def test_eof_aborts(self, superds: dict, monkeypatch):
        """EOF (piped input) aborts removal."""
        main([
            "--no-color", "add",
            "-d", str(superds["super"]),
            str(superds["wt_location"] / "confirm-eof"), "feat/confirm-eof",
        ])

        def raise_eof(_):
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        exit_code = main([
            "--no-color", "remove",
            "-d", str(superds["super"]),
            "feat/confirm-eof",
        ])
        assert exit_code == 1
        assert (superds["wt_location"] / "confirm-eof").exists()

    def test_delete_branch_shown_in_preview(self, superds: dict, capsys, monkeypatch):
        """--delete-branch is mentioned in the preview."""
        main([
            "--no-color", "add",
            "-d", str(superds["super"]),
            str(superds["wt_location"] / "confirm-br"), "feat/confirm-br",
        ])
        capsys.readouterr()

        monkeypatch.setattr("builtins.input", lambda _: "n")
        main([
            "--no-color", "remove", "--delete-branch",
            "-d", str(superds["super"]),
            "feat/confirm-br",
        ])
        out = capsys.readouterr().out
        assert "delete branch" in out.lower()
        assert "feat/confirm-br" in out
