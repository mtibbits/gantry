"""The fire collector (#32): one ordered FireReport per full build, a read
status for every source and the two named STUCK counts.

Inputs are built in memory: a hostile folder name cannot exist on a Windows
disk, and the collector reads only what build_all hands it.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import helpers_portfolio as hp
from page_safety import HOSTILE
from test_samples import ROOT as ROOT_DIR

from gantry import devagent_records as dr
from gantry import fires, model
from gantry import fires_list as fl
from gantry.fires import (
    FAIL_CLOSED,
    KINDS,
    OK_STATUSES,
    OPEN_STATES,
    STATUSES,
    Fire,
    FireReport,
    SourceStatus,
    fire_reference,
    naive_reference,
)

AS_OF = date(2026, 1, 20)
EST = timezone(timedelta(hours=-5), "EST")
IST = timezone(timedelta(hours=5, minutes=30), "IST")


def config(pinned=True, check_days=3):
    """The config keys the collector reads."""
    return SimpleNamespace(as_of=AS_OF, as_of_pinned=pinned, fires_check_days=check_days)


# --- the reference time -------------------------------------------------------------


NOWS = (datetime(2026, 1, 22, 3, 15, tzinfo=timezone.utc), datetime(2026, 1, 21, 23, 59, tzinfo=EST))


@pytest.mark.parametrize("now", NOWS)
@pytest.mark.parametrize("local", (EST, IST))
def test_reference_with_as_of_is_midnight_utc(now, local):
    got = fire_reference(config(), now, local)
    assert got == datetime(AS_OF.year, AS_OF.month, AS_OF.day, tzinfo=timezone.utc)
    assert got.utcoffset() == timedelta(0)


@pytest.mark.parametrize("now", NOWS)
@pytest.mark.parametrize("local", (EST, IST))
def test_reference_without_as_of_is_build_start_in_local_zone(now, local):
    got = fire_reference(config(pinned=False), now, local)
    assert got == now
    assert got.utcoffset() == local.utcoffset(None)


@pytest.mark.parametrize("pinned", (True, False))
@pytest.mark.parametrize("local", (EST, IST))
def test_reference_agrees_with_fires_list_rule(pinned, local):
    c, now = config(pinned=pinned), NOWS[0]
    assert naive_reference(fire_reference(c, now, local)) == fl.fires_reference(
        c, now.astimezone(local).replace(tzinfo=None))


def test_reference_refuses_naive_now():
    with pytest.raises(ValueError):
        fire_reference(config(), datetime(2026, 1, 22, 3, 15))


def test_states_are_partitioned():
    assert set(model.STATE_GLYPHS.values()) == set(OPEN_STATES) | {"done", "skipped", "parked"}
    assert len(OPEN_STATES) == 4


def test_vocabularies():
    assert KINDS == ("fires_list", "p0_leaf", "stuck")
    assert set(OK_STATUSES) == {"read", "absent"} and set(OK_STATUSES) <= set(STATUSES)
    assert FAIL_CLOSED == ("leaf_unknown", "parked_unknown")


# --- the report ---------------------------------------------------------------------


def a_fire(**kw):
    values = dict(kinds=("fires_list",), site=None, site_as_written=None, issue=None, leaf_key=None, title=None,
                  why=("a reason",), unreadable=False, fail_closed=None, since=None, date_only=False, age=None,
                  still_a_fire=False)
    values.update(kw)
    return Fire(**values)


def status(value, source="p0_leaf", site="harbor"):
    return SourceStatus(source, site, value, None)


@pytest.mark.parametrize("values, all_read, others", [
    ((), False, 0),  # no status at all: fail closed
    (("read",), True, 0),
    (("absent",), True, 0),
    (("read", "absent", "read"), True, 0),
    (("read", "read_with_problems"), False, 1),
    (("not_read",), False, 1),
    (("not_configured", "read"), False, 1),
    (("not_read", "read_with_problems", "absent"), False, 2),
])
def test_report_properties(values, all_read, others):
    sources = tuple(status(v) for v in values)
    empty = FireReport((), sources, {}, {})
    assert empty.all_read is all_read
    assert empty.confirmed_none is all_read
    assert empty.not_fully_read == others
    burning = FireReport((a_fire(),), sources, {}, {})
    assert burning.all_read is all_read
    assert burning.confirmed_none is False
    assert burning.not_fully_read == others


# --- builders -------------------------------------------------------------------------

REFERENCE = datetime(2026, 1, 20, tzinfo=timezone.utc)
GLYPH_OF = {state: glyph for glyph, state in model.STATE_GLYPHS.items()}


def leaf(key, state="pending", priority=None, *, issue=None, glyph="", title=None, line=1):
    """A parsed leaf. `glyph=None` builds a state-only leaf; `issue` defaults to the key."""
    return model.Leaf(key, key if issue is None else issue, key, f"title {key}" if title is None else title,
                      GLYPH_OF[state] if glyph == "" else glyph, state, state not in ("done", "skipped"),
                      priority, (), {}, None, line)


def site(name, leaves=(), *, read=True, problem=None, reason=None, report=(), planning=True, data=None):
    """A site as build_all leaves it: read (its WBS.md in `inputs`) or not, with
    its SiteEntry problem and its index reason."""
    return SimpleNamespace(name=name, leaves=tuple(leaves), read=read, problem=problem, reason=reason,
                           report=tuple(report), planning=planning, data=data)


def fake_result(*specs):
    from pathlib import PurePath

    from gantry.build import SiteSummary
    from gantry.sites import SiteEntry

    entries, inputs, summaries = [], {}, []
    for s in specs:
        entries.append(SiteEntry(s.name, PurePath("plan", s.name) if s.planning else None, s.problem))
        if s.read:
            data = s.data or model.SiteData(s.leaves, (), (), (), s.report)
            inputs[s.name] = SimpleNamespace(data=data)
            summaries.append(SiteSummary(s.name, "built"))
        else:
            summaries.append(SiteSummary(s.name, "not_built", s.reason or s.problem))
    return SimpleNamespace(sites=tuple(entries), inputs=inputs, summaries=tuple(summaries),
                           roots=SimpleNamespace(roots=()))


NO_FIRES_LIST = fl.FiresList((), "read", None, (), 0, 0)


def fires_list_of(text, names=("harbor", "quarry")):
    return fl.parse_fires_list(text, names, naive_reference(REFERENCE))


def context(result, fires_list=NO_FIRES_LIST, records=None, cfg=None, reference=REFERENCE):
    return fires.FireContext(result, cfg or config(), fires_list, {} if records is None else records, reference)


def collect_one(source, ctx):
    """One source's result, as a report (counts included)."""
    got = source.collect(ctx)
    return FireReport(tuple(got.fires), tuple(got.statuses), dict(got.stuck_parked), dict(got.stuck_no_open_leaf))


def p0(result, **kw):
    return collect_one(fires.P0Source(), context(result, **kw))


def by_source(report, source):
    return [(s.site, s.status, s.reason) for s in report.sources if s.source == source]


# --- the P0-leaf source -----------------------------------------------------------------


@pytest.mark.parametrize("state", sorted(model.STATE_GLYPHS.values()))
def test_p0_open_states_are_fires(state):
    report = p0(fake_result(site("harbor", [leaf("Issue-1", state, "P0"), leaf("Issue-2", state, "P1")])))
    expected = 1 if state in OPEN_STATES else 0
    assert len(report.fires) == expected
    for fire in report.fires:
        assert (fire.kinds, fire.site, fire.leaf_key, fire.why) == (("p0_leaf",), "harbor", "Issue-1", ("P0 leaf",))
        assert (fire.issue, fire.title, fire.since, fire.age) == ("Issue-1", "title Issue-1", None, None)
        assert fire.this_machine is False and fire.still_a_fire is None


def test_p0_inherited_is_not_a_fire():
    leaves = [leaf("Issue-1", "pending", "P0"), leaf("Issue-2", "pending", "P1")]
    data = model.SiteData(tuple(leaves), (), (("Issue-2", "Issue-1"),), (), ())
    report = p0(fake_result(site("harbor", data=data)))
    assert [f.leaf_key for f in report.fires] == ["Issue-1"]


# devagent has already unquoted `"P0"` (#12): a stored value that still holds the quotes is not P0.
@pytest.mark.parametrize("raw", ["P0", model.ParsedValue("P0"), '"P0"', "p0", "P00", "[P0]", None])
def test_p0_goes_through_priority_rule(raw):
    from gantry import priority

    report = p0(fake_result(site("harbor", [leaf("Issue-1", "pending", raw)])))
    assert len(report.fires) == (1 if priority.rank(raw) == 0 else 0)
    assert len(report.fires) == (1 if raw == "P0" else 0)


def test_p0_state_only_leaves():
    states = sorted(model.STATE_GLYPHS.values())
    with_glyph = [leaf(f"Issue-{i}", s, "P0", line=i) for i, s in enumerate(states)]
    state_only = [replace(x, glyph=None) for x in with_glyph]
    a = p0(fake_result(site("harbor", with_glyph)))
    b = p0(fake_result(site("harbor", state_only)))
    assert len(a.fires) == len(OPEN_STATES)
    assert [f.leaf_key for f in a.fires] == [f.leaf_key for f in b.fires]


def test_p0_statuses():
    from gantry.build import CLASH
    from gantry.sites import NO_WBS

    result = fake_result(
        site("harbor", [leaf("Issue-1", "pending", "P0")]),
        site("bakery", read=False, problem=NO_WBS),
        site("marsh", read=False, reason="WBS.md is not valid UTF-8 at byte 7"),
        site("quarry", read=False, problem="planning directory does not exist"),
        site("b\tad", read=False, problem="name does not match the rule", planning=False),
        site("Harbor", read=False, reason=CLASH.format(earlier="harbor")),
        site("orchard", [leaf("Issue-2")]),  # read; its render then failed
    )
    result.summaries = tuple(replace(s, status="not_built", reason="render error: X") if s.name == "orchard" else s
                             for s in result.summaries)
    report = p0(result)
    assert by_source(report, "p0_leaf") == [
        ("harbor", "read", None),
        ("bakery", "absent", NO_WBS),
        ("marsh", "not_read", "WBS.md is not valid UTF-8 at byte 7"),
        ("quarry", "not_read", "planning directory does not exist"),
        ("b\tad", "not_read", "name does not match the rule"),
        ("Harbor", "not_read", CLASH.format(earlier="harbor")),
        ("orchard", "read", None),
    ]
    assert len(report.fires) == 1


# The fires tests' WBS.md (tests/fixtures/portfolio/fires.md) as devagent's aggregator reads it at the pin
# (#12): line 1 is one clean leaf, each other line one shape. FIRES[n] is line n's text.
FIRES = [None, *(ROOT_DIR / "tests" / "fixtures" / "portfolio" / "fires.md").read_bytes().decode("utf-8").splitlines()]


def fires_site(*lines):
    """devagent's reading of fires.md narrowed to `lines`, as site harbor's SiteData."""
    return hp.site_of(hp.keep_lines(hp.fixture_doc("fires", "harbor"), *lines))


# p0 and PO are read as nodes and reported bad_priority; the rest are devagent's near misses (skipped_line),
# * [ ] among them: the stopgap neither read nor reported it, so it hid a P0 there.
@pytest.mark.parametrize("line", [2, 3, 4, 5, 14])
def test_p0_report_problems_fail_closed(line):
    assert FIRES[1] == "- [ ] Pour the slab {issue: Issue-1}"
    assert "P0" in FIRES[line].upper().replace("PO", "P0")
    control = p0(fake_result(site("harbor", data=fires_site(1))))
    assert by_source(control, "p0_leaf") == [("harbor", "read", None)]
    assert control.confirmed_none is True

    data = fires_site(1, line)
    report = p0(fake_result(site("harbor", data=data)))
    assert by_source(report, "p0_leaf") == [("harbor", "read_with_problems", "WBS.md lines not read as leaves: 1")]
    assert report.confirmed_none is False


# --- the fires-list source --------------------------------------------------------------

FIRES_TEXT = """\
- [ ] 2026-01-19 @harbor the east crane failed
- [ ] 2026-01-12 nowhere @x but a reason
- [ ] 2026-01-18 @atlantis an unknown site
- [x] 2026-01-15 @harbor resolved
- [>] 2026-01-16 @harbor promoted
- [?] 2026-01-17 @quarry a bad box
- [ ] 2026-01-10 10:30 @quarry with a time
"""


def fires_of(text, **kw):
    return collect_one(fires.FiresListSource(), context(fake_result(), fires_list=fires_list_of(text), **kw))


def test_fires_list_entries_are_fires():
    parsed = fires_list_of(FIRES_TEXT)
    report = fires_of(FIRES_TEXT)
    assert [f.why[0] for f in report.fires] == [
        "the east crane failed", "nowhere @x but a reason", "an unknown site", "- [?] 2026-01-17 @quarry a bad box",
        "with a time"]
    first, second, unknown, bad, timed = report.fires
    assert (first.kinds, first.site, first.site_as_written, first.unreadable) == (("fires_list",), "harbor", None,
                                                                                  False)
    assert first.date_only is True and timed.date_only is False
    assert first.since == datetime(2026, 1, 19, tzinfo=timezone.utc)
    assert first.age == timedelta(days=1)
    assert timed.since == datetime(2026, 1, 10, 10, 30, tzinfo=timezone.utc)
    assert (second.site, second.site_as_written) == (None, None)
    assert (unknown.site, unknown.site_as_written) == (None, "atlantis")
    assert bad.unreadable is True and bad.site == "quarry"
    for fire, entry in zip(report.fires, [e for e in parsed.entries if not e.ended], strict=True):
        assert fire.still_a_fire == fl.still_a_fire(entry, naive_reference(REFERENCE), 3)
        assert fire.this_machine is False and fire.fail_closed is None
    assert [f.still_a_fire for f in report.fires] == [False, True, False, True, True]
    # The flag follows the configured days.
    assert [f.still_a_fire for f in fires_of(FIRES_TEXT, cfg=config(check_days=9)).fires] == [
        False, False, False, False, True]


@pytest.mark.parametrize("fires_list, reason", [
    (NO_FIRES_LIST, None),
    (fires_list_of("- [ ] 2026-01-18 @atlantis x\n- [?] 2026-01-17 y\n"), "fires list report entries: 2"),
    (fl.FiresList((), "not_read", "fires_list: the fires list does not exist", (), 0, 0),
     "fires_list: the fires list does not exist"),
    (fl.FiresList((), "not_configured", "fires_list is not set", (), 0, 0), "fires_list is not set"),
])
def test_fires_list_status_passes_through(fires_list, reason):
    report = collect_one(fires.FiresListSource(), context(fake_result(), fires_list=fires_list))
    assert by_source(report, "fires_list") == [(None, fires_list.status, reason)]
    assert len(report.sources) == 1


def test_entry_like_line_stops_confirmed_none():
    parsed = fires_list_of("[] 2026-01-14 call back\n")
    report = collect_one(fires.FiresListSource(), context(fake_result(), fires_list=parsed))
    assert by_source(report, "fires_list") == [(None, "read_with_problems", "fires list report entries: 1")]
    assert len(report.fires) == 1 and report.fires[0].unreadable is True
    assert report.all_read is False


# --- the STUCK source -------------------------------------------------------------------

LOCAL_CREATED = datetime(2026, 1, 12, 9, 30)


def marker(step=2, step_name="draft", reason="waiting", created=LOCAL_CREATED, fmt=None):
    if fmt is None:
        fmt = dr.LOCAL_MINUTES if created is not None and created.tzinfo is None else dr.ISO_OFFSET
    return dr.StuckMarker(step, step_name, reason, None, created, fmt if created is not None else dr.UNPARSEABLE, ())


def present(value):
    return dr.Record(dr.PRESENT, value, None)


GONE = dr.Record(dr.ABSENT, None, None)
UNREAD = dr.Record(dr.UNREADABLE, None, "checklist.md is not valid UTF-8 at byte 3")
UNREAD_STUCK = dr.Record(dr.UNREADABLE, None, "STUCK is not valid UTF-8 at byte 3")


def checklist(parked=False):
    step = dr.Step(3, "scope", "P") if parked else None
    # next_step None with all_done False: an active block with no steps (the
    # parked row, if any, sits in an earlier block), so steps is ().
    return present(dr.ChecklistState(None, step, None, False, True, 1, (), ()))


UNPARKED, PARKED = checklist(False), checklist(True)


def folder(name, stuck=None, checklist=GONE):
    """An issue folder's records; `stuck` defaults to a readable marker."""
    return dr.IssueRecords(name, present(marker()) if stuck is None else stuck, checklist, GONE)


def site_records(*folders, skipped=None):
    return dr.SiteRecords(dr.READ, None, {f.issue: f for f in folders}, dict(skipped or {}))


def stuck(result, records, **kw):
    return collect_one(fires.StuckSource(), context(result, records=records, **kw))


def all_sources(result, records, fires_list=NO_FIRES_LIST):
    """The three sources' statuses and fires, by hand: collect_fires is Task 4's."""
    ctx = context(result, fires_list=fires_list, records=records)
    got = [s.collect(ctx) for s in (fires.FiresListSource(), fires.P0Source(), fires.StuckSource())]
    return FireReport(tuple(f for g in got for f in g.fires), tuple(s for g in got for s in g.statuses))


def outcome_table():
    """(result, records): one marker per AC-2 case."""
    from gantry.sites import NO_WBS

    result = fake_result(
        site("harbor", [leaf("Issue-1"), leaf("Issue-2"), leaf("Issue-3"), leaf("Issue-4"), leaf("Issue-5", "parked"),
                        leaf("Issue-6", "done"), leaf("Issue-7", "done"), leaf("Issue-8", "skipped"),
                        leaf("Issue-9", "skipped")]),
        site("bakery", read=False, problem=NO_WBS),
        site("marsh", read=False, reason="WBS.md is not valid UTF-8 at byte 7"),
    )
    records = {
        "harbor": site_records(
            folder("Issue-1", checklist=UNPARKED), folder("Issue-2"), folder("Issue-3", checklist=UNREAD),
            folder("Issue-4", checklist=PARKED), folder("Issue-5", checklist=UNPARKED),
            folder("Issue-6", checklist=UNPARKED), folder("Issue-7", checklist=PARKED),
            folder("Issue-8", checklist=UNPARKED), folder("Issue-9", checklist=PARKED), folder("Issue-50")),
        "bakery": site_records(folder("Issue-1")),
        "marsh": site_records(folder("Issue-1", checklist=UNPARKED)),
    }
    return result, records


def test_stuck_outcomes():
    report = stuck(*outcome_table())
    assert [(f.site, f.issue, f.fail_closed) for f in report.fires] == [
        ("harbor", "Issue-1", None), ("harbor", "Issue-2", None), ("harbor", "Issue-3", "parked_unknown"),
        ("marsh", "Issue-1", "leaf_unknown")]
    for fire in report.fires:
        assert fire.kinds == ("stuck",) and fire.this_machine is True and fire.unreadable is False
    harbor = report.fires[:3]
    assert [f.leaf_key for f in harbor] == ["Issue-1", "Issue-2", "Issue-3"]
    assert [f.why for f in harbor] == [("step 2 draft: waiting",), ("step 2 draft: waiting",),
                                       ("parked unknown: checklist.md could not be read; step 2 draft: waiting",)]
    marsh = report.fires[3]
    assert (marsh.leaf_key, marsh.title) == (None, None)
    assert marsh.why == ("leaf unknown: WBS.md was not read into the build; step 2 draft: waiting",)
    assert report.stuck_parked == {"harbor": ("Issue-4", "Issue-5")}
    assert report.stuck_no_open_leaf == {"harbor": ("Issue-50", "Issue-6", "Issue-7", "Issue-8", "Issue-9"),
                                         "bakery": ("Issue-1",)}
    assert by_source(report, "stuck") == [("harbor", "read_with_problems", "stuck fires not fully read: 1"),
                                          ("bakery", "read", None), ("marsh", "read", None)]


def test_every_marker_has_exactly_one_outcome():
    result, records = outcome_table()
    markers = [(name, f) for name, r in records.items() for f in r.issues if r.issues[f].stuck.status != dr.ABSENT]
    assert len(markers) == 12
    report = stuck(result, records)
    fired = {(f.site, f.issue) for f in report.fires}
    parked = {(s, f) for s, names in report.stuck_parked.items() for f in names}
    no_leaf = {(s, f) for s, names in report.stuck_no_open_leaf.items() for f in names}
    assert len(fired) == len(report.fires)
    assert len(markers) == len(report.fires) + len(parked) + len(no_leaf)
    assert fired.isdisjoint(parked) and fired.isdisjoint(no_leaf) and parked.isdisjoint(no_leaf)
    assert fired | parked | no_leaf == set(markers)


def test_wbs_with_no_node_lines_fails_closed():
    """A WBS.md whose one line is not a node: no_nodes, and devagent's near miss for the line itself."""
    assert FIRES[6] == "* [ ] Fix the crane {issue: Issue-7}"
    data = fires_site(6)
    assert [e.kind for e in data.report] == ["no_nodes", "skipped_line"]
    report = all_sources(fake_result(site("harbor", data=data)), {"harbor": site_records(folder("Issue-7"))})
    assert report.fires == ()
    assert by_source(report, "p0_leaf") == [("harbor", "read_with_problems", "WBS.md lines not read as leaves: 2")]
    assert by_source(report, "stuck") == [
        ("harbor", "read_with_problems", "markers with no open leaf while WBS.md lines were not read: 1")]
    assert report.confirmed_none is False


@pytest.mark.parametrize("line", [7, 8])
def test_stuck_marker_behind_an_unread_line_fails_closed(line):
    assert FIRES[line].endswith("Fix the crane {issue: Issue-7}")
    data = fires_site(1, line)
    assert [e.kind for e in data.report] == ["skipped_line"]
    report = stuck(fake_result(site("harbor", data=data)), {"harbor": site_records(folder("Issue-7"))})
    assert report.stuck_no_open_leaf == {"harbor": ("Issue-7",)}
    assert by_source(report, "stuck") == [
        ("harbor", "read_with_problems", "markers with no open leaf while WBS.md lines were not read: 1")]


def test_unread_line_without_a_stray_marker_keeps_stuck_read():
    assert FIRES[9] == "- [ ]Fix the crane"
    data = fires_site(1, 9)
    report = stuck(fake_result(site("harbor", data=data)), {"harbor": site_records(folder("Issue-1"))})
    assert len(report.fires) == 1 and by_source(report, "stuck") == [("harbor", "read", None)]


def test_stuck_marker_on_a_parent_row_has_no_open_leaf():
    assert FIRES[10:12] == ["- [ ] Rebuild the quay {issue: Issue-5}", "  - [ ] Pour the slab {issue: Issue-6}"]
    data = fires_site(10, 11)
    assert [lf.issue for lf in data.leaves] == ["Issue-6"]
    report = stuck(fake_result(site("harbor", data=data)), {"harbor": site_records(folder("Issue-5"))})
    assert report.fires == () and report.stuck_no_open_leaf == {"harbor": ("Issue-5",)}
    assert by_source(report, "stuck") == [("harbor", "read", None)]


def test_stuck_unreadable_marker():
    result = fake_result(site("harbor", [leaf("Issue-1")]))
    report = stuck(result, {"harbor": site_records(folder("Issue-50", UNREAD_STUCK))})
    assert report.fires == () and report.stuck_no_open_leaf == {"harbor": ("Issue-50",)}
    assert by_source(report, "stuck") == [("harbor", "read", None)]

    report = stuck(result, {"harbor": site_records(folder("Issue-1", UNREAD_STUCK, UNPARKED))})
    assert len(report.fires) == 1
    fire = report.fires[0]
    assert fire.unreadable is True and fire.since is None and fire.age is None
    assert fire.why == ("step and reason unknown: STUCK could not be read",)
    assert by_source(report, "stuck") == [("harbor", "read_with_problems", "stuck fires not fully read: 1")]


@pytest.mark.parametrize("reason", ["", "  \t", None])
def test_stuck_blank_reason(reason):
    result = fake_result(site("harbor", [leaf("Issue-1")]))
    report = stuck(result, {"harbor": site_records(folder("Issue-1", present(marker(reason=reason)), UNPARKED))})
    assert len(report.fires) == 1
    assert report.fires[0].why == ("step 2 draft: reason unknown",)


def test_stuck_unknown_step():
    result = fake_result(site("harbor", [leaf("Issue-1")]))
    unknown = present(marker(step=None, step_name=None))
    report = stuck(result, {"harbor": site_records(folder("Issue-1", unknown, UNPARKED))})
    assert report.fires[0].why == ("step unknown: waiting",)


def test_stuck_vanished_folder():
    result = fake_result(site("harbor", [leaf("Issue-1")]))
    gone = dr.Record(dr.UNREADABLE, None, dr.FOLDER_MISSING)
    vanished = dr.IssueRecords("Issue-1", gone, gone, gone)
    report = stuck(result, {"harbor": site_records(vanished)})
    assert (report.fires, report.stuck_parked, report.stuck_no_open_leaf) == ((), {}, {})
    assert by_source(report, "stuck") == [("harbor", "read", None)]

    refused = dr.Record(dr.UNREADABLE, None, dr.FOLDER_CANNOT_READ.format(error="Access is denied"))
    report = stuck(result, {"harbor": site_records(dr.IssueRecords("Issue-1", refused, refused, refused))})
    assert len(report.fires) == 1 and report.fires[0].unreadable is True
    assert by_source(report, "stuck") == [("harbor", "read_with_problems", "stuck fires not fully read: 1")]


def test_stuck_missing_records_entry():
    result = fake_result(site("harbor", [leaf("Issue-1")]), site("quarry", [leaf("Issue-1")]))
    report = stuck(result, {"quarry": site_records()})
    assert by_source(report, "stuck") == [("harbor", "not_read", "records not read"), ("quarry", "read", None)]


def test_stuck_glyph_alone_is_not_a_fire():
    result = fake_result(site("harbor", [leaf("Issue-1", "stuck")]))
    report = stuck(result, {"harbor": site_records(folder("Issue-2", GONE, UNPARKED))})
    assert (report.fires, report.stuck_parked, report.stuck_no_open_leaf) == ((), {}, {})
    # The control: a marker on the same leaf is a fire.
    assert len(stuck(result, {"harbor": site_records(folder("Issue-1"))}).fires) == 1


def test_stuck_duplicate_issue_joins_first_leaf():
    leaves = [leaf("1.1", "done", issue="Issue-1"), leaf("1.2", "pending", issue="Issue-1")]
    report = stuck(fake_result(site("harbor", leaves)), {"harbor": site_records(folder("Issue-1", checklist=UNPARKED))})
    assert report.fires == () and report.stuck_no_open_leaf == {"harbor": ("Issue-1",)}


def test_stuck_statuses():
    result = fake_result(
        site("harbor", [leaf("Issue-1")]),
        site("b\tad", read=False, problem="name does not match the rule", planning=False),
        site("quarry", read=False, problem="planning directory does not exist"),
        site("orchard", [leaf("Issue-1")]),
        site("marsh", [leaf("Issue-1")]),
    )
    records = {
        "harbor": site_records(folder("Issue-1", present(marker(created=None)), UNPARKED)),
        "quarry": dr.SiteRecords(dr.NOT_READ, dr.PLANNING_MISSING, {}, {}),
        "orchard": dr.SiteRecords(dr.NOT_READ, dr.PLANNING_CANNOT_LIST.format(error="Access is denied"), {}, {}),
        "marsh": site_records(),
    }
    report = stuck(result, records)
    assert by_source(report, "stuck") == [
        ("harbor", "read_with_problems", "stuck fires not fully read: 1"),
        ("b\tad", "not_read", "name does not match the rule"),
        ("quarry", "not_read", dr.PLANNING_MISSING),
        ("orchard", "not_read", "planning directory cannot be listed: Access is denied"),
        ("marsh", "read", None),
    ]
    assert len(report.fires) == 1 and report.fires[0].since is None and report.fires[0].age is None


def test_stuck_skipped_link_folder():
    result = fake_result(site("harbor", [leaf("Issue-1")]))
    control = all_sources(result, {"harbor": site_records()})
    assert len(control.sources) == 3 and control.confirmed_none is True

    report = all_sources(result, {"harbor": site_records(skipped={"Issue-60": dr.SKIPPED_LINK})})
    assert report.fires == ()
    assert by_source(report, "stuck") == [("harbor", "read_with_problems", "linked issue folders not read: 1")]
    assert report.all_read is False and report.confirmed_none is False

    both = all_sources(result, {"harbor": site_records(folder("Issue-1", UNREAD_STUCK, UNPARKED),
                                                       skipped={"Issue-60": dr.SKIPPED_LINK})})
    assert by_source(both, "stuck") == [
        ("harbor", "read_with_problems", "stuck fires not fully read: 1; linked issue folders not read: 1")]
    assert all("Issue-60" not in (s.reason or "") for s in both.sources)


def test_stuck_ages():
    plus_two = timezone(timedelta(hours=2))
    leaves = [leaf("Issue-1"), leaf("Issue-2"), leaf("Issue-3")]
    records = site_records(
        folder("Issue-1", present(marker(created=LOCAL_CREATED)), UNPARKED),
        folder("Issue-2", present(marker(created=datetime(2026, 1, 12, 9, 30, tzinfo=plus_two))), UNPARKED),
        folder("Issue-3", present(marker(created=datetime(2026, 1, 25, 8, 0))), UNPARKED),
    )
    report = stuck(fake_result(site("harbor", leaves)), {"harbor": records})
    local, offset, future = report.fires
    assert local.since == datetime(2026, 1, 12, 9, 30, tzinfo=timezone.utc)
    assert local.age == timedelta(days=7, hours=14, minutes=30)
    assert offset.since == datetime(2026, 1, 12, 9, 30, tzinfo=plus_two)
    assert offset.age == timedelta(days=7, hours=16, minutes=30)
    assert future.age == timedelta(0)
    assert sorted(f.since for f in report.fires) == [offset.since, local.since, future.since]


def test_open_p0_leaf_with_parked_checklist():
    result = fake_result(site("harbor", [leaf("Issue-1", "pending", "P0")]))
    records = {"harbor": site_records(folder("Issue-1", checklist=PARKED))}
    assert [f.kinds for f in p0(result).fires] == [("p0_leaf",)]
    report = stuck(result, records)
    assert report.fires == () and report.stuck_parked == {"harbor": ("Issue-1",)}


# --- collect_fires ------------------------------------------------------------------------


def collect(result, records=None, fires_list=NO_FIRES_LIST, cfg=None, reference=REFERENCE):
    return fires.collect_fires(result, cfg or config(), fires_list, {} if records is None else records, reference)


def label(fire):
    """A fire as (kinds, site, issue or first why)."""
    return fire.kinds, fire.site, fire.issue if fire.issue is not None else fire.why[0]


EVERY_KIND_FIRES = """\
- [ ] no date at all
- [ ] 2026-01-15 @harbor older
- [ ] 2026-01-19 @quarry newest
- [ ] 2026-01-15 tie later in the file
- [?] 2026-01-17 a bad box
- [ ] undated again
- [x] 2026-01-18 resolved
"""


def at(day, hour=9):
    return present(marker(created=datetime(2026, 1, day, hour, 0)))


def every_kind():
    """(result, records, fires_list): every kind of fire, with ties."""
    result = fake_result(
        site("harbor", [leaf("Issue-1", "pending", "P0"), leaf("Issue-2", "in_progress", "P0"), leaf("Issue-4"),
                        leaf("Issue-5"), leaf("Issue-7")]),
        site("quarry", [leaf("Issue-3", "pending", "P0"), leaf("Issue-6"), leaf("Issue-8")]),
    )
    no_date = present(marker(created=None))
    records = {
        "harbor": site_records(folder("Issue-2", at(10), UNPARKED), folder("Issue-4", at(12), UNPARKED),
                               folder("Issue-5", at(14), UNPARKED), folder("Issue-7", no_date, UNPARKED)),
        "quarry": site_records(folder("Issue-6", at(14), UNPARKED), folder("Issue-8", no_date, UNPARKED)),
    }
    return result, records, fires_list_of(EVERY_KIND_FIRES)


def test_order_on_every_kind():
    result, records, listed = every_kind()
    report = collect(result, records, listed)
    assert [label(f) for f in report.fires] == [
        (("fires_list",), None, "- [ ] no date at all"),
        (("fires_list",), None, "- [ ] undated again"),
        (("fires_list",), "quarry", "newest"),
        (("fires_list",), None, "- [?] 2026-01-17 a bad box"),
        (("fires_list",), "harbor", "older"),
        (("fires_list",), None, "tie later in the file"),
        (("p0_leaf",), "harbor", "Issue-1"),
        (("p0_leaf", "stuck"), "harbor", "Issue-2"),
        (("p0_leaf",), "quarry", "Issue-3"),
        (("stuck",), "harbor", "Issue-5"),
        (("stuck",), "quarry", "Issue-6"),
        (("stuck",), "harbor", "Issue-4"),
        (("stuck",), "harbor", "Issue-7"),
        (("stuck",), "quarry", "Issue-8"),
    ]
    assert [s.source for s in report.sources] == ["fires_list", "p0_leaf", "p0_leaf", "stuck", "stuck"]


def test_p0_and_stuck_merge():
    result = fake_result(site("harbor", [leaf("Issue-1", "pending", "P0")]))
    before = collect(result, {"harbor": site_records()})
    assert len(before.fires) == 1 and before.fires[0].kinds == ("p0_leaf",)
    report = collect(result, {"harbor": site_records(folder("Issue-1", at(12), UNPARKED))})
    assert len(report.fires) == 1
    fire = report.fires[0]
    assert fire.kinds == ("p0_leaf", "stuck")
    assert fire.why == ("P0 leaf", "step 2 draft: waiting")
    assert fire.since == datetime(2026, 1, 12, 9, 0, tzinfo=timezone.utc)
    assert fire.age == timedelta(days=7, hours=15)
    assert fire.this_machine is True and fire.leaf_key == "Issue-1" and fire.title == "title Issue-1"


def test_reports_equal_under_listing_order():
    result, records, listed = every_kind()
    flipped = {name: replace(r, issues=dict(reversed(r.issues.items()))) for name, r in reversed(records.items())}
    assert list(flipped) != list(records)
    assert collect(result, records, listed) == collect(result, flipped, listed)


def test_reports_equal_across_clocks_and_zones():
    result, records, _ = every_kind()
    reports = []
    for now in NOWS:
        for local in (EST, IST):
            reference = fire_reference(config(), now, local)
            listed = fl.parse_fires_list(EVERY_KIND_FIRES, ("harbor", "quarry"), naive_reference(reference))
            reports.append(collect(result, records, listed, reference=reference))
    assert len(reports) == 4 and len(reports[0].fires) == 14
    assert all(r == reports[0] for r in reports)


class FakeSource(fires.FireSource):
    name = "fake"

    def __init__(self, raise_after=False):
        self.raise_after = raise_after

    def collect(self, context):
        fire = a_fire(kinds=("fake",), why=("a fake fire",))
        if self.raise_after:
            found = [fire]
            raise RuntimeError(f"failed after {len(found)}")
        return fires.SourceResult((fire,), (SourceStatus("fake", None, "read", None),))


def test_fake_source_appended(monkeypatch):
    monkeypatch.setattr(fires, "SOURCES", (*fires.SOURCES, FakeSource()))
    result, records, listed = every_kind()
    report = collect(result, records, listed)
    assert len(report.fires) == 15
    assert [label(f) for f in report.fires][-2:] == [(("stuck",), "quarry", "Issue-8"), (("fake",), None, "a fake fire")]
    assert report.sources[-1] == SourceStatus("fake", None, "read", None)


def test_source_that_raises_as_a_whole(monkeypatch):
    monkeypatch.setattr(fires, "SOURCES", (*fires.SOURCES, FakeSource(raise_after=True)))
    result, records, listed = every_kind()
    report = collect(result, records, listed)
    assert len(report.fires) == 14
    assert all(f.kinds != ("fake",) for f in report.fires)
    assert report.sources[-1] == SourceStatus("fake", None, "not_read", "source failed: RuntimeError")


class ReturnsSource(fires.FireSource):
    def __init__(self, name, returns):
        self.name, self.returns = name, returns

    def collect(self, context):
        return self.returns


def test_duplicate_source_name_keeps_every_fire(monkeypatch):
    empty = fires.SourceResult((), (SourceStatus("stuck", None, "read", None),))
    monkeypatch.setattr(fires, "SOURCES", (*fires.SOURCES, ReturnsSource("stuck", empty)))
    result, records, listed = every_kind()
    report = collect(result, records, listed)
    assert len(report.fires) == 14
    assert sum("stuck" in f.kinds for f in report.fires) > 0


def test_malformed_source_result_is_isolated(monkeypatch):
    monkeypatch.setattr(fires, "SOURCES", (*fires.SOURCES, ReturnsSource("fake", None)))
    result, records, listed = every_kind()
    report = collect(result, records, listed)
    assert len(report.fires) == 14
    assert report.sources[-1] == SourceStatus("fake", None, "not_read", "source failed: AttributeError")


def test_source_with_no_status_fails_closed(monkeypatch):
    monkeypatch.setattr(fires, "SOURCES", (*fires.SOURCES, ReturnsSource("fake", fires.SourceResult((), ()))))
    result, records, listed = every_kind()
    report = collect(result, records, listed)
    assert report.sources[-1] == SourceStatus("fake", None, "not_read", "source gave no status")
    assert not report.all_read


class Boom:
    @property
    def status(self):
        raise RuntimeError("no status")


def test_stuck_site_that_raises():
    result, records, listed = every_kind()
    report = collect(result, {**records, "harbor": Boom()}, listed)
    assert by_source(report, "stuck") == [("harbor", "not_read", "source failed: RuntimeError"),
                                          ("quarry", "read_with_problems", "stuck fires not fully read: 1")]
    assert [label(f) for f in report.fires if f.site == "harbor" and "stuck" in f.kinds] == []
    assert [f.issue for f in report.fires if f.kinds == ("p0_leaf",)] == ["Issue-1", "Issue-2", "Issue-3"]
    assert [f.issue for f in report.fires if f.kinds == ("stuck",)] == ["Issue-6", "Issue-8"]


def test_by_site():
    result, records, listed = every_kind()
    report = collect(result, records, fires_list_of(EVERY_KIND_FIRES + "- [ ] 2026-01-16 @atlantis far away\n"))
    groups = report.by_site()
    assert list(groups) == [None, "quarry", "harbor"]
    assert [label(f)[2] for f in groups[None]] == ["- [ ] no date at all", "- [ ] undated again", "- [?] 2026-01-17 a bad box",
                                                   "far away", "tie later in the file"]
    assert groups[None][3].site_as_written == "atlantis"
    assert [f.issue for f in groups["harbor"] if f.issue] == ["Issue-1", "Issue-2", "Issue-5", "Issue-4", "Issue-7"]
    assert sum(len(g) for g in groups.values()) == len(report.fires) == 15


@pytest.mark.parametrize("hostile", HOSTILE)
def test_hostile_text_is_kept_verbatim(hostile):
    listed = fires_list_of(f"- [ ] 2026-01-15 {hostile}\n- [?] 2026-01-15 {hostile}\n")
    result = fake_result(site("harbor", [leaf("Issue-1", "pending", "P0", title=hostile),
                                         leaf("Issue-2", issue=hostile)]))
    records = {"harbor": site_records(folder("Issue-1", present(marker(reason=hostile)), UNPARKED),
                                      folder(hostile, present(marker(step_name=hostile)), UNPARKED))}
    report = collect(result, records, listed)
    reason_fire, raw_fire, p0_fire, folder_fire = report.fires
    assert hostile in reason_fire.why[0] and hostile in raw_fire.why[0]
    assert p0_fire.title == hostile and hostile in p0_fire.why[1]
    assert folder_fire.issue == hostile and hostile in folder_fire.why[0]


def test_confirmed_none():
    from gantry.sites import NO_WBS

    clean = fake_result(site("harbor", [leaf("Issue-1")]), site("bakery", read=False, problem=NO_WBS))
    records = {"harbor": site_records(), "bakery": site_records()}
    assert collect(clean, records).confirmed_none is True
    burning = fake_result(site("harbor", [leaf("Issue-1", "pending", "P0")]), site("bakery", read=False, problem=NO_WBS))
    positive = collect(burning, records)
    assert positive.all_read is True and positive.confirmed_none is False
    for listed in (fl.FiresList((), "not_configured", "fires_list is not set", (), 0, 0),
                   fl.FiresList((), "not_read", "fires_list: the fires list does not exist", (), 0, 0),
                   fires_list_of("- [ ] 2026-01-18 @atlantis x\n")):
        assert collect(clean, records, listed).confirmed_none is False
    marsh = fake_result(site("harbor", [leaf("Issue-1")]), site("marsh", read=False, reason="not UTF-8"))
    assert collect(marsh, {"harbor": site_records(), "marsh": site_records()}).confirmed_none is False
    missing = fake_result(site("harbor", [leaf("Issue-1")]),
                          site("quarry", read=False, problem="planning directory does not exist"))
    quarry = dr.SiteRecords(dr.NOT_READ, dr.PLANNING_MISSING, {}, {})
    assert collect(missing, {"harbor": site_records(), "quarry": quarry}).confirmed_none is False


def test_collect_refuses_naive_reference():
    with pytest.raises(ValueError):
        collect(fake_result(), reference=datetime(2026, 1, 20))


@pytest.fixture
def every_report():
    """Every report this module's case builders make."""
    reports = [collect(*outcome_table()), collect(*every_kind())]
    for hostile in HOSTILE[:3]:
        listed = fires_list_of(f"- [?] 2026-01-15 {hostile}\n")
        result = fake_result(site("harbor", [leaf("Issue-1", "pending", "P0", title=hostile)]))
        reports.append(collect(result, {"harbor": site_records(folder("Issue-1", UNREAD_STUCK, UNREAD))}, listed))
    return reports


def test_fire_invariants(every_report):
    every = [f for r in every_report for f in r.fires]
    assert len(every) > 20
    for fire in every:
        assert len(fire.kinds) == len(fire.why)
        assert list(fire.kinds) == [k for k in KINDS if k in fire.kinds]
        assert fire.this_machine == ("stuck" in fire.kinds)
        assert fire.fail_closed is None or ("stuck" in fire.kinds and fire.fail_closed in FAIL_CLOSED)
        assert (fire.age is None) == (fire.since is None)
        assert fire.since is None or fire.since.utcoffset() is not None
        assert (fire.still_a_fire is None) == ("fires_list" not in fire.kinds)


# --- the samples ------------------------------------------------------------------------


def test_sample_quarry_records_read_clean():
    from gantry.config import load_config
    from gantry.sites import config_roots, load_sites
    from test_samples import GANTRY_SAMPLE

    cfg = load_config(GANTRY_SAMPLE)
    roots = config_roots(cfg)
    quarry = next(s for s in load_sites(cfg, roots)[0] if s.name == "quarry")
    got = dr.read_site_records(roots, quarry.planning_dir, site="quarry")
    assert (got.status, got.skipped) == ("read", {})
    assert sorted(got.issues) == ["Issue-20", "Issue-3", "Issue-5", "Issue-9"]
    # Issue-3 and Issue-5 (#173) hold a checklist only.
    assert {name for name, r in got.issues.items() if r.stuck.status == "present"} == {"Issue-20", "Issue-9"}
    formats = []
    for name, records in got.issues.items():
        if name in ("Issue-3", "Issue-5"):
            assert records.stuck.status == "absent" and records.checklist.value.problems == (), name
            continue
        assert records.stuck.status == "present" and records.stuck.value.problems == (), name
        assert records.checklist.status == "present" and records.checklist.value.problems == (), name
        assert records.stuck.value.created.date() < cfg.as_of
        formats.append(records.stuck.value.created_format)
    assert sorted(formats) == [dr.ISO_OFFSET, dr.LOCAL_MINUTES]
    assert got.issues["Issue-20"].stuck.value.created_format == dr.LOCAL_MINUTES
    assert got.issues["Issue-9"].checklist.value.parked is True
    assert got.issues["Issue-20"].checklist.value.parked is False


# --- the doc ------------------------------------------------------------------------------


def test_doc_names_every_token():
    from gantry.cli import FIRES_LINE
    from test_samples import ROOT

    text = (ROOT / "docs" / "daily-screen.md").read_bytes().decode("utf-8")
    tokens = [*STATUSES, *KINDS, *FAIL_CLOSED, "stuck_parked", "stuck_no_open_leaf", "this_machine",
              FIRES_LINE.format(n="N", m="M")]
    assert len(tokens) == 14
    assert [t for t in tokens if f"`{t}`" not in text] == []
    assert "the-page" not in [line[3:].lower().replace(" ", "-") for line in text.splitlines()
                              if line.startswith("## ")]


# Lines that hide a P0 while every status reads `read` (#12, at the pin's devagent): devagent's parser
# neither reads their priority nor reports a kind the fires count (analysis/fires-shape-grid.md in the
# issue's notes). The doc names them as examples, not as a complete list. fires.md's line for each.
BLIND_P0_LINES = {12: "- [ ] Fix the crane {priority: P0} today", 13: "- [ ] Fix the crane {Priority: P0}",
                  15: "1. [ ] Fix the crane {priority: P0}", 16: "- [ ] Fix the crane {priority: P0"}
# The three that moved from fail-closed (the stopgap reported them) to blind: indented with no-break
# spaces, which devagent's near-miss rule does not count as indentation. The doc names them in words.
NBSP_LINES = (17, 18, 19)


def _blind(line):
    report = collect(fake_result(site("harbor", data=fires_site(1, line))), {"harbor": site_records()})
    return (report.fires, report.confirmed_none) == ((), True)


@pytest.mark.parametrize("line", list(BLIND_P0_LINES))
def test_doc_p0_blind_shapes_are_examples(line):
    from test_samples import ROOT

    assert FIRES[line] == BLIND_P0_LINES[line]
    assert _blind(line)
    text = " ".join((ROOT / "docs" / "daily-screen.md").read_bytes().decode("utf-8").split())
    assert f"`{BLIND_P0_LINES[line]}`" in text
    assert "One P0 shape is still not seen" not in text


@pytest.mark.parametrize("line", NBSP_LINES)
def test_no_break_space_indent_hides_a_p0(line):
    from test_samples import ROOT

    assert FIRES[line].startswith("\u00a0\u00a0- [") and "P0" in FIRES[line]
    assert _blind(line)
    text = " ".join((ROOT / "docs" / "daily-screen.md").read_bytes().decode("utf-8").split())
    assert "indented with no-break spaces" in text
