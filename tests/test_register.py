"""
Smoke test for the DataLad extension registration.

Mirrors what DataLad's extension loader does at runtime: for each entry in
``command_suite``, import the module and resolve the class. A typo here
would otherwise only surface when someone runs ``datalad -h`` or
``datalad worktree-<cmd>`` with the extension installed.
"""

from __future__ import annotations

import importlib

import pytest

from datalad_worktree import command_suite

try:
    from datalad.interface.base import Interface
    HAS_DATALAD = True
except ImportError:
    HAS_DATALAD = False

pytestmark = pytest.mark.skipif(not HAS_DATALAD, reason="DataLad not installed")


def test_command_suite_shape():
    description, commands = command_suite
    assert isinstance(description, str) and description
    assert commands, "command_suite must declare at least one command"


@pytest.mark.parametrize("module_name,class_name,cmd_name", command_suite[1])
def test_registered_command_resolves(module_name, class_name, cmd_name):
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    assert issubclass(cls, Interface), (
        f"{module_name}.{class_name} is not a DataLad Interface subclass"
    )
    assert cmd_name.startswith("worktree-"), cmd_name
