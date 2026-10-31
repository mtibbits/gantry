"""Priority: the rank of a priority: value and effective priority (#8)."""

import pytest

from gantry.priority import effective, is_valid, rank
from gantry.wbs_stopgap import parse_wbs
from test_samples import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "wbs"


def parse(name):
    return parse_wbs((FIXTURES / f"{name}.md").read_bytes().decode("utf-8"), "alpha")


def schedulable(data):
    return [leaf for leaf in data.leaves if leaf.open and leaf.key not in data.held_out]

RANK_TABLE = [
    ("P1", 1), ("P2", 2), ("P3", 3), ("P4", 4),
    (None, 3), ("P0", 3), ("P5", 3), ("P7", 3), ("p1", 3), ("[P1]", 3),
    ("P" + "9" * 5000, 3),
    ('"P2"', 2), ("[P1", 1),
]
INVALID = {None, "P0", "P5", "P7", "p1", "[P1]", "P" + "9" * 5000}


@pytest.mark.parametrize(("raw", "expected"), RANK_TABLE)
def test_rank_scale(raw, expected):
    assert rank(raw) == expected
    assert is_valid(raw) is (raw not in INVALID)


# --- effective priority ------------------------------------------------------


def test_effective_direct_and_transitive():
    data = parse("priority")
    assert data.report == ()
    assert data.links == (("Issue-1", "Issue-2"), ("Issue-2", "Issue-3"))
    eff = effective(schedulable(data), data.links)
    assert (eff["Issue-2"], eff["Issue-1"]) == (1, 1)


def test_effective_not_through_finished_leaf():
    data = parse("priority")
    assert effective(schedulable(data), data.links)["Issue-4"] == 3


def test_effective_ignores_foreign_entry():
    data = parse("priority")
    eff = effective(schedulable(data), data.links)
    assert (eff["Issue-7"], eff["Issue-8"]) == (3, 1)


def test_row_priority_not_inherited_and_reported():
    data = parse("rows")
    eff = effective(schedulable(data), data.links)
    assert (eff["Issue-2"], eff["Issue-11"], eff["Issue-12"]) == (3, 3, 3)
    found = [e for e in data.report if e.kind == "bad_priority"]
    assert [e.line for e in found] == [5]
    assert "'high'" in found[0].message


def test_effective_rejects_cycle():
    data = parse("cycle")
    with pytest.raises(ValueError):
        effective([leaf for leaf in data.leaves if leaf.open], data.links)
