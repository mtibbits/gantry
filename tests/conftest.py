"""Fixtures shared by the test modules."""

import pytest

from page_script.gate import require_node


@pytest.fixture
def node():
    """Skip the test without Node and jsdom, or fail it under CI."""
    require_node()


@pytest.fixture
def git_ready():
    """Skip the test without git, or fail it under CI (#28)."""
    from helpers_history import require_git

    require_git()
