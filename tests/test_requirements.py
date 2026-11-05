"""The requirements file format and ID scheme (#15).

docs/requirements-format.md states the rules these tests pin.
"""

import time
from pathlib import Path

import pytest

import gantry.config as cfg
from gantry import requirements as req
from gantry.requirements import (
    LAYERS,
    PROJECT_LAYERS,
    ReqId,
    parse_id,
    parse_requirements,
    read_requirements,
)

# A fictional mixed-case namespace (operator decision Q3); the format doc's
# case rule uses the same name.
MIXED_NS = "harborWest"

FIXTURES = Path(__file__).parent / "fixtures" / "requirements"
ALL = set(LAYERS)


def parse(text, namespace="alpha", *, layers=PROJECT_LAYERS, br="life", source="t.md", first_line=1):
    return parse_requirements(text, namespace, source=source, first_line=first_line, layers=layers,
                              br_namespace=br)


def read(name, namespace="alpha", *, layers=PROJECT_LAYERS, br="life"):
    return read_requirements(FIXTURES / name, namespace, layers=layers, br_namespace=br)


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


def test_indented_item_after_blank_opens_an_item():
    s = parse("- FR-1: a\n\n  - FR-3: e\n")
    assert list(s.requirements) == ["alpha:FR-1", "alpha:FR-3"]
    assert s.report == []


def test_item_under_a_prose_bullet_is_nested():
    s = parse("- Functional requirements\n  - FR-1: x\n")
    assert s.requirements == {}
    (entry,) = s.report
    assert (entry.line, entry.kind) == (2, "malformed_item")
    assert "requirements are not nested" in entry.message


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


HOSTILE = [
    "- " + "a" * 100_000,
    "- " + "a-" * 50_000,
    "- " + "a1" * 50_000,
    "- FR-" + "9" * 100_000,
    "- FR-1" + " " * 100_000,
    "- " + "*_" * 50_000,
    " " * 100_000 + "x",
    "- FR-1: x " + "{a:" * 33_000,
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
    s = read_requirements(path, "alpha", layers=PROJECT_LAYERS, br_namespace="life")
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
    s = read_requirements(p, "alpha", layers=PROJECT_LAYERS, br_namespace="life")
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
        read_requirements(FIXTURES / "empty.md", namespace, layers=layers, br_namespace=br)
