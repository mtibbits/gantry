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


@pytest.fixture(scope="session")
def built_sample_history():
    """Rebuild the real, ignored samples/history/out once per session (#28).

    Only the real checkout has the gantry repository above the samples, so
    the sample legs run there; one pytest session per checkout at a time."""
    import io
    from contextlib import redirect_stderr, redirect_stdout

    import sample_history
    from helpers_history import require_git

    require_git()
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = sample_history.main([])
    assert code == 0, err.getvalue()
