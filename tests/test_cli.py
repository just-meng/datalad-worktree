"""Tests for the CLI entry point."""

from __future__ import annotations

from datalad_worktree.cli import build_parser, main


class TestBuildParser:
    def test_required_args(self):
        parser = build_parser()
        args = parser.parse_args(["/tmp/wt/my-feature", "main"])
        assert str(args.worktree_path) == "/tmp/wt/my-feature"
        assert args.branch == "main"

    def test_flags_default_false(self):
        parser = build_parser()
        args = parser.parse_args(["/tmp/wt", "b"])
        assert args.dry_run is False
        assert args.force is False
        assert args.no_create_branch is False
        assert args.no_color is False

    def test_all_flags(self):
        parser = build_parser()
        args = parser.parse_args([
            "-n", "-f",
            "--no-create-branch", "--no-color",
            "-d", "/data/ds",
            "/tmp/wt", "b",
        ])
        assert args.dry_run is True
        assert args.force is True
        assert args.no_create_branch is True
        assert args.no_color is True
        assert str(args.dataset) == "/data/ds"


class TestMainCLI:
    def test_dry_run_succeeds(self, superds: dict):
        exit_code = main([
            "--dry-run", "--no-color",
            "-d", str(superds["super"]),
            str(superds["wt_location"] / "test-cli"), "feat/cli",
        ])
        assert exit_code == 0

    def test_not_a_repo_returns_1(self, tmp_path):
        exit_code = main([
            "--no-color",
            "-d", str(tmp_path),
            str(tmp_path / "wt"), "b",
        ])
        assert exit_code == 1

    def test_full_run(self, superds: dict):
        exit_code = main([
            "--no-color",
            "-d", str(superds["super"]),
            str(superds["wt_location"] / "cli-full"), "feat/cli-full",
        ])
        assert exit_code == 0
        wt = superds["wt_location"] / "cli-full"
        assert wt.is_dir()
        assert (wt / "sub-01").is_dir()
        assert (wt / "sub-01" / "derivatives").is_dir()
        assert (wt / "sub-02").is_dir()
