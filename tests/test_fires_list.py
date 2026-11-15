"""The fires list (#30): its format, its reader, ages and the flag.

docs/fires-list.md states the rules. Control characters are built with chr()
at run time, never typed as escapes in source or held in a fixture file.
"""

import random
import time as clock
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from gantry import fires_list as fl
from gantry import model

FIXTURES = Path(__file__).parent / "fixtures" / "fires_list"
NAMES = ("harbor", "quarry", "marsh")
# Later than every date in the fixtures and in docs/fires-list.md's examples.
REFERENCE = datetime(2026, 11, 1, 12, 0)
LF, CR = chr(10), chr(13)

# Every fixture, with the line numbers of the entries it holds: a fixture
# that loses a line reddens, and no fixture goes unused.
EXPECTED = {
    "well_formed.md": [5, 6, 7, 8],
    "fences.md": [11],
    "unclosed_fence.md": [3, 5],
    "empty_items.md": [3],
    "notes.md": [1, 4, 6, 10, 12],
    "entry_like.md": [3, 4, 5, 6, 7],
}


def fixture_text(name):
    return (FIXTURES / name).read_bytes().decode("utf-8")


def parse(text, names=NAMES, reference=REFERENCE):
    return fl.parse_fires_list(text, names, reference)


def parse_fixture(name, **kw):
    return parse(fixture_text(name), **kw)


def lines_of(result):
    return [e.line for e in result.entries]


def kinds_of(result):
    return [(e.line, e.kind) for e in result.report]


def test_every_fixture_is_expected():
    assert sorted(p.name for p in FIXTURES.iterdir()) == sorted(EXPECTED)


# --- report kinds and the report entry (Task 2) ---------------------------------


KINDS = ("fires_list_unreadable", "fires_bad_entry", "fires_unclosed_fence", "fires_unknown_site",
         "fires_future_date")


def test_report_kinds_and_order():
    assert fl.REPORT_KINDS == KINDS
    for kind in KINDS:
        line = None if kind == "fires_list_unreadable" else 4
        e = fl.FiresReportEntry(None, line, kind, "m")
        assert (e.site, e.line, e.kind, e.message) == (None, line, kind, "m")
    whole = fl.FiresReportEntry(None, None, "fires_list_unreadable", "a")
    early = fl.FiresReportEntry(None, 2, "fires_unknown_site", "b")
    bad = fl.FiresReportEntry(None, 7, "fires_bad_entry", "c")
    future = fl.FiresReportEntry(None, 7, "fires_future_date", "d")
    fence = fl.FiresReportEntry(None, 7, "fires_unclosed_fence", "e")
    expected = [whole, early, bad, fence, future]
    shuffled = expected[:]
    random.Random(30).shuffle(shuffled)
    assert shuffled != expected
    assert fl.report_order(shuffled) == expected
    assert fl.FiresReportEntry is not model.ReportEntry


# --- lines, fences and classification (Task 3) ----------------------------------


ENTRY = "- [ ] 2026-10-24 @harbor the release build is red"


def test_lines_split_at_lf_only():
    inside = chr(0x2028) + "b" + chr(0x85) + "c" + CR + "d"
    text = "- [ ] 2026-10-24 @harbor a" + inside + LF + "- [ ] 2026-10-25 @harbor next" + LF
    result = parse(text)
    assert lines_of(result) == [1, 2]
    raw = result.entries[0].raw
    assert chr(0x2028) in raw and chr(0x85) in raw and CR in raw
    assert raw.endswith(inside)


def test_final_cr_with_no_lf_is_dropped():
    result = parse(ENTRY + CR)
    assert lines_of(result) == [1]
    assert result.entries[0].raw == ENTRY


def test_bom_and_crlf_read_alike():
    text = fixture_text("well_formed.md")
    plain = parse(text)
    assert lines_of(plain) == EXPECTED["well_formed.md"]
    assert parse(chr(0xFEFF) + text.replace(LF, CR + LF)) == plain


def test_markers_and_indent():
    base = parse(ENTRY).entries
    assert len(base) == 1
    for prefix in ("* ", "+ ", " - ", "  - ", "   - ", "-" + chr(9)):
        text = prefix + ENTRY[2:]
        got = parse(text).entries
        assert [replace(e, raw="") for e in got] == [replace(e, raw="") for e in base], prefix
        assert got[0].raw == text


def test_prose_is_not_read():
    text = LF.join([
        "# Fires", "## 2026-10-24 a heading with a date", "", "A paragraph about [x] boxes.",
        "    some indented text", chr(9) + "tabbed text", "> a quote", "Words, then 2026-10-24.", "-",
    ]) + LF
    result = parse(text)
    assert (result.entries, result.report, result.status) == ((), (), "read")
    assert (result.skipped_empty, result.skipped_in_fences) == (0, 0)


def test_closed_fence_hides_and_counts():
    result = parse_fixture("fences.md")
    assert lines_of(result) == EXPECTED["fences.md"]
    assert result.skipped_in_fences == 2
    assert (result.report, result.status) == ((), "read")


def test_unclosed_fence_reported_and_read_past():
    result = parse_fixture("unclosed_fence.md")
    assert lines_of(result) == EXPECTED["unclosed_fence.md"]
    assert kinds_of(result) == [(4, "fires_unclosed_fence")]
    assert result.report[0].message == "a fence opened here is never closed; the lines after it are still read"
    assert result.status == "read_with_problems"


def test_backtick_info_with_a_backtick_opens_no_fence():
    result = parse("```js```" + LF + ENTRY + LF)
    assert lines_of(result) == [2]
    assert (result.report, result.status) == ((), "read")


def test_empty_items_skipped():
    result = parse_fixture("empty_items.md")
    assert lines_of(result) == EXPECTED["empty_items.md"]
    assert result.skipped_empty == 2
    assert (result.report, result.status) == ((), "read")
    # An entry-like line that holds nothing after its box is skipped alike.
    result = parse(LF.join([ENTRY, "1. [ ]", "-[x]" + chr(9), "    - [ ] "]) + LF)
    assert lines_of(result) == [1]
    assert result.skipped_empty == 3
    assert (result.report, result.status) == ((), "read")


def test_thematic_breaks():
    result = parse(LF.join(["- - -", "* * *", "---", "___", " -" + chr(9) + "- -", ENTRY]) + LF)
    assert lines_of(result) == [6]
    assert (result.skipped_empty, result.report, result.status) == (0, (), "read")


def test_notes():
    result = parse_fixture("notes.md")
    assert lines_of(result) == EXPECTED["notes.md"]  # 7, 9 and 11 are notes


# Each line or block ends a run of notes: the bullet after it is an entry.
CHAIN_ENDS = {
    "thematic break": ["- - -"],
    "empty item": ["- "],
    "entry-like line holding nothing": ["1. [ ]"],
    "closed fence": ["```", "x", "```"],
    "unclosed fence opener": ["~~~"],
    "heading": ["# Later"],
    "paragraph": ["Some prose."],
}


def test_note_chain_ends():
    assert len(CHAIN_ENDS) == 7
    for name, block in CHAIN_ENDS.items():
        result = parse(LF.join([ENTRY, *block, "  - a bullet"]) + LF)
        assert lines_of(result) == [1, len(block) + 2], name


def test_entry_like_lines():
    tab = chr(9) + "- [ ] 2026-10-24 @harbor tab-indented"
    for text, expected in ((fixture_text("entry_like.md"), EXPECTED["entry_like.md"]), (tab + LF, [1])):
        result = parse(text)
        assert lines_of(result) == expected
        bad = [e for e in result.report if e.kind == "fires_bad_entry"]
        assert [e.line for e in bad] == expected
        assert all("not a top-level `- ` item" in e.message for e in bad)
        assert all(e.state == "unreadable" for e in result.entries)
        assert result.status == "read_with_problems"


def test_hostile_shapes_are_cheap():
    lines = [" " * 20000 + "x", "- " + " " * 20000 + "x", "1" * 20000 + "x", "- " * 10000 + "x",
             chr(9) * 20000 + "[", "`" * 20000 + "x"]
    start = clock.perf_counter()
    for line in lines:
        parse(line + LF)
    assert clock.perf_counter() - start < 0.5
