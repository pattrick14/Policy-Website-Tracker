"""Fixtures for operations tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from ops_helpers import GitRemote, make_git_remote


@pytest.fixture
def git_remote(tmp_path: Path) -> GitRemote:
    return make_git_remote(tmp_path)
