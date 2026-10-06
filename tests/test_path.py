"""Tests for `worktree <branch>`: print the path of a branch's worktree."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from datalad_worktree.cli import SUBCOMMANDS, build_parser, main
from datalad_worktree.core import git_current_branch


def _path_of(branch: str, cwd: Path, monkeypatch, capsys) -> tuple[int, str, str]:
    monkeypatch.chdir(cwd)
    exit_code = main([branch])
    out, err = capsys.readouterr()
    return exit_code, out, err


def _add(superds: dict, branch: str, capsys) -> Path:
    wt = superds["wt_location"] / branch.replace("/", "-")
    assert main(["add", "-d", str(superds["super"]), branch, str(wt)]) == 0
    capsys.readouterr()
    return wt


def test_subcommands_match_parser():
    """A new subcommand missing from SUBCOMMANDS would be read as a branch."""
    parser = build_parser()
    choices = next(
        a.choices for a in parser._actions
        if isinstance(a, argparse._SubParsersAction)
    )
    assert set(choices) == SUBCOMMANDS


def test_subcommand_name_is_not_a_branch(superds: dict, monkeypatch, capsys):
    """`worktree list` lists, even with a worktree on a branch named `list`."""
    wt = _add(superds, "list", capsys)

    _, out, _ = _path_of("list", superds["super"], monkeypatch, capsys)

    assert out.strip() != str(wt.resolve())
    assert str(wt.resolve()) in out


def test_prints_only_the_path(superds: dict, monkeypatch, capsys):
    """stdout is the path and nothing else, so `cd (worktree runs)` works."""
    wt = _add(superds, "feat/path", capsys)

    exit_code, out, err = _path_of("feat/path", superds["super"], monkeypatch, capsys)

    assert exit_code == 0
    assert out == f"{wt.resolve()}\n"
    assert err == ""


def test_innermost_repo_wins(superds: dict, monkeypatch, capsys):
    """From inside a subdataset, the subdataset's worktree is printed."""
    wt = _add(superds, "feat/inner", capsys)

    _, out, _ = _path_of("feat/inner", superds["sub01_deriv"], monkeypatch, capsys)

    assert Path(out.strip()) == (wt / "sub-01" / "derivatives").resolve()


def test_main_checkout_from_worktree(superds: dict, monkeypatch, capsys):
    """`worktree <main branch>` from inside a worktree leads back home."""
    wt = _add(superds, "feat/home", capsys)
    main_branch = git_current_branch(superds["sub01"])

    _, out, _ = _path_of(main_branch, wt / "sub-01", monkeypatch, capsys)

    assert Path(out.strip()) == superds["sub01"].resolve()


def test_walks_up_past_repo_without_worktree(superds: dict, monkeypatch, capsys):
    """A subdataset with no worktree on the branch defers to its parent."""
    wt = superds["wt_location"] / "super-only"
    subprocess.run(
        ["git", "-C", str(superds["super"]), "worktree", "add", "-b",
         "feat/up", str(wt)],
        check=True, capture_output=True,
    )

    _, out, _ = _path_of("feat/up", superds["sub02"], monkeypatch, capsys)

    assert Path(out.strip()) == wt.resolve()


def test_unknown_branch_fails_with_empty_stdout(superds: dict, monkeypatch, capsys):
    """Nothing on stdout, so a shell `cd` gets no bogus path."""
    exit_code, out, err = _path_of("no-such-branch", superds["super"], monkeypatch, capsys)

    assert exit_code == 1
    assert out == ""
    assert "no-such-branch" in err


def test_outside_a_repo_fails(tmp_path: Path, monkeypatch, capsys):
    exit_code, out, err = _path_of("runs", tmp_path, monkeypatch, capsys)

    assert exit_code == 1
    assert out == ""
    assert "Not inside a git repository" in err
