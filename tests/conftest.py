"""Shared fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fixtures import build_environment  # noqa: E402


@pytest.fixture
def environment(tmp_path: Path) -> Path:
    """A synthetic environment containing every format the probes cover."""
    return build_environment(tmp_path / "env")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"
