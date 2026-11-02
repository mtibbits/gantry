"""Fixtures shared by the test modules."""

import pytest

from page_script.gate import require_node


@pytest.fixture
def node():
    """Skip the test without Node and jsdom, or fail it under CI."""
    require_node()
