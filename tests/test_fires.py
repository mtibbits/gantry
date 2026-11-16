"""The fire collector (#32): one ordered FireReport per full build, a read
status for every source and the two named STUCK counts.

Inputs are built in memory: a hostile folder name cannot exist on a Windows
disk, and the collector reads only what build_all hands it.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

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
