"""Gantry's own WBS.md reader (#8): fixtures, grammar, identity and read_wbs."""

from dataclasses import fields

import pytest

from gantry.model import (
    REPORT_KINDS,
    DepRef,
    Leaf,
    ReportEntry,
    Row,
    SiteData,
    meta_values,
)
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


# --- the model ---------------------------------------------------------------


def test_model_field_lists():
    assert [f.name for f in fields(DepRef)] == ["raw", "kind", "site", "issue"]
    assert [f.name for f in fields(Leaf)] == [
        "key", "issue", "outline", "title", "glyph", "state", "open", "priority",
        "deps", "meta", "epic", "line", "estimate", "models", "flags",
    ]
    assert [f.name for f in fields(Row)] == [
        "key", "issue", "outline", "title", "glyph", "state", "line", "parent", "meta",
    ]
    assert [f.name for f in fields(ReportEntry)] == ["site", "line", "kind", "message"]
    assert [f.name for f in fields(SiteData)] == ["leaves", "rows", "links", "held_out", "report"]


def test_report_kinds():
    assert REPORT_KINDS == (
        "no_issue", "duplicate_issue", "key_collision", "unresolved_ref",
        "malformed_ref", "bad_priority", "cycle", "waits_on_cycle",
        "skipped_line", "no_nodes", "site_unreadable",
    )


def test_nodes_are_unhashable():
    leaf = Leaf("Issue-1", "Issue-1", "1", "t", " ", "pending", True, None, (), {}, None, 2)
    row = Row("Issue-2", "Issue-2", "2", "t", " ", "pending", 3, None, {})
    with pytest.raises(TypeError):
        hash(leaf)
    with pytest.raises(TypeError):
        hash(row)


@pytest.mark.parametrize(
    ("raw", "values"),
    [
        ("", []),
        ("Issue-1", ["Issue-1"]),
        ('"a:b"', ["a:b"]),
        ("'x'", ["x"]),
        ("[A, 'B', \"C\"]", ["A", "B", "C"]),
        ("[ , A ,]", ["A"]),
        ("[P1", ["P1"]),
        ('"open', ["open"]),
        ('""', []),
        (None, []),
    ],
)
def test_meta_values_table(raw, values):
    assert meta_values(raw) == values
