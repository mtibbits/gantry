"""The fire collector (#32): one ordered FireReport per full build, a read
status for every source and the two named STUCK counts.

Inputs are built in memory: a hostile folder name cannot exist on a Windows
disk, and the collector reads only what build_all hands it.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

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
                  still_a_fire=False, this_machine=False)
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


@pytest.mark.parametrize("raw", ["P0", '"P0"', "p0", "P00", "[P0]", None])
def test_p0_goes_through_priority_rule(raw):
    from gantry import priority

    report = p0(fake_result(site("harbor", [leaf("Issue-1", "pending", raw)])))
    assert len(report.fires) == (1 if priority.rank(raw) == 0 else 0)
    if raw in ("P0", '"P0"'):
        assert len(report.fires) == 1


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


@pytest.mark.parametrize("line", ["- [ ] Fix the crane {priority: p0}", "- [ ] Fix the crane {priority: PO}",
                                  "- [ ]Fix the crane {priority: P0}", "- [X] Fix the crane {priority: P0}"])
def test_p0_report_problems_fail_closed(line):
    from gantry.wbs_stopgap import parse_wbs

    clean = parse_wbs("- [ ] Pour the slab {issue: Issue-1}\n", "harbor")
    control = p0(fake_result(site("harbor", data=clean)))
    assert by_source(control, "p0_leaf") == [("harbor", "read", None)]
    assert control.confirmed_none is True

    data = parse_wbs(f"- [ ] Pour the slab {{issue: Issue-1}}\n{line}\n", "harbor")
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
    return present(dr.ChecklistState(None, step, None, False, True, 1, ()))


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
    assert marsh.why == ("leaf unknown: WBS.md could not be read; step 2 draft: waiting",)
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
