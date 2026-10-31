"""Gantry's own WBS.md reader (#8): fixtures, grammar, identity and read_wbs."""

import random
import re
import time
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
from gantry.priority import rank
from gantry.wbs_stopgap import MESSAGES, parse_wbs, read_wbs, split_meta, split_title_meta
from commit_check import path_form_hits
from sample_roles import BUSY, HOSTILE_TITLE, OWN_UNIT, SMALL, UNESTIMATED, UNREADABLE
from test_samples import MARSH_BAD_OFFSET, ROOT, decode_bytes, git, git_work_tree, needs_git

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


# --- grammar, identity and the parse-time report -----------------------------


def fixture_text(name):
    return (FIXTURES / f"{name}.md").read_bytes().decode("utf-8")


def parse(name):
    return parse_wbs(fixture_text(name), "alpha")


def by_line(data):
    return {n.line: n for n in (*data.leaves, *data.rows)}


def entries(data, kind):
    return [e for e in data.report if e.kind == kind]


def test_nesting_outline_kind_epic_parent():
    nodes = by_line(parse("nesting"))
    rows = {  # line: (key, outline, parent)
        2: ("Issue-1", "1", None),
        3: ("Issue-2", "1.1", "Issue-1"),
        8: ("2", "2", None),
    }
    leaves = {  # line: (key, outline, epic)
        4: ("Issue-3", "1.1.1", "Issue-1"),
        5: ("1.1.2", "1.1.2", "Issue-1"),
        6: ("Issue-4", "1.2", "Issue-1"),
        7: ("Issue-5", "1.3", "Issue-1"),
        9: ("Issue-6", "2.1", "2"),
        10: ("Issue-7", "2.2", "2"),
        11: ("3", "3", None),
    }
    assert sorted(nodes) == sorted([*rows, *leaves])
    for line, (key, outline, parent) in rows.items():
        node = nodes[line]
        assert isinstance(node, Row), line
        assert (node.key, node.outline, node.parent) == (key, outline, parent), line
    for line, (key, outline, epic) in leaves.items():
        node = nodes[line]
        assert isinstance(node, Leaf), line
        assert (node.key, node.outline, node.epic) == (key, outline, epic), line


def test_rows_listed():
    rows = parse("nesting").rows
    assert [(r.key, r.issue, r.title) for r in rows] == [
        ("Issue-1", "Issue-1", "Foundations"),
        ("Issue-2", "Issue-2", "Pour"),
        ("2", None, "Frame"),
    ]


def test_brace_title_and_unbracketed_list():
    leaf = by_line(parse("nesting"))[10]
    assert leaf.title == "Brace {in} title"
    assert leaf.meta == {"issue": "Issue-7", "satisfies": "a:X", "a": "Y"}


@pytest.mark.parametrize(
    ("body", "meta"),
    [
        ('depends_on: "x, y"', {"depends_on": '"x, y"'}),
        ("depends_on: [a, b], priority: P1", {"depends_on": "[a, b]", "priority": "P1"}),
        ("a: 1, a: 2", {"a": "2"}),
        ("no colon", {}),
        (": x", {}),
        ("k: [open", {"k": "[open"}),
    ],
)
def test_split_meta_table(body, meta):
    assert split_meta(body) == meta


# devagent's _META_BLOCK_RE (scripts/lib/wbs-parser.py:108), kept here only as
# the oracle for split_title_meta.
DEVAGENT_META_RE = re.compile(r"\s*\{(?P<body>[^{}]*)\}\s*$")


def devagent_split(rest):
    m = DEVAGENT_META_RE.search(rest)
    if not m:
        return rest.strip(), None
    return rest[: m.start()].rstrip(), m.group("body")


META_SPLIT_CASES = [
    "", " ", "a", "a {b: c}", "a {b: c} ", "a{}", "{}", " {} ", "a {b} {c}", "a {b}}",
    "a {{b}", "a {b} c", "a }{", "a {b\t}\t", "{a {b}", "a { }", "a {b: [c, d]}",
]


def test_meta_split_matches_devagent_regex():
    rng = random.Random(8)
    cases = META_SPLIT_CASES + [
        "".join(rng.choice(" \t{}a:") for _ in range(rng.randrange(13))) for _ in range(2000)
    ]
    for rest in cases:
        assert split_title_meta(rest) == devagent_split(rest), repr(rest)


def test_long_whitespace_line_is_linear():
    text = "- [ ] A" + " " * 30000 + "b {issue: Issue-1}\n"
    start = time.perf_counter()
    data = parse_wbs(text, "alpha")
    assert time.perf_counter() - start < 1
    assert [leaf.key for leaf in data.leaves] == ["Issue-1"]


def test_fences():
    data = parse("fences")
    assert [leaf.key for leaf in data.leaves] == ["Issue-1", "Issue-2", "Issue-3"]
    assert data.rows == ()
    assert entries(data, "skipped_line") == []


def test_issue_less_leaf_named_by_outline():
    data = parse("ids")
    leaf = by_line(data)[6]
    assert (leaf.key, leaf.issue) == ("1.4", None)
    assert [e.line for e in entries(data, "no_issue")] == [5, 6]


def test_fork_ids_kept():
    nodes = by_line(parse("ids"))
    assert isinstance(nodes[2], Row) and nodes[2].key == "Issue-Fork-1"
    assert isinstance(nodes[3], Leaf) and nodes[3].key == "Issue-Fork-2"


def test_duplicate_issue():
    data = parse("ids")
    found = entries(data, "duplicate_issue")
    assert [e.line for e in found] == [4]
    assert "line 3" in found[0].message
    leaf = by_line(data)[4]
    assert (leaf.key, leaf.issue) == ("1.2", "Issue-Fork-2")


def test_key_collision():
    data = parse("ids")
    assert [e.message for e in entries(data, "key_collision")] == [
        "WBS.md line 5: outline position '1.3' is the issue: id on line 7; "
        "this node is named '1.3~5'"
    ]
    nodes = by_line(data)
    assert nodes[7].key == "1.3"
    assert nodes[5].key == "1.3~5"


def test_tilde_key_never_duplicates_an_issue():
    data = parse_wbs("- [ ] A\n- [ ] B {issue: 1}\n- [ ] C {issue: 1~1}\n", "alpha")
    assert [leaf.key for leaf in data.leaves] == ["1~1~1", "1", "1~1"]
    assert [e.message for e in entries(data, "key_collision")] == [
        "WBS.md line 1: outline position '1' is the issue: id on line 2; "
        "this node is named '1~1~1'"
    ]


def test_skipped_lines():
    data = parse("ids")
    found = entries(data, "skipped_line")
    assert [e.line for e in found] == [10, 11]
    assert found[0].message.endswith(MESSAGES["why_glyph"].format(glyph="X"))
    assert found[1].message.endswith(MESSAGES["why_text"])
    assert {10, 11}.isdisjoint(by_line(data))

    data = parse_wbs("- [ ]   \n", "alpha")
    assert [(leaf.key, leaf.title) for leaf in data.leaves] == [("1", "")]
    assert [e.kind for e in data.report] == ["no_issue"]

    data = parse_wbs("- [ ] \n", "alpha")
    assert data.leaves == ()
    assert [e.kind for e in data.report] == ["no_nodes", "skipped_line"]  # whole-file first


def test_bad_priority_and_p0():
    data = parse("ids")
    found = entries(data, "bad_priority")
    assert [e.line for e in found] == [3, 4]
    assert "'P0'" in found[0].message and "'P9'" in found[1].message
    nodes = by_line(data)
    assert rank(nodes[3].priority) == rank(nodes[4].priority) == 3


def test_quoted_scalar_and_list_depends_on():
    nodes = by_line(parse("ids"))
    assert nodes[8].deps == (DepRef("Issue-Fork-2", "local", "alpha", "Issue-Fork-2"),)
    assert [d.issue for d in nodes[9].deps] == ["Issue-3", "1.4", "Issue-99"]


def test_no_nodes():
    data = parse("empty")
    assert data.leaves == () and data.rows == ()
    assert [(e.kind, e.line) for e in data.report] == [("no_nodes", None)]


@pytest.mark.parametrize("name", UTF8_FIXTURES)
def test_crlf_parses_like_lf(name):
    text = fixture_text(name)
    assert parse_wbs(text.replace("\n", "\r\n"), "alpha") == parse_wbs(text, "alpha")



# --- resolution over the fixtures and the samples ----------------------------


def test_ids_links_and_unresolved():
    data = parse("ids")
    assert data.links == (("Issue-Fork-2", "Issue-3"), ("Issue-3", "Issue-4"))
    found = entries(data, "unresolved_ref")
    assert [e.line for e in found] == [9, 9]
    assert "'1.4'" in found[0].message and "'Issue-99'" in found[1].message


def sample(site):
    return parse_wbs((ROOT / "samples" / site / "WBS.md").read_bytes().decode("utf-8"), site)


def test_sample_sites_parse():
    busy = sample(BUSY)
    assert busy.report == ()
    assert len(busy.leaves) == 14
    assert ("Issue-6", "Issue-7") in busy.links

    own = sample(OWN_UNIT)
    assert [leaf.key for leaf in own.leaves if leaf.issue is None] == ["1.5", "2.3", "3"]
    found = entries(own, "unresolved_ref")
    assert len(found) == 1 and "'2.1'" in found[0].message

    found = entries(sample(UNESTIMATED), "unresolved_ref")
    assert len(found) == 1 and "'Issue-99'" in found[0].message

    small = sample(SMALL)
    assert HOSTILE_TITLE in [leaf.title for leaf in small.leaves]
    assert next(leaf for leaf in small.leaves if leaf.key == "Issue-5").epic is None


# --- read_wbs ----------------------------------------------------------------


def only_unreadable(data):
    assert (data.leaves, data.rows, data.links, data.held_out) == ((), (), (), ())
    assert [(e.kind, e.line) for e in data.report] == [("site_unreadable", None)]
    return data.report[0].message


def test_read_wbs_not_utf8():
    offset = NOT_UTF8.read_bytes().index(b"\xe9")
    message = only_unreadable(read_wbs(NOT_UTF8, "alpha"))
    assert message == f"WBS.md is not valid UTF-8 at byte {offset}"
    assert path_form_hits(message) == []


def test_read_wbs_unreadable_sample():
    message = only_unreadable(read_wbs(ROOT / "samples" / UNREADABLE / "WBS.md", UNREADABLE))
    assert message == f"WBS.md is not valid UTF-8 at byte {MARSH_BAD_OFFSET}"


@pytest.mark.parametrize("make", ["missing", "directory"])
def test_read_wbs_cannot_open(tmp_path, make):
    path = tmp_path / "WBS.md"
    if make == "directory":
        path.mkdir()
    message = only_unreadable(read_wbs(path, "alpha"))
    assert message.startswith("WBS.md cannot be read: ")
    assert str(tmp_path) not in message
    assert path_form_hits(message) == []


def test_read_wbs_reads_utf8_not_locale(tmp_path):
    path = tmp_path / "WBS.md"
    path.write_bytes("- [ ] Café {issue: Issue-1}\n".encode("utf-8"))
    assert [leaf.title for leaf in read_wbs(path, "alpha").leaves] == ["Café"]
