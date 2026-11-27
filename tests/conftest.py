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
    from helpers_history import require_git, run_sample_history

    require_git()
    code, _, err = run_sample_history()
    assert code == 0, err


@pytest.fixture(scope="session")
def sample_document(tmp_path_factory):
    """devagent's portfolio JSON over a samples copy, made once per session by
    the pinned aggregator (#83): (the document's path, the samples copy).

    Skips without a devagent checkout, or fails under CI (tests/helpers_devagent.py)."""
    from helpers_portfolio import build_sample_document

    return build_sample_document(tmp_path_factory.mktemp("sample_portfolio"))


@pytest.fixture(scope="session")
def real_sample_document():
    """The committed sample document (tests/fixtures/portfolio/samples.json) written to the real
    samples tree's devagent/out/portfolio/portfolio.json, where #12's build will read it (#195);
    once per session, over what a developer's aggregator run left there. Only after require_git
    (skip locally, fail under CI) and sp.guard_out (git ignores devagent/out and tracks nothing
    in it; a Refused fails); an archive copy of the tree, with no git work tree, skips."""
    import sample_portfolio as sp
    from helpers_config import install_sample_document
    from helpers_history import require_git
    from test_samples import ROOT, SAMPLES, git_work_tree

    require_git()
    if not git_work_tree(ROOT):
        pytest.skip("the gantry root is not a git work tree")
    sp.guard_out(SAMPLES)
    return install_sample_document(SAMPLES)


@pytest.fixture(autouse=True)
def _no_devagent_fetch(monkeypatch):
    """No test fetches devagent (#12): an in-process demo step that would clone fails the test.

    tests/test_demo_build.py's `fetches` fixture replaces the clone with a local fake; a
    child demo run always has GANTRY_DEVAGENT_DIR set (tests/helpers_demo.py run_child)."""
    import demo_build

    real = demo_build._git

    def guarded(root, *args):
        if args[:1] == ("clone",):
            pytest.fail("a test would fetch devagent; set GANTRY_DEVAGENT_DIR or use the fetches fixture",
                        pytrace=False)
        return real(root, *args)

    monkeypatch.setattr(demo_build, "_git", guarded)
