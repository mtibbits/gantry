"""The requirements file format and ID scheme (#15).

docs/requirements-format.md states the rules these tests pin.
"""

import re
import time
from pathlib import Path

import pytest

import gantry.config as cfg
from gantry import requirements as req
from gantry.requirements import (
    LAYERS,
    PROJECT_LAYERS,
    REPORT_KINDS,
    ReqId,
    parse_id,
    in_trace_sites,
    parse_requirements,
    read_requirements,
)
from gantry.roots import directory_roots
from gantry.sites import SiteWarning, load_sites
from gantry.sites import config_roots
from helpers_config import write_site_tree
from test_samples import GANTRY_SAMPLE, ROOT, md_tables
from helpers_config import roots_around

# A fictional mixed-case namespace (operator decision Q3); the format doc's
# case rule uses the same name.
MIXED_NS = "harborWest"

FIXTURES = Path(__file__).parent / "fixtures" / "requirements"
ALL = set(LAYERS)


def parse(text, namespace="alpha", *, layers=PROJECT_LAYERS, br="life", source="t.md", first_line=1):
    return parse_requirements(text, namespace, source=source, first_line=first_line, layers=layers,
                              br_namespace=br)


def read(name, namespace="alpha", *, layers=PROJECT_LAYERS, br="life"):
    return read_requirements(directory_roots([FIXTURES]), FIXTURES / name, namespace, layers=layers, br_namespace=br)


def rid(text, namespace="alpha"):
    return parse_id(text, namespace)


# --- the ID grammar ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "default", "full", "fields"),
    [
        ("gantry:FR-12", None, "gantry:FR-12", ("gantry", "FR", 12)),
        ("life:BR-3", None, "life:BR-3", ("life", "BR", 3)),
        (f"{MIXED_NS}:UC-2", None, f"{MIXED_NS}:UC-2", (MIXED_NS, "UC", 2)),
        ("FR-12", "gantry", "gantry:FR-12", ("gantry", "FR", 12)),
        ("BRule-1", "alpha", "alpha:BRule-1", ("alpha", "BRule", 1)),
        ("alpha:CON-999999999", None, "alpha:CON-999999999", ("alpha", "CON", 999999999)),
    ],
)
def test_parse_id_accepts(text, default, full, fields):
    rid = parse_id(text, default)
    assert isinstance(rid, ReqId)
    assert str(rid) == full
    assert tuple(rid) == fields
    assert type(rid.number) is int


@pytest.mark.parametrize(
    ("text", "default"),
    [
        ("FR-012", "gantry"),
        ("FR-0", "gantry"),
        ("fr-12", "gantry"),
        ("NFR-4", "gantry"),
        ("gantry:FR", None),
        ("gantry:FR-1x", None),
        ("gantry: FR-1", None),
        ("a:b:FR-1", None),
        (":FR-1", "gantry"),
        ("FR-12", None),
        ("FR-1", "a b"),
        ("FR-1234567890", "gantry"),
        ("FR-" + "9" * 5000, "gantry"),
        ("", "gantry"),
    ],
)
def test_parse_id_rejects(text, default):
    assert parse_id(text, default) is None


def test_layers_and_default():
    assert LAYERS == ("BR", "BRule", "UC", "SR", "FR", "QA", "EI", "CON")
    assert PROJECT_LAYERS == set(LAYERS) - {"BR"}
    assert req.DEFAULT_BR_NAMESPACE == "life"
    assert req.DEFAULT_BR_NAMESPACE is cfg.DEFAULT_BR_NAMESPACE


# --- the file shape ------------------------------------------------------------

LAYERS_EXPECTED = {
    # bare ID: (layer, line, text)
    "BR-1": ("BR", 7, "Grow the harbor trade."),
    "BRule-1": ("BRule", 8, "A berth holds one vessel at a time."),
    "UC-1": ("UC", 12, "Book a berth."),
    "UC-2": ("UC", 13, "Cancel a booking."),
    "UC-3": ("UC", 14, "Browse the schedule."),
    "SR-1": ("SR", 18, "The system keeps a berth register."),
    "FR-1": ("FR", 19, "The register lists every berth with its length and its depth - sub point"),
    "FR-2": ("FR", 23, "Bookings are checked against length. This line continues the text."),
    "FR-3": ("FR", 25, "Each booking names a vessel and a date."),
    "QA-1": ("QA", 27, "A booking is confirmed within one second."),
    "EI-1": ("EI", 28, "The register is read from a CSV export."),
    "CON-1": ("CON", 29, "The system runs on the harbor office computer."),
}


def test_reads_every_layer():
    s = read("layers.md", layers=ALL)
    assert s.report == []
    assert s.namespace == "alpha"
    assert list(s.requirements) == [f"alpha:{bare}" for bare in LAYERS_EXPECTED]
    for bare, (layer, line, text) in LAYERS_EXPECTED.items():
        r = s.requirements[f"alpha:{bare}"]
        assert r.id == rid(bare)
        assert (r.layer, r.line, r.text) == (layer, line, text), bare
        assert r.source == str(FIXTURES / "layers.md")
    assert {r.layer for r in s.requirements.values()} == ALL
    got = s.requirements
    assert got["alpha:FR-2"].refines == ReqId("alpha", "SR", 1)
    assert got["alpha:FR-3"].refines == ReqId("alpha", "SR", 1)
    assert got["alpha:FR-1"].refines is None
    assert got["alpha:UC-1"].needs == [rid("FR-1"), rid("QA-1")]
    assert got["alpha:UC-2"].needs == [rid("FR-1")]
    assert got["alpha:UC-3"].needs == []


def test_empty_file_reads_empty():
    for s in (read("empty.md", layers=ALL), parse("")):
        assert s.requirements == {}
        assert s.report == []


CANDIDATE_REPORTS = [
    # (line, head, reason fragment)
    (12, "'FR12:'", "no '-' between"),
    (13, "'Fr-12:'", "'Fr' is not a layer code"),
    (14, "'FR-012:'", "the number '012'"),
    (15, "'NFR-4:'", "'NFR' is not a layer code"),
    (16, "'FR-3a:'", "letters after the number"),
    (17, "'FR-3 :'", "a space before the colon"),
    (18, "'**FR-3:'", "marks around the ID"),
    (19, "'`FR-3`:'", "marks around the ID"),
    (20, "'gantry:FR-12:'", "a namespace"),
    (21, "'FR-3:'", "an ordered list item"),
    (23, "'FR-2:'", "requirements are not nested"),
    (24, "'Step-1:'", "'Step' is not a layer code"),
    (25, "'Q3:'", "'Q' is not a layer code"),
    (26, "'v2:'", "'v' is not a layer code"),
]


def test_prose_not_reported_candidates_are():
    s = read("candidates.md")
    assert [e.kind for e in s.report] == ["malformed_item"] * len(CANDIDATE_REPORTS)
    for entry, (line, head, reason) in zip(s.report, CANDIDATE_REPORTS):
        assert entry.line == line
        assert entry.message.startswith(f"{head} is not a requirement: "), entry
        assert reason in entry.message, entry
        assert entry.source == str(FIXTURES / "candidates.md")
    assert list(s.requirements) == ["alpha:FR-1", "alpha:FR-6", "alpha:FR-5"]
    assert s.requirements["alpha:FR-1"].text == "parent - FR-2: child"
    assert s.requirements["alpha:FR-6"].text == ""
    assert s.requirements["alpha:FR-5"].text == "still read"


def test_every_reason_is_listed_in_order():
    (entry,) = parse("1. **fr-012x :\n").report
    assert entry.message == (
        "'**fr-012x :' is not a requirement: an ordered list item (a requirement uses -, * or +); "
        "marks around the ID (write it plain); a space before the colon; "
        "'fr' is not a layer code (BR, BRule, UC, SR, FR, QA, EI, CON); "
        "the number '012' is not a positive integer without a leading zero; letters after the number"
    )
    (entry,) = parse("- FR-1234567890: x\n").report
    assert "the number has more than 9 digits" in entry.message


def test_line_numbers_and_crlf():
    # U+2028 is a line break to str.splitlines(), never to the reader.
    text = "# T\n\n- FR-1: one\n  two\n- FR-2: a\u2028b\n- FR-3: c\n"
    for s in (parse(text), parse(text.replace("\n", "\r\n"))):
        assert s.report == []
        assert [(r.line, r.text) for r in s.requirements.values()] == [(3, "one two"), (5, "a\u2028b"), (6, "c")]


def test_text_stops_at_blank_and_shallower():
    # An empty line, a whitespace-only line, and a line indented no deeper.
    s = parse("- FR-1: a\n\n  b\n- FR-2: c\n   \n  d\n- FR-3: e\nf\n- FR-4: g\n  h\n")
    texts = [r.text for r in s.requirements.values()]
    assert texts == ["a", "c", "e", "g h"]


def NN(id, n):
    """The report row of a nested ID-shaped item on line n."""
    return (n, "malformed_item", f"'{id}:' is not a requirement: requirements are not nested")


def rows(s):
    return [(e.line, e.kind, e.message) for e in s.report]


def test_indented_item_after_blank_is_nested():
    s = parse("- FR-1: a\n\n  - FR-3: e\n")
    assert list(s.requirements) == ["alpha:FR-1"]
    assert rows(s) == [NN("FR-3", 3)]
    assert s.requirements["alpha:FR-1"].text == "a"


def test_second_indented_sibling_after_blank_is_nested():
    s = parse("- FR-1: a\n\n  - FR-2: b\n\n  - FR-3: c\n")
    assert list(s.requirements) == ["alpha:FR-1"]
    assert rows(s) == [NN("FR-2", 3), NN("FR-3", 5)]


@pytest.mark.parametrize(
    ("text", "keys", "id", "line"),
    [
        ("- FR-1: a\n\n  more\n\n  - FR-2: b\n", ["alpha:FR-1"], "FR-2", 5),
        ("- FR-1: a\n\n  ```text\n  x\n  ```\n\n  - FR-2: b\n", ["alpha:FR-1"], "FR-2", 7),
        ("- FR-1: a\n\n\t- FR-2: b\n", ["alpha:FR-1"], "FR-2", 3),
        ("- FR-1: a\n\n  <!-- x -->\n\n  - FR-2: b\n", ["alpha:FR-1"], "FR-2", 5),
        ("- Functional requirements\n\n  - FR-1: x\n", [], "FR-1", 3),
        ("-   FR-1: a\n\n    - FR-2: b\n", ["alpha:FR-1"], "FR-2", 3),
    ],
)
def test_enclosing_item_spans_blank_lines(text, keys, id, line):
    s = parse(text)
    assert list(s.requirements) == keys
    assert rows(s) == [NN(id, line)]


@pytest.mark.parametrize(
    ("text", "keys"),
    [
        ("- FR-1: a\n\nProse.\n\n  - FR-2: b\n", ["alpha:FR-1", "alpha:FR-2"]),
        ("- FR-1: a\n\n## B\n\n  - FR-2: b\n", ["alpha:FR-1", "alpha:FR-2"]),
        ("- FR-1: a\n\n```text\nx\n```\n\n  - FR-2: b\n", ["alpha:FR-1", "alpha:FR-2"]),
        ("- FR-1: a\n\n<!-- x -->\n\n  - FR-2: b\n", ["alpha:FR-1", "alpha:FR-2"]),
        ("- FR-1: a\n\n - FR-2: b\n", ["alpha:FR-1", "alpha:FR-2"]),
        ("-   FR-1: a\n\n  - FR-2: b\n", ["alpha:FR-1", "alpha:FR-2"]),
        ("1. Section\n\n  - FR-2: b\n", ["alpha:FR-2"]),
    ],
)
def test_item_below_the_content_column_is_a_sibling(text, keys):
    s = parse(text)
    assert list(s.requirements) == keys
    assert rows(s) == []


def test_item_under_a_prose_bullet_is_nested():
    s = parse("- Functional requirements\n  - FR-1: x\n")
    assert s.requirements == {}
    (entry,) = s.report
    assert (entry.line, entry.kind) == (2, "malformed_item")
    assert "requirements are not nested" in entry.message


def test_nested_items_block_is_not_the_parents():
    s = parse("- FR-1: parent\n  - FR-2: child {refines: SR-1}\n- SR-1: y\n")
    assert s.requirements["alpha:FR-1"].refines is None
    assert "alpha:FR-2" not in s.requirements
    assert any(e.line == 2 and "requirements are not nested" in e.message for e in s.report)


def test_tab_indent_counts_as_four():
    assert parse("- FR-1: a\n\tb\n").requirements["alpha:FR-1"].text == "a b"


def test_unclosed_fence_is_text():
    s = parse("```text\n- FR-1: read\n\n- FR-2: read\n")
    assert list(s.requirements) == ["alpha:FR-1", "alpha:FR-2"]


def test_fences_hide_and_pair():
    text = "````\n```\n- FR-1: hidden\n```\n- FR-2: hidden\n````\n~~~\n- FR-3: hidden\n~~~\n- FR-4: read\n"
    assert list(parse(text).requirements) == ["alpha:FR-4"]
    # A backtick opener whose info holds a backtick is not an opener.
    assert list(parse("``` a`b\n- FR-1: read\n```\n").requirements) == ["alpha:FR-1"]
    # A fenced line ends the text before it.
    s = parse("- FR-1: a\n  ```\n  b\n  ```\n  c\n")
    assert s.requirements["alpha:FR-1"].text == "a"



def test_fence_spans():
    # Back to back: two spans, not one run.
    assert req.fence_spans(["```", "a", "```", "```toml", "b", "```"]) == [(0, 2), (3, 5)]
    # An opener with no closer is plain text.
    assert req.fence_spans(["```", "a"]) == []
    # A four-backtick fence holding a three-backtick line is one span.
    assert req.fence_spans(["````", "```", "x", "```", "````"]) == [(0, 4)]


# --- HTML comments ----------------------------------------------------------------

UNCLOSED = "an HTML comment with no closing '-->' hides every line after it"
LEAKED = "an HTML comment whose list item ends before its '-->' hides every line after it"
INLINE = "an HTML comment inside the text; it stays in the text and hides nothing: put it on its own line"
BEFORE_ID = "an HTML comment before the ID; the item is not read: put the comment on its own line"


def MC(n, message):
    """The report row of a malformed comment on line n."""
    return (n, "malformed_comment", message)


@pytest.mark.parametrize(
    "text",
    [
        "- FR-1: a\n<!--\n- FR-2: b\n-->\n- FR-3: c\n",
        "- FR-1: a\n<!--\n- FR-2: b -->\n- FR-3: c\n",
        "- FR-1: a\n<!--\n- FR12: typo\n-->\n- FR-3: c\n",
        "- FR-1: a\n <!--\n- FR-2: b\n-->\n- FR-3: c\n",
    ],
)
def test_block_comment_hides_items(text):
    s = parse(text)
    assert list(s.requirements) == ["alpha:FR-1", "alpha:FR-3"]
    assert rows(s) == []


@pytest.mark.parametrize("sp", [" ", "  ", "   "])
def test_comment_opener_indented_up_to_three_spaces(sp):
    s = parse(f"{sp}<!--\n- FR-2: b\n-->\n- FR-3: c\n")
    assert list(s.requirements) == ["alpha:FR-3"]
    assert rows(s) == []


@pytest.mark.parametrize("opener", ["    <!--", "\t<!--"])
def test_comment_opener_indented_four_is_not_a_block(opener):
    s = parse(opener + "\n- FR-2: b\n-->\n")
    assert list(s.requirements) == ["alpha:FR-2"]
    assert rows(s) == []


@pytest.mark.parametrize(
    "text",
    [
        "- FR-1: The page\n  <!-- note -->\n  lists checks.\n",
        "- FR-1: The page\n  <!--\n  note\n  -->\n  lists checks.\n",
        "- FR-1: The page\n  <!--\n\n  -->\n  lists checks.\n",
    ],
)
def test_comment_line_in_the_text_is_skipped(text):
    s = parse(text)
    assert list(s.requirements) == ["alpha:FR-1"]
    assert rows(s) == []
    assert s.requirements["alpha:FR-1"].text == "The page lists checks."


def test_comment_in_the_text_hides_a_nested_item():
    s = parse("- FR-1: a\n  <!--\n  - FR-2: x\n  -->\n")
    assert list(s.requirements) == ["alpha:FR-1"]
    assert rows(s) == []
    assert s.requirements["alpha:FR-1"].text == "a"


@pytest.mark.parametrize(
    ("text", "keys", "fr1", "report"),
    [
        ("- FR-1: a <!-- x --> b\n", ["alpha:FR-1"], "a <!-- x --> b", [MC(1, INLINE)]),
        ("- FR-1: a\n  b <!-- x -->\n", ["alpha:FR-1"], "a b <!-- x -->", [MC(2, INLINE)]),
        ("- FR-1: a <!-- x\n  - FR-2: y -->\n", ["alpha:FR-1"], "a <!-- x - FR-2: y -->",
         [MC(1, INLINE), NN("FR-2", 2)]),
        ("- FR-1: <!--> b\n", ["alpha:FR-1"], "<!--> b", [MC(1, INLINE)]),
        ("- FR-1: a <!-- b\n- FR-2: c -->\n", ["alpha:FR-1", "alpha:FR-2"], "a <!-- b", [MC(1, INLINE)]),
        ("- FR-1: a \\<!-- x --> b\n", ["alpha:FR-1"], "a \\<!-- x --> b", [MC(1, INLINE)]),
        ("-   FR-1: a\n    <!-- x -->\n", ["alpha:FR-1"], "a <!-- x -->", [MC(2, INLINE)]),
    ],
)
def test_comment_in_the_text_is_reported(text, keys, fr1, report):
    s = parse(text)
    assert list(s.requirements) == keys
    assert s.requirements["alpha:FR-1"].text == fr1
    assert rows(s) == report


def test_comment_in_a_code_span_is_not_reported():
    s = parse("- FR-1: escapes `<!--` and `-->` here\n")
    assert s.requirements["alpha:FR-1"].text == "escapes `<!--` and `-->` here"
    assert rows(s) == []


def test_comment_after_the_meta_block_is_reported():
    s = parse("- FR-1: a {refines: SR-1} <!-- note -->\n- SR-1: s\n")
    fr1 = s.requirements["alpha:FR-1"]
    assert fr1.text == "a {refines: SR-1} <!-- note -->"
    assert fr1.refines is None
    assert rows(s) == [(1, "malformed_meta", "a meta block inside the text; it stays in the text and is not read"),
                       MC(1, INLINE)]


@pytest.mark.parametrize(
    ("text", "keys", "report"),
    [
        ("- <!-- x --> FR-1: y\n", [], [MC(1, BEFORE_ID)]),
        ("- <!-- x\n- FR-2: y\n", [], [MC(1, LEAKED)]),
        ("- <!-- x\n  y -->\n- FR-2: y\n", ["alpha:FR-2"], []),
        ("- <!-- x --> prose\n", [], []),
    ],
)
def test_comment_at_the_start_of_an_item(text, keys, report):
    s = parse(text)
    assert list(s.requirements) == keys
    assert rows(s) == report


@pytest.mark.parametrize(
    ("text", "keys", "report"),
    [
        ("- FR-1: a\n<!--\n- FR-2: b\n", ["alpha:FR-1"], [MC(2, UNCLOSED)]),
        ("- FR-1: a\n  <!-- note\n  more\n", ["alpha:FR-1"], [MC(2, UNCLOSED)]),
        ("- FR-1: a\n  <!-- note\n- FR-2: b\n  -->\n- FR-3: c\n", ["alpha:FR-1"], [MC(2, LEAKED)]),
        ("- FR-1: a\n  <!-- note\n- FR-2: b\n-->\n- FR-3: c\n", ["alpha:FR-1"], [MC(2, LEAKED)]),
        ("- Notes\n  - <!-- x\n  - more\n    -->\n- FR-3: c\n", [], [MC(2, LEAKED)]),
        ("<!--\n- FR12: typo\n", [], [MC(1, UNCLOSED)]),
    ],
)
def test_unclosed_comment_hides_every_line_after_it(text, keys, report):
    s = parse(text)
    assert list(s.requirements) == keys
    assert rows(s) == report
    if keys:
        assert s.requirements["alpha:FR-1"].text == "a"


@pytest.mark.parametrize(
    ("text", "keys"),
    [
        ("<!--\n```\n-->\n- FR-1: a\n```\n- FR-2: b\n", ["alpha:FR-1", "alpha:FR-2"]),
        ("<!--\n```\n-->\n- FR-1: a\n```\n- FR-2: b\n```\n", ["alpha:FR-1"]),
        ("```\n<!--\n```\n- FR-1: a\n", ["alpha:FR-1"]),
    ],
)
def test_fences_and_comments_pair_in_document_order(text, keys):
    s = parse(text)
    assert list(s.requirements) == keys
    assert rows(s) == []


def test_hostile_comment_files_are_fast():
    texts = [
        "<!-- x -->\n" * 50_000,
        "- a\n  <!--\n" * 25_000,
        "- FR-1: a <!--\n" + "  b\n" * 50_000,
        "<!--\n" * 50_000,
        "<!--\n```\n-->\n" * 20_000,
        "- a\n\n" + "  - FR-1: x\n\n" * 25_000,
    ]
    start = time.perf_counter()
    for text in texts:
        parse(text)
    assert time.perf_counter() - start < 2


HOSTILE = [
    "- " + "a" * 100_000,
    "- " + "a-" * 50_000,
    "- " + "a1" * 50_000,
    "- FR-" + "9" * 100_000,
    "- FR-1" + " " * 100_000,
    "- " + "*_" * 50_000,
    " " * 100_000 + "x",
    "- FR-1: x " + "{a:" * 33_000,
    "- FR-1: x " + "{a: b} " * 14_000,
    "- FR-1: x " + "<!-- a " * 14_000,
    "- FR-1: x " + "<!-- a --> " * 9_000,
    "<!--" * 25_000,
    "- FR-1: x " + "`` ` " * 20_000 + "<!--",
]


def test_hostile_lines_are_fast():
    start = time.perf_counter()
    for line in HOSTILE:
        parse(line + "\n")
    assert time.perf_counter() - start < 2


def _unreadable(tmp_path, case):
    if case == "missing":
        return tmp_path / "nope.md"
    if case == "directory":
        folder = tmp_path / "folder.md"
        folder.mkdir()
        return folder
    if case == "not-utf8":
        bad = tmp_path / "bad.md"
        bad.write_bytes(b"- FR-1: caf\xe9\n")
        return bad
    return tmp_path / "a\x00b.md"


@pytest.mark.parametrize("case", ["missing", "directory", "not-utf8", "nul"])
def test_read_unreadable(tmp_path, case):
    path = _unreadable(tmp_path, case)
    s = read_requirements(roots_around(path), path, "alpha", layers=PROJECT_LAYERS, br_namespace="life")
    assert s.requirements == {}
    (entry,) = s.report
    assert (entry.kind, entry.line, entry.source) == ("unreadable", None, str(path))
    if case == "not-utf8":
        assert entry.message == "the requirements file is not valid UTF-8 at byte 11"
    else:
        assert entry.message.startswith("cannot read the requirements file: ")


def test_read_bom(tmp_path):
    p = tmp_path / "bom.md"
    p.write_bytes(b"\xef\xbb\xbf- FR-1: x\n")
    s = read_requirements(roots_around(p), p, "alpha", layers=PROJECT_LAYERS, br_namespace="life")
    assert s.report == []
    assert s.requirements["alpha:FR-1"].line == 1


@pytest.mark.parametrize(
    ("namespace", "layers", "br"),
    [
        ("a b", PROJECT_LAYERS, "life"),
        ("", PROJECT_LAYERS, "life"),
        ("alpha", PROJECT_LAYERS, "a:b"),
        ("alpha", {"NFR"}, "life"),
    ],
)
def test_programmer_errors_raise(namespace, layers, br):
    with pytest.raises(ValueError):
        parse_requirements("- FR-1: x\n", namespace, source="t", layers=layers, br_namespace=br)
    with pytest.raises(ValueError):
        read_requirements(directory_roots([FIXTURES]), FIXTURES / "empty.md", namespace, layers=layers, br_namespace=br)


# --- links and the whole-set report ------------------------------------------------

BR_SECTION = {"namespace": "life", "layers": {"BR"}, "br": "life"}


def _fr1(s):
    return s.requirements["alpha:FR-1"]


REPORT_CASES = {
    # fixture: (kind, line, fragment, check of the set)
    "duplicate.md": ("duplicate_id", 2, "alpha:FR-1 is also defined on line 1; the first definition is kept",
                     lambda s: _fr1(s).text == "first"),
    "undefined.md": ("undefined_link", 1, "refines: alpha:SR-7 is not defined in this file",
                     lambda s: _fr1(s).refines == rid("SR-7")),
    "needs-non-uc.md": ("misplaced", 1, "needs: on alpha:FR-1, which is not a use case; it is not read",
                        lambda s: _fr1(s).needs == []),
    "unknown-key.md": ("malformed_meta", 1, "unknown key 'owner' in the meta block",
                       lambda s: _fr1(s).refines == rid("SR-1")),
    "refines-list.md": ("malformed_meta", 1, "refines: takes one target, not a list",
                        lambda s: _fr1(s).refines is None),
    "needs-empty.md": ("malformed_meta", 1, "needs: [] names no target",
                       lambda s: s.requirements["alpha:UC-1"].needs == []),
    "repeated-key.md": ("malformed_meta", 1, "key 'refines' is repeated in the meta block; the first is kept",
                        lambda s: _fr1(s).refines == rid("SR-1")),
    "second-block.md": ("malformed_meta", 1, "a second meta block on one item",
                        lambda s: _fr1(s).text == "x more {refines: SR-1}" and _fr1(s).refines == rid("SR-1")),
    "mid-text.md": ("malformed_meta", 1, "a meta block inside the text",
                    lambda s: _fr1(s).refines is None and _fr1(s).text == "a {refines: SR-2} b"),
    "self-refines.md": ("refines_cycle", 1, "alpha:FR-1 refines itself",
                        lambda s: _fr1(s).refines == rid("FR-1")),
    "cycle.md": ("refines_cycle", 1, "refines cycle: alpha:FR-1 -> alpha:FR-2 -> alpha:FR-1",
                 lambda s: _fr1(s).refines == rid("FR-2")),
    "br-in-project.md": ("misplaced", 1,
                         "alpha:BR-1 is a BR item, a layer this file does not hold (BRule, UC, SR, FR, QA, EI, CON)",
                         lambda s: "alpha:BR-1" not in s.requirements),
    "fr-in-br.md": ("misplaced", 1, "life:FR-1 is a FR item, a layer this file does not hold (BR)",
                    lambda s: "life:FR-1" not in s.requirements),
    "installation-link.md": ("misplaced", 1,
                             "refines: life:BR-1 is in the installation namespace 'life', "
                             "which only the portfolio file may link to; it is not read",
                             lambda s: _fr1(s).refines is None),
    "malformed-link.md": ("malformed_link", 1, "needs: 'fr-2' is not a requirement ID",
                          lambda s: s.requirements["alpha:UC-1"].needs == [rid("FR-1")]),
    "unreadable-block.md": ("malformed_meta", 1,
                            "the meta block '{refines: [SR-1}' cannot be read: an unclosed [ or quote",
                            lambda s: _fr1(s).refines is None),
}


@pytest.mark.parametrize("name", sorted(REPORT_CASES))
def test_report_case(name):
    kind, line, fragment, check = REPORT_CASES[name]
    if name == "fr-in-br.md":
        s, control = read("report/" + name, **BR_SECTION), "life:BR-9"
    else:
        s, control = read("report/" + name), "alpha:FR-9"
    (entry,) = s.report
    assert (entry.kind, entry.line) == (kind, line), entry
    assert fragment in entry.message, entry
    assert entry.source == str(FIXTURES / "report" / name)
    assert control in s.requirements  # the read did not stop
    assert check(s)


def test_other_site_link_unreported():
    s = read("report/other-site.md")
    assert s.report == []
    assert _fr1(s).refines == ReqId("ferry", "SR", 2)


def test_installation_link_legal_in_br_section():
    s = parse("- BR-1: a\n- BR-2: x {refines: BR-1}\n", "life", layers={"BR"}, br="life")
    assert s.report == []
    assert s.requirements["life:BR-2"].refines == ReqId("life", "BR", 1)


def test_section_line_numbers():
    s = parse_requirements("- BR-1: a\n- BR-1: b\n", "life", source="portfolio", first_line=40, layers={"BR"},
                           br_namespace="life")
    assert s.requirements["life:BR-1"].line == 40
    assert s.requirements["life:BR-1"].source == "portfolio"
    (entry,) = s.report
    assert (entry.kind, entry.line, entry.source) == ("duplicate_id", 41, "portfolio")
    assert "on line 40;" in entry.message


def test_report_order():
    s = parse("- FR-1: x {refines: SR-7, owner: y}\n- FR12: bad\n- FR-2: y {refines: FR-2}\n")
    got = [(e.line, e.kind) for e in s.report]
    assert got == [(1, "malformed_meta"), (1, "undefined_link"), (2, "malformed_item"), (3, "refines_cycle")]


def test_parse_requirements_skips_a_leading_bom():
    # A caller that passes a file's first section passes its byte-order mark too (#22).
    s = parse(chr(0xFEFF) + "- FR-1: x\n")
    assert s.report == []
    assert s.requirements["alpha:FR-1"].line == 1


@pytest.mark.parametrize(
    "text",
    ["- FR-1: a {refines: SR-1, note: {x}}\n", "- FR-1: a {refines: SR-1\n", "- FR-1: a {refines: SR-1 {b:c\n"],
)
def test_unclosed_or_nested_brace_block_is_reported(text):
    s = parse(text + "- SR-1: y\n")
    (entry,) = s.report
    assert (entry.line, entry.kind) == (1, "malformed_meta")
    assert entry.message == "the meta block starting '{refines:' cannot be read: an unclosed or nested brace"
    assert s.requirements["alpha:FR-1"].refines is None


def test_broken_opener_beside_a_block_leaves_the_block_read():
    s = parse("- FR-1: a {note: x {refines: SR-1}\n- SR-1: y\n")
    assert [e.kind for e in s.report] == ["malformed_meta"]
    assert s.requirements["alpha:FR-1"].refines == rid("SR-1")


def test_meta_entry_with_no_key():
    (entry,) = parse("- FR-1: x {refines: SR-1, SR-2}\n- SR-1: y\n").report
    assert entry.message == "the meta block '{refines: SR-1, SR-2}' cannot be read: an entry with no key"


def test_misplaced_item_is_dropped_before_its_block():
    # The wrong-layer item's own block is never read, so no further entry.
    s = parse("- BR-1: x {owner: y}\n- FR-1: z {refines: BR-1}\n")
    assert [(e.line, e.kind) for e in s.report] == [(1, "misplaced"), (2, "undefined_link")]


def test_cycle_reported_once_at_its_earliest_member():
    s = parse("- FR-3: c {refines: FR-1}\n- FR-1: a {refines: FR-2}\n- FR-2: b {refines: FR-3}\n"
              "- FR-4: d {refines: FR-1}\n")
    (entry,) = s.report
    assert entry.line == 1
    assert entry.message == "refines cycle: alpha:FR-3 -> alpha:FR-1 -> alpha:FR-2 -> alpha:FR-3"


# --- in the trace --------------------------------------------------------------


def _trace(tmp_path, names, tail):
    tree = write_site_tree(tmp_path, {name: b"# x\n" for name in names}, tail=tail)
    c = cfg.load_config(tree)
    return c, in_trace_sites(c, load_sites(c)[0])


def test_in_trace_sites_rules(tmp_path):
    c, (traced, warnings) = _trace(
        tmp_path / "a",
        ["alpha", "beta", "Life"],
        '[site.alpha]\nrequirements = "a.md"\n[site.Life]\nrequirements = "l.md"\n',
    )
    # beta has no key: out, with no warning. Life is not life: the case is exact.
    assert traced == {"alpha": c.sites["alpha"].requirements, "Life": c.sites["Life"].requirements}
    assert warnings == []
    c, (traced, warnings) = _trace(
        tmp_path / "b",
        ["alpha", "beta"],
        '[requirements]\nbr_namespace = "alpha"\n[site.alpha]\nrequirements = "a.md"\n'
        '[site.beta]\nrequirements = "b.md"\n',
    )
    assert traced == {"beta": c.sites["beta"].requirements}
    assert warnings == [
        SiteWarning(
            "site.alpha.requirements",
            f"{c.path}: key 'site.alpha.requirements' is not read: site alpha has the name of the "
            "installation namespace (br_namespace = 'alpha'), so it cannot be in the trace",
        )
    ]


def test_in_trace_sites_invalid_name(tmp_path):
    c, (traced, warnings) = _trace(tmp_path, ["bad name"], '[site."bad name"]\nrequirements = "b.md"\n')
    assert traced == {}
    assert warnings == [
        SiteWarning(
            "site.bad name.requirements",
            f"{c.path}: key 'site.bad name.requirements' is not read: site bad name's name does not "
            "match [A-Za-z0-9][A-Za-z0-9_.-]*, so it cannot be in the trace",
        )
    ]


# --- the samples ---------------------------------------------------------------


def _sample_trace():
    c = cfg.load_config(GANTRY_SAMPLE)
    sites = load_sites(c)[0]
    return c, sites, in_trace_sites(c, sites)


def _sample_sets():
    """{site name: its RequirementSet} for each site in the sample trace."""
    c, _, (traced, _) = _sample_trace()
    return {name: read_requirements(config_roots(c), path, name, layers=PROJECT_LAYERS, br_namespace=c.requirements.br_namespace)
            for name, path in traced.items()}


def test_sample_trace_in_and_out():
    _, sites, (traced, warnings) = _sample_trace()
    assert warnings == []
    assert len(traced) >= 1
    assert [site.name for site in sites if site.name not in traced] != []


def test_sample_requirements_read_clean():
    sets = _sample_sets()
    assert sets
    for s in sets.values():
        assert s.report == []
        reqs = s.requirements.values()
        assert {"UC", "SR", "FR", "QA", "CON"} <= {r.layer for r in reqs}
        assert any(r.layer == "UC" and r.needs for r in reqs)
        assert any(r.layer == "FR" and r.refines is not None and r.refines.code == "SR"
                   and str(r.refines) in s.requirements for r in reqs)


# How each fixture is read: (namespace, layers, br_namespace). Total: a fixture
# missing here fails the test below.
DEFAULT_READ = ("alpha", PROJECT_LAYERS, "life")
FIXTURE_READS = {
    "layers.md": ("alpha", ALL, "life"),
    "empty.md": DEFAULT_READ,
    "candidates.md": DEFAULT_READ,
    **{f"report/{name}": DEFAULT_READ for name in REPORT_CASES if name != "fr-in-br.md"},
    "report/fr-in-br.md": ("life", {"BR"}, "life"),
    "report/other-site.md": DEFAULT_READ,
}
# mtibbits/devagent#888's ID_TOKEN_RE, typed here rather than imported.
DEVAGENT_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def test_ids_fit_devagent_888_shape():
    found = sorted(p.relative_to(FIXTURES).as_posix() for p in FIXTURES.rglob("*.md"))
    assert [name for name in found if name not in FIXTURE_READS] == []
    sets = []
    for name in found:
        namespace, layers, br = FIXTURE_READS[name]
        sets.append(read(name, namespace, layers=layers, br=br))
    sets += _sample_sets().values()
    ids = []
    for s in sets:
        for r in s.requirements.values():
            ids += [r.id, *r.needs] + ([r.refines] if r.refines is not None else [])
    for one in ids:
        text = str(one)
        assert text.count(":") == 1, text
        namespace, _, rest = text.partition(":")
        assert DEVAGENT_TOKEN.fullmatch(namespace) and DEVAGENT_TOKEN.fullmatch(rest), text
    assert len(ids) > 30


# --- docs/requirements-format.md -------------------------------------------------

FORMAT_DOC = ROOT / "docs" / "requirements-format.md"
DOC_FENCE_RE = re.compile(r"[ \t]*(`{3,}|~{3,})(.*)")


def doc_blocks(text):
    """[(info, body)] for each top-level fenced block, paired by character and
    run length, so an outer four-backtick fence holds a three-backtick one."""
    lines = text.split("\n")
    blocks, i = [], 0
    while i < len(lines):
        m = DOC_FENCE_RE.fullmatch(lines[i])
        if m:
            run, info = m.groups()
            for j in range(i + 1, len(lines)):
                c = DOC_FENCE_RE.fullmatch(lines[j])
                if c and c[1][0] == run[0] and len(c[1]) >= len(run) and c[2].strip() == "":
                    blocks.append((info.strip(), "\n".join(lines[i + 1 : j]) + "\n"))
                    i = j
                    break
        i += 1
    return blocks


def test_format_doc_states_rules():
    text = FORMAT_DOC.read_bytes().decode("utf-8")
    doc = " ".join(text.split())
    firsts = {row[0] for table in md_tables(text) for row in table.rows}
    assert [code for code in LAYERS if f"`{code}`" not in firsts] == []
    wanted = list(REPORT_KINDS) + [
        cfg.ID_TOKEN_RE.pattern, "`life`", "UTF-8", "byte-order mark", "renumbered", "reused",
        "Step-1:", "Q3:", "v2:", "R1:", "refines:", "needs:", "in the trace", "br_namespace", "fenced",
        MIXED_NS, MIXED_NS.lower(), "9 digits", "requirements are not nested", "mtibbits/devagent#888",
        "withdrawn", "reword", "enclosing list item", "content column", "three spaces", "joins across it",
        "hides every line after it", "outside fences and comments", "on its own line",
    ]
    assert [w for w in wanted if w not in doc] == []


def test_format_doc_examples_read_clean():
    blocks = doc_blocks(FORMAT_DOC.read_bytes().decode("utf-8"))
    project = [body for info, body in blocks if info == "markdown"]
    portfolio = [body for info, body in blocks if info == "markdown portfolio"]
    assert len(project) >= 2 and len(portfolio) >= 1
    for body in project:
        s = parse(body, "gantry", layers=PROJECT_LAYERS, br="life")
        assert s.report == [], body
    for body in portfolio:
        s = parse(body, "life", layers={"BR"}, br="life")
        assert s.report == [], body
    assert parse(project[0], "gantry").requirements != {}
    assert parse(project[-1], "gantry").requirements == {}
    assert all(parse(body, "life", layers={"BR"}).requirements for body in portfolio)
    # The Comments example: its block comment hides FR-2, and FR-1 joins across its comment line.
    s = parse(project[0], "gantry")
    assert list(s.requirements) == ["gantry:FR-1", "gantry:FR-3"]
    assert s.requirements["gantry:FR-1"].text == "The log lists each check of the last thirty nights."
