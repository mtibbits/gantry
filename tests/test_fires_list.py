"""The fires list (#30): its format, its reader, ages and the flag.

docs/fires-list.md states the rules. Control characters are built with chr()
at run time, never typed as escapes in source or held in a fixture file.
"""

import random
import time as clock
from dataclasses import replace
import datetime as dt
from datetime import date, datetime, time, timedelta
from pathlib import Path

from gantry import fires_list as fl
from gantry import model
from gantry.config import load_config
from gantry.roots import directory_roots
from page_safety import HOSTILE
from test_samples import GANTRY_SAMPLE
from helpers_config import write_config

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
    "bad_entries.md": list(range(1, 13)),
    "sites.md": [1, 2, 3, 4, 5, 6],
    "empty.md": [],
    "ended_only.md": [3, 4],
    "not_utf8.md": [],
    "stray_fence.md": [3],
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


# --- the field reader (Task 4) ----------------------------------------------------


def one(text, **kw):
    result = parse(text + LF, **kw)
    assert len(result.entries) == 1
    return result.entries[0], result


def test_reads_every_field():
    e, result = one("- [ ] 2026-10-24 09:40 @harbor the release build is red")
    assert (e.state, e.ended, e.date, e.time, e.site) == ("open", False, date(2026, 10, 24), time(9, 40), "harbor")
    assert e.reason == "the release build is red"
    assert result.report == ()
    e, _ = one("- [ ] 2026-10-24 09:40 the release build is red")
    assert e.site is None and e.reason == "the release build is red"


def test_ended_states():
    for box, state in (("[x]", "resolved"), ("[X]", "resolved"), ("[>]", "promoted")):
        e, result = one(f"- {box} 2026-10-20 @quarry done")
        assert (e.state, e.ended, result.status) == (state, True, "read")


# Each row of bad_entries.md, in order, with a phrase its message holds.
BAD_ROWS = [
    "no state box",
    "state box [?] is not [ ], [x] or [>]",
    "state box [-] is not",
    "state box [/] is not",
    "no space after the state box",
    "no date",
    "date 2026-02-30 is not a real YYYY-MM-DD date",
    "time 25:00 is not a time from 0:00 to 23:59",
    "time 12:60 is not a time from 0:00 to 23:59",
    "an empty @ names no site",
    "no reason",
    "no reason",
]


def test_bad_entries():
    text = fixture_text("bad_entries.md")
    result = parse(text)
    assert len(BAD_ROWS) == 12
    assert lines_of(result) == EXPECTED["bad_entries.md"] == list(range(1, 13))
    assert result.status == "read_with_problems"
    raws = text.split(LF)
    for e, phrase in zip(result.entries, BAD_ROWS, strict=True):
        assert (e.state, e.ended, e.raw) == ("unreadable", False, raws[e.line - 1]), e.line
        bad = [r for r in result.report if r.line == e.line]
        assert [r.kind for r in bad] == ["fires_bad_entry"], e.line
        assert phrase in bad[0].message, (e.line, bad[0].message)


def test_fields_kept_past_a_failure():
    for text in ("- [?] 2026-10-24 @harbor x", "- [ ]2026-10-24 @harbor x"):
        e, _ = one(text)
        assert (e.date, e.site, e.reason) == (date(2026, 10, 24), "harbor", "x")
    e, result = one("- 2026-10-24 call back")
    assert (e.state, e.date, e.reason) == ("unreadable", date(2026, 10, 24), "call back")
    assert result.report[0].message == "no state box"
    e, _ = one("- [ ] 2026-02-30 x")
    assert (e.date, e.reason) == (None, "x")
    e, result = one("- [ ] 2026-10-24 09:40 @harbor" + chr(9) + " ")
    assert (e.date, e.time, e.site, e.reason) == (date(2026, 10, 24), time(9, 40), "harbor", None)
    assert result.report[0].message == "no reason"


def test_failures_joined_in_field_order():
    e, result = one("1. [?] 2026-02-30 99:99 @")
    assert result.report[0].message == "; ".join([
        "not a top-level `- ` item", "state box [?] is not [ ], [x] or [>]",
        "date 2026-02-30 is not a real YYYY-MM-DD date", "time 99:99 is not a time from 0:00 to 23:59",
        "an empty @ names no site", "no reason",
    ])


def test_ended_bad_line_is_ended_and_reported():
    for text in ("- [x] 2026-02-30 done", "1. [x] 2026-10-20 @quarry done"):
        e, result = one(text)
        assert (e.state, e.ended) == ("unreadable", True), text
        assert kinds_of(result) == [(1, "fires_bad_entry")]
    e, result = one("1. [ ] 2026-10-20 @quarry still open")
    assert (e.state, e.ended, e.date) == ("unreadable", False, date(2026, 10, 20))


def test_time_shapes():
    e, _ = one("- [ ] 2026-10-24 9:40 x")
    assert e.time == time(9, 40)
    for token in ("24/7", "9am"):
        e, result = one(f"- [ ] 2026-10-24 {token} watch it")
        assert e.time is None and e.reason == f"{token} watch it"
        assert result.status == "read"


def test_site_token_ends_at_space():
    e, _ = one("- [ ] 2026-10-24 @harbor: the crane", names=("harbor:",))
    assert (e.site, e.reason) == ("harbor:", "the crane")


def test_notes_unreadable_legs():
    result = parse_fixture("notes.md")
    by_line = {e.line: e for e in result.entries}
    for n in (1, 4, 12):  # a bullet first in the file, after a heading, and at column 0
        assert (by_line[n].state, by_line[n].ended) == ("unreadable", False), n
    assert by_line[12].reason == "call the supplier back"
    assert by_line[10].state == "open"
    assert [line for line, kind in kinds_of(result) if kind == "fires_bad_entry"] == [1, 4, 12]


# --- sites, future dates, ages and the flag (Task 5) ------------------------------


def test_unknown_site():
    result = parse_fixture("sites.md", names=("harbor", "marsh"))
    assert lines_of(result) == EXPECTED["sites.md"]
    e = {e.line: e for e in result.entries}
    assert (e[1].state, e[1].site, e[1].site_known) == ("open", "nowhere", False)
    assert (e[2].state, e[2].site, e[2].site_known) == ("unreadable", "nowhere", False)
    assert (e[3].site, e[3].site_known) == ("Harbor", False)
    assert (e[4].site, e[4].site_known) == ("marsh", True)  # listed, as #7 lists a site it cannot build
    assert (e[6].site, e[6].site_known) == ("harbor", True)
    assert kinds_of(result) == [(1, "fires_unknown_site"), (2, "fires_bad_entry"), (2, "fires_unknown_site"),
                                (3, "fires_unknown_site")]
    assert result.report[0].message == "@nowhere names no site in this installation"
    assert result.status == "read_with_problems"


def test_unknown_site_message_is_one_line():
    name = "a" + chr(0x2028) + "b"
    _, result = one(f"- [ ] 2026-10-24 @{name} x")
    assert result.report[0].message == "@a" + chr(92) + "u2028b names no site in this installation"


def test_flag_boundary():
    ref = datetime(2026, 10, 27, 9, 0)
    young, _ = one("- [ ] 2026-10-24 10:00 @harbor x", reference=ref)
    old, _ = one("- [ ] 2026-10-24 09:00 @harbor x", reference=ref)
    assert fl.entry_age(young, ref) == timedelta(days=2, hours=23)
    assert not fl.still_a_fire(young, ref, 3)
    assert fl.entry_age(old, ref) == timedelta(days=3)
    assert fl.still_a_fire(old, ref, 3)
    ended, _ = one("- [x] 2026-01-01 @harbor x", reference=ref)
    assert not fl.still_a_fire(ended, ref, 3)


def test_entry_since():
    e, _ = one("- [ ] 2026-10-24 09:40 x")
    assert fl.entry_since(e) == datetime(2026, 10, 24, 9, 40)
    e, _ = one("- [ ] 2026-10-24 x")
    assert fl.entry_since(e) == datetime(2026, 10, 24, 0, 0)
    e, _ = one("- [ ] x")
    assert fl.entry_since(e) is None


def test_unreadable_fire_flag():
    dated, _ = one("- [?] 2026-10-01 @harbor x")
    assert dated.state == "unreadable" and fl.still_a_fire(dated, REFERENCE, 3)
    undated, _ = one("- [ ] @harbor x")
    assert fl.entry_age(undated, REFERENCE) is None
    assert not fl.still_a_fire(undated, REFERENCE, 3)


def test_future_date():
    for text in ("- [ ] 2026-12-01 @harbor x", "- [?] 2026-12-01 @harbor x"):
        e, result = one(text)
        assert fl.entry_age(e, REFERENCE) == timedelta(0)
        assert [k for _, k in kinds_of(result) if k == "fires_future_date"] == ["fires_future_date"]
        message = [r.message for r in result.report if r.kind == "fires_future_date"][0]
        assert message == "the entry is dated after the reference time; its age is 0"
    midnight = datetime(2026, 10, 24)
    e, result = one("- [ ] 2026-10-24 @harbor x", reference=midnight)
    assert (result.report, fl.entry_age(e, midnight)) == ((), timedelta(0))
    e, result = one("- [ ] 2026-10-24 08:00 @harbor x", reference=midnight)
    assert kinds_of(result) == [(1, "fires_future_date")]


def test_large_check_days_does_not_raise():
    e, _ = one("- [ ] 2026-10-01 @harbor x")
    assert fl.still_a_fire(e, REFERENCE, 10**30) is False


def test_fires_reference(tmp_path):
    now = datetime(2026, 10, 24, 15, 30)
    pinned = load_config(write_config(tmp_path / "pinned", as_of="2026-01-20"))
    assert fl.fires_reference(pinned, now) == datetime(2026, 1, 20, 0, 0)
    unpinned = load_config(write_config(tmp_path / "unpinned"))
    assert fl.fires_reference(unpinned, now) is now


class ClockRead(AssertionError):
    pass


def raise_on_clock(monkeypatch):
    """Patch datetime.now and date.today to raise, on the datetime module and
    on every date or datetime class gantry.fires_list holds (tests/clock.py's
    technique: plain subclasses, so construction and arithmetic still work)."""
    real_date, real_datetime = dt.date, dt.datetime

    class NoToday(real_date):
        __slots__ = ()

        @classmethod
        def today(cls):
            raise ClockRead("date.today")

    class NoNow(real_datetime, NoToday):
        __slots__ = ()

        @classmethod
        def now(cls, tz=None):
            raise ClockRead("datetime.now")

        @classmethod
        def today(cls):
            raise ClockRead("datetime.today")

    monkeypatch.setattr(dt, "date", NoToday)
    monkeypatch.setattr(dt, "datetime", NoNow)
    patched = 0
    for name, value in list(vars(fl).items()):
        if value is real_datetime:
            monkeypatch.setattr(fl, name, NoNow)
            patched += 1
        elif value is real_date:
            monkeypatch.setattr(fl, name, NoToday)
            patched += 1
    assert patched >= 2
    try:
        dt.datetime.now()
    except ClockRead:
        pass
    else:
        raise AssertionError("the patch is not live")


def test_no_clock_read(monkeypatch, tmp_path):
    raise_on_clock(monkeypatch)
    result = parse_fixture("well_formed.md")
    assert lines_of(result) == EXPECTED["well_formed.md"]
    e = result.entries[0]
    assert fl.entry_age(e, REFERENCE) is not None
    assert fl.still_a_fire(e, REFERENCE, 3) is True


# --- reading the file, the statuses, hostile text (Task 6) -------------------------


def read(roots, path, names=NAMES, reference=REFERENCE):
    return fl.read_fires_list(roots, path, names, reference)


def _missing(tmp_path, monkeypatch):
    return directory_roots([tmp_path]), tmp_path / "missing.md", "fires_list: the fires list does not exist"


def _directory(tmp_path, monkeypatch):
    (tmp_path / "fires.md").mkdir()
    return directory_roots([tmp_path]), tmp_path / "fires.md", "fires_list: the fires list is not a file"


def _not_utf8(tmp_path, monkeypatch):
    return (directory_roots([FIXTURES]), FIXTURES / "not_utf8.md",
            "fires_list: the fires list is not valid UTF-8 at byte 28")


def _outside_roots(tmp_path, monkeypatch):
    (tmp_path / "inside").mkdir()
    path = tmp_path / "fires.md"
    path.write_bytes(b"- [ ] 2026-10-24 @harbor x\n")
    return (directory_roots([tmp_path / "inside"]), path,
            "fires_list: cannot read the fires list: fires_list is outside the configured roots")


def _permission(tmp_path, monkeypatch):
    path = tmp_path / "fires.md"
    path.write_bytes(b"- [ ] 2026-10-24 @harbor x\n")

    def refuse(roots, p, *, key, site=None):
        raise PermissionError(13, "Permission denied", str(p))

    monkeypatch.setattr(fl, "stat_confined", refuse)
    return directory_roots([tmp_path]), path, "fires_list: cannot read the fires list: Permission denied"


NOT_READ = {"missing": _missing, "directory": _directory, "not_utf8": _not_utf8,
            "outside_roots": _outside_roots, "permission": _permission}


def test_not_read(tmp_path, monkeypatch):
    ran = 0
    for case, make in NOT_READ.items():
        folder = tmp_path / case
        folder.mkdir()
        with monkeypatch.context() as m:
            roots, path, message = make(folder, m)
            result = read(roots, path)
        assert (result.status, result.entries, result.reason) == ("not_read", (), message), case
        assert [(e.site, e.line, e.kind, e.message) for e in result.report] == [
            (None, None, "fires_list_unreadable", message)], case
        for text in (message, result.reason):
            assert str(tmp_path) not in text and str(FIXTURES) not in text and "fires.md" not in text, case
        ran += 1
    assert ran == 5


def test_not_configured():
    result = read(directory_roots([FIXTURES]), None)
    assert (result.status, result.entries, result.report) == ("not_configured", (), ())
    assert result.reason == "fires_list is not set"


def test_read_statuses(tmp_path):
    roots = directory_roots([FIXTURES])
    for name in ("empty.md", "ended_only.md"):
        result = read(roots, FIXTURES / name)
        assert lines_of(result) == EXPECTED[name]
        assert (result.status, result.report, result.reason) == ("read", (), None), name
        assert [e for e in result.entries if not e.ended] == []
    path = tmp_path / "fires.md"
    path.write_bytes((FIXTURES / "ended_only.md").read_bytes() + b"- [?] 2026-10-24 @harbor x\n")
    result = read(directory_roots([tmp_path]), path)
    assert result.status == "read_with_problems"


def test_read_matches_parse():
    result = read(directory_roots([FIXTURES]), FIXTURES / "well_formed.md")
    assert result == parse_fixture("well_formed.md")


def test_hostile_text_round_trips():
    assert len(HOSTILE) == 11
    no_blank = [s for s in HOSTILE if " " not in s and chr(9) not in s]
    assert no_blank
    for s in HOSTILE:
        e, _ = one("- [ ] 2026-10-24 @harbor " + s)
        assert e.reason == s and e.reason.encode("utf-8") == s.encode("utf-8")
        line = "- " + s
        e, _ = one(line)
        assert e.state == "unreadable"
        assert e.raw == line and e.raw.encode("utf-8") == line.encode("utf-8")
    for s in no_blank:
        e, _ = one(f"- [ ] 2026-10-24 @{s} x")
        assert e.site == s and e.site.encode("utf-8") == s.encode("utf-8")


def test_no_clock_read_from_the_file(monkeypatch):
    raise_on_clock(monkeypatch)
    result = read(directory_roots([FIXTURES]), FIXTURES / "well_formed.md")
    assert lines_of(result) == EXPECTED["well_formed.md"]


def test_stray_fence_counts():
    result = parse_fixture("stray_fence.md")
    assert lines_of(result) == EXPECTED["stray_fence.md"]
    assert (result.skipped_in_fences, result.status) == (1, "read")



# --- the sample (Task 8) -------------------------------------------------------------


def test_sample_reads_clean():
    from gantry.build import site_list
    from gantry.sites import config_roots

    config = load_config(GANTRY_SAMPLE)
    roots = config_roots(config, purpose="check")
    sites, _ = site_list(config, roots)
    reference = fl.fires_reference(config, None)
    assert config.as_of_pinned and reference == datetime(2026, 1, 20)
    result = fl.read_fires_list(roots, config.fires_list, [s.name for s in sites], reference)
    assert (result.status, result.report, result.skipped_empty) == ("read", (), 0)
    assert lines_of(result) == [6, 8, 9, 10, 11, 12]  # line 7 is a note
    assert [e.state for e in result.entries] == ["open"] * 4 + ["resolved", "promoted"]
    timed = [e for e in result.entries if e.time is not None]
    assert len(timed) == 1 and timed[0].date < config.as_of
    flagged = [e.line for e in result.entries if fl.still_a_fire(e, reference, config.fires_check_days)]
    assert flagged == [9]
    assert all(e.site_known for e in result.entries if e.site is not None)



# --- the docs (Task 9) ---------------------------------------------------------------


DOC = Path(__file__).parent.parent / "docs" / "fires-list.md"


def doc_text():
    from helpers_docs import markdown_text

    return markdown_text(DOC)


def first_fence_under(heading):
    """The lines of the first fenced block in the section under `heading`."""
    lines = doc_text().split("\n")
    start = lines.index(heading)
    section = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        section.append(line)
    opener = next(i for i, line in enumerate(section) if line.startswith("```"))
    closer = next(i for i in range(opener + 1, len(section)) if section[i].startswith("```"))
    return section[opener + 1:closer]


def test_doc_names_every_kind_status_and_key():
    text = doc_text()
    assert len(fl.REPORT_KINDS) == 5
    statuses = ("read", "read_with_problems", "not_read", "not_configured")
    tokens = [*fl.REPORT_KINDS, *statuses, "fires_check_days", "fires_list", "gantry fires", "skipped_empty",
              "skipped_in_fences", "[-]", "[/]", "as_of", "fires_list_unreadable"]
    assert [t for t in tokens if f"`{t}`" not in text] == []
    # One heading per prose limb of the documentation criterion, in its order.
    headings = ["## Entries", "## Lines", "## Notes under an entry", "## Items that hold nothing",
                "## Entry-like lines", "## Fences", "## How an entry ends", "## Still a fire?",
                "## The reference time", "## Statuses", "## Checking the file", "## Editing from a phone",
                "## Why not a file-sync tool", "## Where the file may live"]
    lines = text.split("\n")
    assert [h for h in headings if h not in lines] == []
    assert [lines.index(h) for h in headings] == sorted(lines.index(h) for h in headings)
    joined = text.replace(LF, " ")
    for phrase in ("U+2028", "lone carriage return", "same day", "public repository",
                   "Gantry's own repository", "paid work", "not freshness",
                   "with or without a list marker", "no-break space"):
        assert phrase in joined, phrase


def test_doc_examples_read_as_documented():
    block = first_fence_under("## Entries")
    assert block
    result = parse(LF.join(block) + LF, names=("harbor", "quarry"))
    assert (result.status, len(result.entries)) == ("read", 4)
    notes = first_fence_under("## Notes under an entry")
    result = parse(LF.join(notes) + LF, names=("harbor", "quarry"))
    assert (result.status, lines_of(result), len(notes)) == ("read", [1], 2)



def test_fenced_lines_count_with_or_without_a_marker():
    # docs/fires-list.md: skipped_in_fences counts entry-shaped lines with or without a list marker.
    block = ["```", "- [ ] 2026-10-24 @harbor x", "[ ] 2026-10-24 @harbor x", "2026-10-24 bare", "- prose", "```"]
    result = parse(LF.join(block) + LF)
    assert (result.entries, result.skipped_in_fences, result.status) == ((), 3, "read")


def test_documented_limits_are_prose():
    # docs/fires-list.md's "Entry-like lines" names these as prose: only the
    # ASCII markers, spaces and tabs make an entry-like line.
    nbsp, bullet, dash = chr(0xA0), chr(0x2022), chr(0x2013)
    lines = ["-" + nbsp + "[ ] 2026-10-24 @harbor x", bullet + " [ ] 2026-10-24 @harbor x",
             dash + " [ ] 2026-10-24 @harbor x", "> - [ ] 2026-10-24 @harbor x", "# [ ] 2026-10-24 @harbor x"]
    for line in lines:
        result = parse(line + LF)
        assert (result.entries, result.report, result.status) == ((), (), "read"), line
