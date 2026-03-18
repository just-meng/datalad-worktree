"""Tests for the CLI entry point."""

from __future__ import annotations

from datalad_worktree.cli import build_parser, main


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
            "--no-color", "remove",
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
            "--no-color", "remove",
            "-d", str(superds["super"]),
            str(wt_path),
        ])
        assert exit_code == 0
        assert not wt_path.exists()
