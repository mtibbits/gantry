"""The requirements file format and ID scheme (#15).

docs/requirements-format.md states the rules these tests pin.
"""

import json
import re
import time
from pathlib import Path

import pytest

import gantry.config as cfg
from gantry import requirements as req
from gantry.portfolio_file import read_portfolio_file
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
        # Text five or more columns past the marker is code: the content column is one past the marker.
        ("-     x\n\n  - FR-2: b\n", [], "FR-2", 3),
        # An empty item followed by a line of its own text stays open.
        ("-\n  - FR-2: b\n", [], "FR-2", 2),
        ("-\n  text\n\n  - FR-2: b\n", [], "FR-2", 4),
        # A thematic break inside the item does not end it.
        ("- FR-1: a\n\n  * * *\n\n  - FR-2: b\n", ["alpha:FR-1"], "FR-2", 5),
        ("- FR-1: a\n  * * *\n  - FR-2: b\n", ["alpha:FR-1"], "FR-2", 3),
        # A list surely starts after a heading, after an HTML block's blank line, or after a list.
        ("# H\n- a\n\n  - FR-1: x\n", [], "FR-1", 4),
        ("<div>\n\n- a\n\n  - FR-1: x\n", [], "FR-1", 5),
        ("- a\n- b\n\n  - FR-1: x\n", [], "FR-1", 4),
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
        # An empty item followed by a blank line encloses nothing.
        ("-\n\n  - FR-2: b\n", ["alpha:FR-2"]),
        ("- \n\n  - FR-2: b\n", ["alpha:FR-2"]),
        ("- FR-1: a\n-\n\n  - FR-2: b\n", ["alpha:FR-1", "alpha:FR-2"]),
        # A thematic break is not a list item: it encloses nothing.
        ("* * *\n\n  - FR-1: a\n", ["alpha:FR-1"]),
        ("- - -\n\n  - FR-1: a\n", ["alpha:FR-1"]),
        ("* * *\n\n   - FR-1: a\n", ["alpha:FR-1"]),
        ("- FR-1: a\n\n* * *\n\n  - FR-2: b\n", ["alpha:FR-1", "alpha:FR-2"]),
        ("- Notes\n- - -\n\n  - FR-1: a\n", ["alpha:FR-1"]),
        # Nor does it start an item, so the lines indented after it are not its text (#112).
        ("* * *\n  - FR-1: a\n", ["alpha:FR-1"]),
        ("- - -\n  - FR-1: a\n", ["alpha:FR-1"]),
        ("- FR-1: a\n- - -\n  - FR-2: b\n", ["alpha:FR-1", "alpha:FR-2"]),
        # Only a line that surely starts a list encloses: not one that continues a paragraph, and
        # not one inside an HTML block. A page nests the last leg; the reader reads it as written.
        ("Para\n2. x\n\n   - FR-1: a\n", ["alpha:FR-1"]),
        ("Text with a hard-wrapped\n3. third point\n\n   - FR-1: a\n", ["alpha:FR-1"]),
        ("Para\n3) x\n\n   - FR-1: a\n", ["alpha:FR-1"]),
        ("Para\n*\n  more\n\n  - FR-1: a\n", ["alpha:FR-1"]),
        ("Para\n-\n  more\n\n  - FR-1: a\n", ["alpha:FR-1"]),
        ("<details>\n- Notes\n\n  - FR-1: a\n", ["alpha:FR-1"]),
        ("- FR-1: a\nlazy\n2. x\n\n   - FR-2: b\n", ["alpha:FR-1", "alpha:FR-2"]),
        ("Para\n- x\n\n  - FR-1: a\n", ["alpha:FR-1"]),
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


NB = chr(0xA0)
QUOTE = "'FR-1:' is not a requirement: inside a block quote"
NBSP = "indented with U+00A0 (only spaces and tabs indent a list item)"


@pytest.mark.parametrize("text, report", [
    ("> - FR-1: a\n", [(1, "malformed_item", QUOTE)]),
    (">- FR-1: a\n", [(1, "malformed_item", QUOTE)]),
    ("> > - FR-1: a\n", [(1, "malformed_item", QUOTE)]),
    ("  > - FR-1: a\n", [(1, "malformed_item", QUOTE)]),
    (NB + "- FR-1: a\n", [(1, "malformed_item", "'FR-1:' is not a requirement: " + NBSP)]),
    ("  " + NB + "- FR-1: a\n", [(1, "malformed_item", "'FR-1:' is not a requirement: " + NBSP)]),
    ("> " + NB + "- FR-1: a\n", [(1, "malformed_item", QUOTE + "; " + NBSP)]),
    # An ASCII text with no '>' still gets the check (a form feed is whitespace).
    ("\f- FR-1: a\n", [(1, "malformed_item", "'FR-1:' is not a requirement: " + NBSP.replace("U+00A0", "U+000C"))]),
    ("> - FR12: a\n",
     [(1, "malformed_item", "'FR12:' is not a requirement: inside a block quote; no '-' between the code and the number")]),
    ("# H\n\n> - FR-1: a\n", [(3, "malformed_item", QUOTE)]),
    # A quoted comment is not a block comment, so the item inside it is reported.
    ("> <!--\n> - FR-1: a\n> -->\n", [(2, "malformed_item", QUOTE)]),
])
def test_quoted_or_unusually_indented_candidate(text, report):
    # An ID item in a block quote, or indented with a no-break space, is never silently lost (#112).
    s = parse(text)
    assert rows(s) == report
    assert s.requirements == {}


@pytest.mark.parametrize("text, line, why", [
    ("- FR-1: a\n  > - FR-2: b\n", 2, "inside a block quote"),
    ("- FR-1: a\n\n  > - FR-2: b\n", 3, "inside a block quote"),
    ("- FR-1: a\n  " + NB + "- FR-2: b\n", 2, NBSP),
])
def test_quoted_candidate_under_an_item(text, line, why):
    # The quote or the odd indent is the cause, so no "not nested" reason, whether or not a blank line comes first.
    s = parse(text)
    assert rows(s) == [(line, "malformed_item", "'FR-2:' is not a requirement: " + why)]
    assert list(s.requirements) == ["alpha:FR-1"]


FENCE = "`" * 3


@pytest.mark.parametrize("text, report", [
    # A quoted fence closes where its quote ends, so an unclosed one hides nothing after the quote.
    ("> " + FENCE + "\n> code\n\n- FR-1: a\n> - FR-2: b\n", [(5, "malformed_item", QUOTE.replace("FR-1", "FR-2"))]),
    ("> " + FENCE + "\n> code\n\n- FR-1: a\n" + NB + "- FR-2: b\n",
     [(5, "malformed_item", "'FR-2:' is not a requirement: " + NBSP)]),
    ("> " + FENCE + "\n> code\n\n- FR-1: a\n  > - FR-2: b\n", [(5, "malformed_item", QUOTE.replace("FR-1", "FR-2"))]),
    # A quoted fence closes only on a run of its own character, at least as long, with nothing after it.
    ("> ````\n> " + FENCE + "\n> - FR-1: x\n> ````\n> - FR-2: y\n", [(5, "malformed_item", QUOTE.replace("FR-1", "FR-2"))]),
    # Not a fence on a page: a backtick info string holding a backtick, or a run 4+ columns past the marker.
    ("> " + FENCE + "a`b\n> - FR-1: a\n", [(2, "malformed_item", QUOTE)]),
    (">     " + FENCE + "\n> - FR-1: a\n", [(2, "malformed_item", QUOTE)]),
])
def test_quoted_fence_hides_only_its_own_lines(text, report):
    s = parse(text)
    assert rows(s) == report


@pytest.mark.parametrize("text", [
    "> - note: x\n",
    NB + "- note: x\n",
    "> FR-1: a\n",
    "```text\n> - FR-1: a\n```\n",
    "<!--\n> - FR-1: a\n-->\n",
    NB + "\n",
    "  " + NB + "\n",
    "> ```text\n> - FR-1: a\n> ```\n",
    # Just outside the grammar: U+200B is not whitespace.
    chr(0x200B) + "- FR-1: a\n",
])
def test_quoted_or_unusually_indented_prose_is_silent(text):
    s = parse(text)
    assert rows(s) == []
    assert s.requirements == {}


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
START = ("an HTML comment that starts a list item and does not close on its line; "
         "it hides nothing: put the comment on its own line")


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


@pytest.mark.parametrize(
    ("text", "keys", "report"),
    [
        # An item after paragraph text encloses nothing, but its comment lines still join its text.
        ("Para\n- FR-1: a\n  <!-- c -->\n  - FR-2: a\n", ["alpha:FR-1"], [NN("FR-2", 4)]),
        # Inside an HTML block a `<!--` line is raw content, not a block comment.
        ("<details>\n<!--\n- x\n  <!-- c -->\n  - FR-1: a\n", [], [NN("FR-1", 5)]),
    ],
)
def test_items_that_do_not_enclose_still_hold_their_text(text, keys, report):
    s = parse(text)
    assert list(s.requirements) == keys
    assert rows(s) == report


@pytest.mark.parametrize(
    "text",
    [
        # A line that encloses nothing bounds no comment: the page closes the comment at its -->.
        "Para\n2. x\n   <!-- c\n-->\n- FR-1: a\n",
        "Para\n-\n  <!--\n-->\n- FR-1: a\n",
        "<details>\n- x\n\n  <!-- c\n-->\n- FR-1: a\n",
        "-->\n-\n  <!--\n-->\n- FR-1: a\n",
        "Para\n*\n  <!--\n-->\n- FR-1: a\n",
        "-->\n-\n  <!--\n-->\n  - FR-1: a\n",
    ],
)
def test_comment_under_a_line_that_encloses_nothing_does_not_leak(text):
    s = parse(text)
    assert list(s.requirements) == ["alpha:FR-1"]
    assert rows(s) == []


@pytest.mark.parametrize(
    ("text", "keys", "fr1"),
    [
        # After an unclosed fence opener inside an item, a <!-- line is text (a page shows code).
        ("- FR-1: a\n  ```\n  <!--\n- FR-2: a\n", ["alpha:FR-1", "alpha:FR-2"], "a ``` <!--"),
        ("- x\n  ```\n  <!--\n-->\n- FR-1: a\n", ["alpha:FR-1"], "a"),
    ],
)
def test_comment_after_an_unclosed_fence_in_an_item_is_text(text, keys, fr1):
    s = parse(text)
    assert list(s.requirements) == keys
    assert s.requirements["alpha:FR-1"].text == fr1
    assert rows(s) == []


@pytest.mark.parametrize(
    ("text", "keys"),
    [
        # A fence a page may pair differently (indented 4+ past its container, or a closer
        # outdented below its item): a <!-- after it is text, as HEAD read it.
        ("```\n    ```\n<!--\n```\n- FR-1: a\n- FR-2: a\n<!-- note -->\n- FR-3: a\n",
         ["alpha:FR-1", "alpha:FR-2", "alpha:FR-3"]),
        ("- x\n  ```\n```\n<!--\n```\n- FR-1: a\n<!-- note -->\n- FR-2: a\n", ["alpha:FR-1", "alpha:FR-2"]),
        ("```\n    ```\n<!--\n```\n- FR-1: a\n", ["alpha:FR-1"]),
        # After such a fence no item encloses either; an opener 4+ columns in is not a fence at all.
        ("```\n    ```\n- x\n  ```\n\n  - FR-1: a\n", ["alpha:FR-1"]),
        ("<!--\n```\n-->\n    ```\n- FR-1: a\n```\n", ["alpha:FR-1"]),
        # A fence whose list item ends inside it, or one opened right after a line that may lazily
        # continue an item, is doubtful too; a doubtful fence hides only what HEAD's pairing hid.
        ("- FR-1: The installer runs:\n  ```sh\nmake install\n  ```\n<!-- old steps\n```\n"
         "- FR-2: The log lists each check.\n-->\n- FR-3: The log is plain text.\n",
         ["alpha:FR-1", "alpha:FR-2", "alpha:FR-3"]),
        ("<!--\n```\n-->\n- x\n  ```\n- FR-1: a\n  ```\n", ["alpha:FR-1"]),
        ("- x\n  ```\n- FR-1: a\n  ```\n  <!--\n- FR-2: a\n", ["alpha:FR-2"]),
        ("- FR-1: a\nPara\n  ```\n```\n<!--\n```\n- FR-2: a\n", ["alpha:FR-1", "alpha:FR-2"]),
        # After a blank line inside a comment, only a line at the content column joins the text.
        ("1. Step one\n   <!--\n\n   -->\n  - FR-1: a\n", ["alpha:FR-1"]),
        ("- FR-1: a\n  <!--\n\n  -->\n - FR-2: b\n", ["alpha:FR-1", "alpha:FR-2"]),
    ],
)
def test_reader_falls_back_to_head_where_the_page_is_in_doubt(text, keys):
    s = parse(text)
    assert list(s.requirements) == keys
    assert rows(s) == []


def test_comment_after_a_lazy_line_is_not_skipped():
    # A stated simplification (no lazy continuation): the page nests FR-2 under FR-1; the reader
    # ends FR-1 at the lazy line, so the comment after it is not skipped and FR-2 is read.
    s = parse("- FR-1: a\n x\n  <!--\n  -->\n  - FR-2: a\n")
    assert list(s.requirements) == ["alpha:FR-1", "alpha:FR-2"]
    assert rows(s) == []


def test_comment_under_an_item_that_encloses_nothing_runs_on():
    # A stated simplification: the page nests FR-1 under x; the reader reads it as written.
    s = parse("Para\n- x\n  <!-- c\n- <!-- c -->\n  - FR-1: a\n")
    assert list(s.requirements) == ["alpha:FR-1"]
    assert rows(s) == []


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


@pytest.mark.parametrize(
    ("text", "fr1", "report"),
    [
        ("- FR-1: a\n  - <!-- x\n    y -->\n  more\n", "a - <!-- x y --> more", [MC(2, START)]),
        ("- FR-1: a\n  - <!-- x\n- FR-2: b\n", "a - <!-- x", [MC(2, START)]),
    ],
)
def test_nested_item_start_comment_is_reported_once(text, fr1, report):
    # One report per line: START, not START and INLINE.
    s = parse(text)
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
        ("- <!-- x --> prose\n", [], []),
        # An item whose text opens a comment that does not close on its line hides nothing (OQ-F1, report-only).
        ("- <!-- x\n- FR-2: y\n", ["alpha:FR-2"], [MC(1, START)]),
        ("- <!-- x\n  y -->\n- FR-2: y\n", ["alpha:FR-2"], [MC(1, START)]),
        ("- Notes\n  - <!-- x\n  - more\n    -->\n- FR-3: c\n", ["alpha:FR-3"], [MC(2, START)]),
        # A line a page shows as code is not reported: indented 4 with no enclosing item, or text
        # five or more columns past the marker. Deep nesting inside an item is still reported.
        ("Para\n\n    - <!-- x\n- FR-1: a\n", ["alpha:FR-1"], []),
        ("-     <!-- x\n- FR-1: a\n", ["alpha:FR-1"], []),
        ("1.     <!-- x\n- FR-1: a\n", ["alpha:FR-1"], []),
        ("- a\n\n      - <!-- x\n- FR-1: b\n", ["alpha:FR-1"], [MC(3, START)]),
        ("- FR-1: a\n\n      - <!-- x\n\n- FR-2: b\n", ["alpha:FR-1", "alpha:FR-2"], [MC(3, START)]),
        ("- FR-1: a\n      - <!-- x\n- FR-2: b\n", ["alpha:FR-1", "alpha:FR-2"], [MC(2, START)]),
        ("- FR-1: a\n  -     <!-- x\n- FR-2: b\n", ["alpha:FR-1", "alpha:FR-2"], []),
        # Paragraph text on a page, not a list item: not reported.
        ("Para\n2. <!-- x\n\n- FR-1: a\n", ["alpha:FR-1"], []),
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


def test_many_blocks_on_one_item_are_fast():
    # Each block is checked against the item's line ends once (#112): never per block, per line.
    start = time.perf_counter()
    parse("- FR-1: x {refines: SR-1}\n" + "  {a: b}\n" * 20_000)
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
    ">" * 100_000 + " - FR-1: x",
    chr(0xA0) * 100_000 + "- FR-1: x",
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
    if case == "under-file":
        file = tmp_path / "file.md"
        file.write_bytes(b"- FR-1: a\n")
        return file / "x.md"
    return tmp_path / "a\x00b.md"


# Each cause reads alike on every operating system (#112): the OS's own text
# differs (a directory is "Permission denied" on Windows, "Is a directory" on
# Linux), so only a NUL keeps the OS's reason.
UNREADABLE = {
    "missing": "the requirements file does not exist",
    "under-file": "the requirements file does not exist",
    "directory": "the requirements file is not a file",
    "not-utf8": "the requirements file is not valid UTF-8 at byte 11",
}


@pytest.mark.parametrize("case", ["missing", "under-file", "directory", "not-utf8", "nul"])
def test_read_unreadable(tmp_path, case):
    path = _unreadable(tmp_path, case)
    s = read_requirements(roots_around(path), path, "alpha", layers=PROJECT_LAYERS, br_namespace="life")
    assert s.requirements == {}
    (entry,) = s.report
    assert (entry.kind, entry.line, entry.source) == ("unreadable", None, str(path))
    if case in UNREADABLE:
        assert entry.message == UNREADABLE[case]
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


@pytest.mark.parametrize("ws", [chr(0xA0), chr(0x2028), chr(0x1C)])
def test_unicode_whitespace_line_ends_the_text(ws):
    # A line of only whitespace, Unicode whitespace included, is blank (#112): it ends the text.
    s = parse("- FR-1: a\n  b\n  " + ws + "\n  c\n")
    assert _fr1(s).text == "a b"
    assert rows(s) == []


@pytest.mark.parametrize("text, texts, report_lines", [
    # A CR-only file is one line: FR-1 keeps no CR and no text of FR-2.
    ("- FR-1: a\r- FR-2: b\r", {"alpha:FR-1": "a"}, [1]),
    ("- FR-1: a\r\n- FR-2: b\r- FR-3: c\n", {"alpha:FR-1": "a", "alpha:FR-2": "b"}, [2]),
    # CRLF, and a bare CR with only whitespace after it, lose nothing.
    ("- FR-1: a\r\n- FR-2: b\r\n", {"alpha:FR-1": "a", "alpha:FR-2": "b"}, []),
    ("- FR-1: a\r\r\n", {"alpha:FR-1": "a"}, []),
])
def test_bare_carriage_return(text, texts, report_lines):
    # A CR with no LF after it cuts its line and is reported, never silently joined (#112).
    s = parse(text)
    assert {k: r.text for k, r in s.requirements.items()} == texts
    assert not any("\r" in r.text for r in s.requirements.values())
    assert rows(s) == [(n, "malformed_item", req.BARE_CR) for n in report_lines]


def test_bare_carriage_return_counts_from_first_line():
    s = parse_requirements("- BR-1: a\r- BR-2: b", "life", source="portfolio", first_line=40, layers={"BR"},
                           br_namespace="life")
    assert list(s.requirements) == ["life:BR-1"]
    assert rows(s) == [(40, "malformed_item", req.BARE_CR)]


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


@pytest.mark.parametrize("text, key, field, value", [
    ("- FR-1: a {refines: SR-1, }\n- SR-1: s\n", "alpha:FR-1", "refines", rid("SR-1")),
    ("- FR-1: a {refines: SR-1,}\n- SR-1: s\n", "alpha:FR-1", "refines", rid("SR-1")),
    ("- UC-1: a {needs: [FR-1], , }\n- FR-1: f\n", "alpha:UC-1", "needs", [rid("FR-1")]),
])
def test_empty_meta_entry_is_skipped(text, key, field, value):
    # A trailing comma's empty entry is skipped, as devagent skips it (#112).
    s = parse(text)
    assert rows(s) == []
    assert getattr(s.requirements[key], field) == value


MALFORMED_LIST = req.MALFORMED_LIST


@pytest.mark.parametrize("text, report, field, value", [
    ("- UC-1: c {needs: [FR-1] [FR-2]}\n- FR-1: a\n- FR-2: b\n",
     [(1, "malformed_meta", "needs: " + MALFORMED_LIST)], ("alpha:UC-1", "needs"), []),
    ("- UC-1: c {needs: [FR-1] x}\n- FR-1: a\n",
     [(1, "malformed_meta", "needs: " + MALFORMED_LIST)], ("alpha:UC-1", "needs"), []),
    ("- FR-1: a {refines: [SR-1] [SR-2]}\n- SR-1: s\n",
     [(1, "malformed_meta", "refines: " + MALFORMED_LIST)], ("alpha:FR-1", "refines"), None),
    # Per key: the block's other keys are still read.
    ("- FR-1: a {refines: SR-1, note: [x] [y]}\n- SR-1: s\n",
     [(1, "malformed_meta", "unknown key 'note' in the meta block (a requirement's keys are refines and needs)")],
     ("alpha:FR-1", "refines"), "SR-1"),
    # The misplaced check stays first.
    ("- FR-1: a {needs: [x] [y]}\n",
     [(1, "misplaced", "needs: on alpha:FR-1, which is not a use case; it is not read")],
     ("alpha:FR-1", "needs"), []),
])
def test_list_with_text_after_its_bracket(text, report, field, value):
    # A list whose ']' is not its value's end is malformed, not empty (#112).
    s = parse(text)
    assert rows(s) == report
    got = getattr(s.requirements[field[0]], field[1])
    assert got == (rid(value) if isinstance(value, str) else value)


def test_malformed_list_then_its_key_repeated():
    # The first is not kept when it cannot be read: the report says neither is read.
    s = parse("- UC-1: c {needs: [x] [y], needs: [FR-1]}\n- FR-1: a\n")
    assert rows(s) == [(1, "malformed_meta", "needs: " + MALFORMED_LIST),
                       (1, "malformed_meta", "key 'needs' is repeated in the meta block, and the first is "
                                             "malformed; neither is read")]
    assert s.requirements["alpha:UC-1"].needs == []


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
    "block-before-nested.md": DEFAULT_READ,
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
        "a comment line indented less ends the text", "one comment closed on its line",
        "only the first `-->` on a line counts", "not read as an escape",
        "five or more columns past the marker", "An empty item followed by a blank line encloses nothing",
        "though a page hides a closed one", "starts with a `<!--` that does not close on its line",
        "inside a block quote", "is still read as an item", "A thematic break",
        "unless a page shows the line as code", "surely starts a list", "is that block's content",
        "after an unclosed fence", "four or more spaces outside every item",
        "a page may pair it differently", "runs on past a later item line",
        "is not a fence", "and no item encloses", "whose list item ends inside it",
        "may lazily continue an item", "a comment after such a line is not skipped",
        "is used once it is on the file's default branch",
    ]
    assert [w for w in wanted if w not in doc] == []


def test_format_doc_states_report_causes():
    # Each cause the reader names (#112) is stated in the format doc.
    text = FORMAT_DOC.read_bytes().decode("utf-8")
    doc = " ".join(text.split())
    wanted = [
        "the requirements file is not a file", "the requirements file does not exist",
        "a path under a file",
        MALFORMED_LIST, "after a trailing comma", "any other block that ends a line of the text",
        "the lines indented after it are not its text",
        "> - FR-3: inside a block quote", NBSP, "zero-width space", "> - Q3: revenue",
        "a quoted ID item inside it is still reported",
        req.BARE_CR, "a bare carriage return is still reported there",
        "any character Python's `str.isspace()` accepts",
        "with no line feed after it and text after it", "and the first is malformed; neither is read",
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
    (comments,) = [body for body in project if "<!-- was:" in body]
    main = next(body for body in project if body is not comments)
    assert parse(main, "gantry").requirements != {}
    assert parse(project[-1], "gantry").requirements == {}
    assert all(parse(body, "life", layers={"BR"}).requirements for body in portfolio)
    # The Comments example: its block comment hides FR-2, and FR-1 joins across its comment line.
    s = parse(comments, "gantry")
    assert list(s.requirements) == ["gantry:FR-1", "gantry:FR-3"]
    assert s.requirements["gantry:FR-1"].text == "The log lists each check of the last thirty nights."


# --- threshold and objective statements (#79) ------------------------------------------

STRAY = "a statement not directly under a requirement"


def ST(n, word, reason=STRAY):
    """The report row of a Threshold: or Objective: item on line n that is not read."""
    return (n, "malformed_item", f"'{word}:' is not read: {reason}")


def SECOND(n, word):
    return ST(n, word, f"a second {word}: statement; it stays in the text")


def INSIDE(n):
    return (n, "malformed_meta", "a meta block inside the text; it stays in the text and is not read")


SECOND_BLOCK = (1, "malformed_meta", "a second meta block on one item; it stays in the text and is not read")
TWO = "- SR-1: s\n- SR-2: t\n"


@pytest.mark.parametrize("text, report, fr_text, refines", [
    # Both on continuation lines: the own text's LAST block is read (3a, 3b).
    ("- FR-2: b\n  {refines: SR-1}\n  {refines: SR-2}\n", [SECOND_BLOCK], "b {refines: SR-1}", "SR-2"),
    ("- FR-2: b\n  c {refines: SR-1}\n  d {refines: SR-2}\n", [SECOND_BLOCK], "b c {refines: SR-1} d", "SR-2"),
    # The first line's block is read.
    ("- FR-2: b {refines: SR-1}\n  {refines: SR-2}\n", [SECOND_BLOCK], "b {refines: SR-2}", "SR-1"),
    # A mid-line block is still inside the text.
    ("- FR-2: b\n  x {refines: SR-1} y\n  {refines: SR-2}\n", [INSIDE(1)], "b x {refines: SR-1} y", "SR-2"),
    # A block ending a nested item's line is a second block too.
    ("- FR-2: a {refines: SR-1}\n  - sub {refines: SR-2}\n  - more\n", [SECOND_BLOCK],
     "a - sub {refines: SR-2} - more", "SR-1"),
])
def test_second_block_wherever_the_first_sits(text, report, fr_text, refines):
    # When a block is read, another block that ends a line is a second block, wherever the read one sits (#112).
    s = parse(text + TWO)
    assert rows(s) == report
    r = s.requirements["alpha:FR-2"]
    assert (r.text, r.refines) == (fr_text, rid(refines))


def fields(r):
    return (r.threshold, r.objective, r.text)


def test_statements_read_and_removed():
    s = parse("- SR-1: The tool reads each site's work breakdown.\n"
              "- FR-21: A dependency on another site's leaf is shown and\n"
              "  checked. {refines: SR-1}\n"
              "  - Threshold: dependencies within one installation.\n"
              "  - Objective: also dependencies on sites that are not read, named.\n", "gantry")
    assert rows(s) == []
    fr = s.requirements["gantry:FR-21"]
    assert fr.threshold == "dependencies within one installation."
    assert fr.objective == "also dependencies on sites that are not read, named."
    assert fr.text == "A dependency on another site's leaf is shown and checked."
    assert fr.refines == parse_id("gantry:SR-1")


def test_statement_continuation_lines_join():
    s = parse("- FR-1: a\n  - Threshold: x\n    y\n      z\n  - Objective: o\n    p\n")
    assert rows(s) == []
    assert fields(_fr1(s)) == ("x y z", "o p", "a")


def test_statements_absent_and_empty():
    assert fields(_fr1(parse("- FR-1: a\n  b\n"))) == (None, None, "a b")
    s = parse("- FR-1: a\n  - Threshold:\n  - Objective:   \n")
    assert rows(s) == []
    assert fields(_fr1(s)) == ("", "", "a")


def test_block_before_first_nested_item_is_read():
    s = parse("- FR-1: a\n  b {refines: SR-1}\n  - note\n- SR-1: s\n")
    assert rows(s) == []
    assert _fr1(s).refines == rid("SR-1")
    assert _fr1(s).text == "a b - note"


def test_block_before_nested_item_in_a_corpus_shaped_file():
    s = read("block-before-nested.md")
    assert s.report == []
    assert _fr1(s).refines == rid("SR-1")
    assert _fr1(s).text == "The page lists each leaf's checks and their results. - The list is sorted by date."


def test_first_line_block_still_read_with_statements():
    s = parse("- FR-1: a {refines: SR-1}\n  - Threshold: x\n- SR-1: s\n")
    assert rows(s) == []
    assert _fr1(s).refines == rid("SR-1")
    assert fields(_fr1(s)) == ("x", None, "a")


@pytest.mark.parametrize(
    ("text", "fr1"),
    [
        ("- FR-1: a\n  - note {refines: SR-1}\n- SR-1: s\n", "a - note {refines: SR-1}"),
        ("- FR-1: a\n  - note\n  more {refines: SR-1}\n- SR-1: s\n", "a - note more {refines: SR-1}"),
        ("- FR-1: a\n  - Threshold: x\n  - note {refines: SR-1}\n- SR-1: s\n", "a - note {refines: SR-1}"),
    ],
)
def test_block_after_nested_item_reported(text, fr1):
    s = parse(text)
    assert rows(s) == [INSIDE(1)]
    assert _fr1(s).refines is None
    assert _fr1(s).text == fr1


def test_block_in_statement_reported_and_kept():
    s = parse("- FR-1: a\n  - Threshold: x {refines: SR-1}\n- SR-1: s\n")
    assert rows(s) == [INSIDE(1)]
    assert _fr1(s).threshold == "x {refines: SR-1}"
    assert _fr1(s).refines is None
    assert _fr1(s).text == "a"


@pytest.mark.parametrize("word", ["Threshold", "Objective"])
def test_second_threshold_reported_and_kept(word):
    s = parse(f"- FR-1: a\n  - {word}: x\n  - {word}: y\n")
    assert rows(s) == [SECOND(3, word)]
    assert getattr(_fr1(s), word.lower()) == "x"
    assert _fr1(s).text == f"a - {word}: y"


@pytest.mark.parametrize(
    ("text", "line", "fr1"),
    [
        # Two levels down: inside a first-level item.
        ("- FR-1: a\n  - note\n    - Threshold: x\n", 3, "a - note - Threshold: x"),
        ("- FR-1: a\n  - Threshold: t\n    - Objective: x\n", 3, "a"),
        # An ordered marker.
        ("- FR-1: a\n  1. Threshold: x\n", 2, "a 1. Threshold: x"),
        # At column 0 after a requirement: the main loop reads it as its own item.
        ("- FR-1: a\n- Threshold: x\n", 2, "a"),
        ("- FR-1: a\n1. Objective: x\n", 2, "a"),
        # Under a prose bullet, which is not stored.
        ("- Notes\n  - Threshold: x\n- FR-1: a\n", 2, "a"),
    ],
)
def test_stray_statements_reported(text, line, fr1):
    s = parse(text)
    word = "Objective" if "- Objective" in text or ". Objective" in text else "Threshold"
    assert rows(s) == [ST(line, word)]
    assert _fr1(s).text == fr1
    if fr1 == "a" and "Threshold: t" not in text:
        assert fields(_fr1(s)) == (None, None, "a")


@pytest.mark.parametrize("word", ["Threshold", "Objective"])
def test_statement_after_blank_line(word):
    text = f"- FR-1: a\n\n  - {word}: x\n"
    s = parse(text)
    assert rows(s) == [ST(3, word)]
    assert fields(_fr1(s)) == (None, None, "a")
    assert req._scan(req.split_lines(text)).nested[2] is True  # no layout change


def test_lowercase_and_plural_stay_text():
    s = parse("- FR-1: a\n  - threshold: x\n  - Thresholds: y\n  - **Threshold:** z\n  - Objective : w\n")
    assert rows(s) == []
    assert fields(_fr1(s)) == (None, None, "a - threshold: x - Thresholds: y - **Threshold:** z - Objective : w")


def test_statement_after_unclosed_fence_is_text():
    # After an unclosed fence opener in the item, a page shows code: never a statement, never reported.
    s = parse("- FR-1: a\n  ```\n  - Threshold: x\n")
    assert rows(s) == []
    assert fields(_fr1(s)) == (None, None, "a ``` - Threshold: x")


def test_statement_under_dropped_requirement_not_reported():
    s = parse("- FR-1: a\n- FR-1: b\n  - Threshold: x\n")
    assert [(e.line, e.kind) for e in s.report] == [(2, "duplicate_id")]
    assert fields(_fr1(s)) == (None, None, "a")


def test_statement_one_column_in():
    s = parse("- FR-1: a\n - Threshold: x\n")
    assert rows(s) == []
    assert fields(_fr1(s)) == ("x", None, "a")


def test_statement_lazy_continuation():
    s = parse("- FR-1: a\n  - Threshold: x\n  more\n")
    assert rows(s) == []
    assert fields(_fr1(s)) == ("x more", None, "a")


@pytest.mark.parametrize(
    ("text", "want", "report"),
    [
        # An own-text-looking line right after a statement continues it, as a page shows it.
        ("- FR-1: a\n  - Threshold: x\n b\n", {"alpha:FR-1": ("x b", None, "a")}, []),
        # Rule 7: the text keeps file order with a non-statement item between two statements.
        ("- FR-1: a\n  - Threshold: x\n  - note\n  - Objective: y\n  - more\n",
         {"alpha:FR-1": ("x", "y", "a - note - more")}, []),
        # A statement after a first-level item with deeper lines.
        ("- FR-1: a\n  - note\n    deeper\n  - Threshold: x\n", {"alpha:FR-1": ("x", None, "a - note deeper")}, []),
        # The * and + markers.
        ("- FR-1: a\n  * Threshold: x\n  + Objective: y\n", {"alpha:FR-1": ("x", "y", "a")}, []),
        # Under a malformed candidate (not stored): reported.
        ("- FR12: a\n  - Threshold: x\n- FR-1: b\n", {"alpha:FR-1": (None, None, "b")},
         [(1, "malformed_item", "'FR12:' is not a requirement: no '-' between the code and the number"),
          ST(2, "Threshold")]),
        # Under a misplaced or a BEFORE_ID parent: dropped with it, covered by its entry.
        ("- BR-1: a\n  - Threshold: x\n- FR-1: b\n", {"alpha:FR-1": (None, None, "b")},
         [(1, "misplaced", "alpha:BR-1 is a BR item, a layer this file does not hold "
                           "(BRule, UC, SR, FR, QA, EI, CON)")]),
        ("- <!-- c --> FR-2: a\n  - Threshold: x\n- FR-1: b\n", {"alpha:FR-1": (None, None, "b")},
         [MC(1, BEFORE_ID)]),
    ],
)
def test_statement_shape_legs(text, want, report):
    s = parse(text)
    assert rows(s) == report
    assert {key: fields(s.requirements[key]) for key in want} == want


@pytest.mark.parametrize(
    ("text", "want"),
    [
        # A new first-level item closes the one before: its statement is first-level.
        ("- FR-1: a\n  - note\n    more\n  - Threshold: x\n", {"alpha:FR-1": ("x", None, "a - note more")}),
        # The next requirement closes it.
        ("- FR-1: a\n  - note\n- FR-2: b\n  - Threshold: x\n",
         {"alpha:FR-1": (None, None, "a - note"), "alpha:FR-2": ("x", None, "b")}),
        # A blank line closes it.
        ("- FR-1: a\n  - note\n\n- FR-2: b\n  - Threshold: x\n",
         {"alpha:FR-1": (None, None, "a - note"), "alpha:FR-2": ("x", None, "b")}),
    ],
)
def test_first_level_state_closes(text, want):
    s = parse(text)
    assert rows(s) == []
    assert {key: fields(s.requirements[key]) for key in want} == want


def test_candidate_shaped_statement_is_candidate():
    s = parse("- FR-1: a\n  - Threshold:FR-2: x\n")
    (entry,) = s.report
    assert (entry.line, entry.kind) == (2, "malformed_item")
    assert entry.message.startswith("'Threshold:FR-2:' is not a requirement: a namespace"), entry
    assert fields(_fr1(s)) == (None, None, "a - Threshold:FR-2: x")


@pytest.mark.parametrize(
    ("text", "threshold", "report"),
    [
        ("- FR-1: a\n  - Threshold: x {refines: SR-1\n", "x {refines: SR-1",
         [(1, "malformed_meta", "the meta block starting '{refines:' cannot be read: an unclosed or nested brace")]),
        ("- FR-1: a {refines: SR-1\n  - Threshold: x {needs: UC-1\n", "x {needs: UC-1",
         [(1, "malformed_meta", "the meta block starting '{refines:' cannot be read: an unclosed or nested brace")]),
        ("- FR-1: a\n  - Threshold: x <!-- c -->\n", "x <!-- c -->", [MC(2, INLINE)]),
    ],
)
def test_unclosed_opener_and_comment_in_statement_reported(text, threshold, report):
    s = parse(text)
    assert rows(s) == report
    assert _fr1(s).threshold == threshold


# The reader corpus (U1): the parse of every repo requirements text, captured on the tree before
# #79 as a literal golden. The files #79 itself changes are excluded by name. A later intended
# change to these parses rewrites the golden from the reader:
#   json.dumps({p: _corpus_entry(s) for p, s in _corpus().items() if p not in CORPUS_NEW}, indent=1) + "\n"
CORPUS_GOLDEN = FIXTURES / "corpus_golden.json"
CORPUS_CHANGED = ("docs/requirements.md", "samples/lighthouse/requirements.md")
CORPUS_NEW = ("tests/fixtures/requirements/block-before-nested.md",)


def _corpus_entry(s):
    return {"requirements": [[key, r.layer, r.text, None if r.refines is None else str(r.refines),
                              [str(t) for t in r.needs], r.line] for key, r in s.requirements.items()],
            "report": [[e.line, e.kind, e.message] for e in s.report]}


def _corpus():
    """{repo path: its RequirementSet}, for the corpus outside CORPUS_CHANGED."""
    c, _, _ = _sample_trace()
    portfolio = ROOT / "samples" / "portfolio.md"
    sets = {"samples/portfolio.md": read_portfolio_file(roots_around(portfolio), portfolio,
                                                        br_namespace=c.requirements.br_namespace).brs}
    for name in sorted(FIXTURE_READS):
        namespace, layers, br = FIXTURE_READS[name]
        sets[f"tests/fixtures/requirements/{name}"] = read(name, namespace, layers=layers, br=br)
    return sets


def test_reader_corpus_unchanged():
    golden = json.loads(CORPUS_GOLDEN.read_bytes().decode("utf-8"))
    sets = _corpus()
    assert sorted(sets) == sorted([*golden, *CORPUS_NEW])
    assert not set(golden) & set(CORPUS_CHANGED)
    for path, want in golden.items():
        assert _corpus_entry(sets[path]) == want, path
        assert all((r.threshold, r.objective) == (None, None) for r in sets[path].requirements.values()), path


def test_format_doc_statement_example():
    blocks = [body for info, body in doc_blocks(FORMAT_DOC.read_bytes().decode("utf-8")) if info == "markdown"]
    stated = [s for s in (parse(body, "gantry") for body in blocks)
              if any(r.threshold is not None for r in s.requirements.values())]
    assert len(stated) == 1
    (s,) = stated
    assert s.report == []
    fr21 = s.requirements["gantry:FR-21"]
    assert fr21.threshold and fr21.objective
    assert fr21.refines == parse_id("gantry:SR-1")
    # It sits between the first project example and the empty form, which stays last.
    index = next(i for i, body in enumerate(blocks) if "FR-21" in body)
    assert 0 < index < len(blocks) - 1


def test_format_doc_states_threshold_rules():
    from test_build import heading_slugs

    text = FORMAT_DOC.read_bytes().decode("utf-8")
    assert "threshold-and-objective" in heading_slugs(text.replace("\r", ""))
    doc = " ".join(text.split())
    wanted = [req._WHY["stray_statement"], "a second `Threshold:` statement", "`malformed_objective`",
              "`unmatched_objective`", "`[thresholds]`", "holds without it", "needs brackets", "objective:",
              "a list item the reader cannot place", "the own text before the first nested item",
              "no longer shows a statement's text",
              # Final review: a constraint has no path box; satisfies: on a parent row stays silent.
              "A constraint has no path box, so its statements are read but not shown",
              "`satisfies:` on a row with children is ignored without a report"]
    assert [w for w in wanted if w not in doc] == []
