"""Gantry's own WBS.md reader (#8): fixtures, grammar, identity and read_wbs."""

import pytest

from test_samples import ROOT, decode_bytes, git, git_work_tree, needs_git

FIXTURES = ROOT / "tests" / "fixtures" / "wbs"
NOT_UTF8 = FIXTURES / "not_utf8.md"
UTF8_FIXTURES = ("nesting", "fences", "ids", "rows", "refs", "cycle", "priority", "empty")


# --- fixtures ----------------------------------------------------------------


def test_fixture_set():
    names = sorted(p.name for p in FIXTURES.iterdir())
    assert names == sorted([f"{n}.md" for n in UTF8_FIXTURES] + ["not_utf8.md"])


def test_not_utf8_fixture_stays_invalid():
    data = NOT_UTF8.read_bytes()
    with pytest.raises(UnicodeDecodeError) as exc:
        data.decode("utf-8")
    assert exc.value.start == data.index(b"\xe9")


@needs_git
def test_not_utf8_fixture_is_not_text():
    if not git_work_tree(ROOT):
        pytest.skip("the gantry root is not a git work tree")
    result = git(ROOT, "check-attr", "text", "--", "tests/fixtures/wbs/not_utf8.md")
    assert result.returncode == 0, decode_bytes(result.stderr)
    assert decode_bytes(result.stdout).strip().endswith("text: unset")
