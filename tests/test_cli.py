"""Tests for the CLI entry point."""

from __future__ import annotations

from pathlib import Path

import pytest

from datalad_worktree.cli import _Colors, _render_report, main
from datalad_worktree.core import WorktreeReport, WorktreeResult


class TestRenderReport:
    """
    Every outcome is rendered, so no result is silently dropped.

    A new ``WorktreeResult`` that ``_render_report`` forgets prints nothing,
    which is how a failure would go unreported. Failures go to stderr, the
    rest to stdout. STARTING is a progress marker, shown only on a TTY.
    """

    @pytest.mark.parametrize(
        "result", [r for r in WorktreeResult if r != WorktreeResult.STARTING],
        ids=lambda r: r.name,
    )
    def test_every_result_names_the_dataset(self, result, capsys):
        _Colors.disable()
        _render_report(WorktreeReport(
            dataset_path="sub-01", source=Path("/src/sub-01"),
            destination=Path("/dst/sub-01"), result=result,
            branch="feat/x", message="why",
        ))

        out, err = capsys.readouterr()
        shown, silent = (err, out) if result == WorktreeResult.FAILED else (out, err)
        assert "sub-01" in shown
        assert silent == ""


class TestMainCLI:
    def test_bare_invocation_lists(self, superds: dict, monkeypatch, capsys):
        """`worktree` with no subcommand behaves like `worktree list`."""
        main([
            "add",
            "-d", str(superds["super"]),
            "feat/bare", str(superds["wt_location"] / "bare-test"),
        ])
        capsys.readouterr()

        monkeypatch.chdir(superds["super"])
        exit_code = main([])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "feat/bare" in out

    def test_add_not_a_repo_returns_1(self, tmp_path):
        exit_code = main([
            "add",
            "-d", str(tmp_path),
            "b", str(tmp_path / "wt"),
        ])
        assert exit_code == 1


class TestCLISummaryOutput:
    """Test that summary lines are printed correctly."""

    def test_add_and_delete_summary_lines(self, superds: dict, capsys, monkeypatch):
        """delete acts without asking for confirmation."""
        def no_input(_):
            raise AssertionError("delete must not prompt")

        monkeypatch.setattr("builtins.input", no_input)
        main([
            "add",
            "-d", str(superds["super"]),
            "feat/sum", str(superds["wt_location"] / "sum-test"),
        ])
        add_out = capsys.readouterr().out
        assert "4 created" in add_out

        main([
            "delete",
            "-d", str(superds["super"]),
            "feat/sum",
        ])
        rm_out = capsys.readouterr().out
        assert "4 deleted" in rm_out
