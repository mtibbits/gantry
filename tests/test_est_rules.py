"""The est: classes, a stopgap for mtibbits/devagent#737 (#10)."""

import pytest

from gantry.est_rules import classify_est
from gantry.model import DAYS_PER_WEEK, HOURS_PER_DAY


@pytest.mark.parametrize(("raw", "hours"), [("3h", 3), ("1.5d", 9), ("2w", 60), ('"3h"', 3)])
def test_time_values(raw, hours):
    v = classify_est(raw)
    assert v.kind == "time"
    assert v.lo_h == v.hi_h == hours


def test_time_units_follow_the_constants():
    assert classify_est("1d").lo_h == HOURS_PER_DAY
    assert classify_est("1w").lo_h == HOURS_PER_DAY * DAYS_PER_WEEK


@pytest.mark.parametrize("raw", ["?", "?w"])
def test_placeholders(raw):
    v = classify_est(raw)
    assert v.kind == "placeholder"
    assert (v.lo_h, v.hi_h) == (None, None)


@pytest.mark.parametrize(("raw", "number", "unit"),
                         [("48pr", 48, "pr"), ("2.5pr", 2.5, "pr"), ("5pt", 5, "pt"), ("3PR", 3, "PR")])
def test_non_time(raw, number, unit):
    v = classify_est(raw)
    assert v.kind == "non_time"
    assert (v.number, v.unit) == (number, unit)
    assert (v.lo_h, v.hi_h) == (None, None)


@pytest.mark.parametrize("raw", ["?3d", "2-3d", "0h", "3H", "48p<r", '"about two days"', "3 h", "[3h]", "",
                                 "9" * 5000 + "h", "0pr", "1.5W"])
def test_unparseable(raw):
    v = classify_est(raw)
    assert v.kind == "unparseable"
    assert (v.lo_h, v.hi_h) == (None, None)


def test_absent():
    assert classify_est(None).kind == "absent"
