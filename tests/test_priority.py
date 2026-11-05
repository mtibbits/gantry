"""Priority: the rank of a priority: value and effective priority (#8)."""

import pytest

from gantry.priority import effective, is_valid, rank
from gantry.schedule import plan
from gantry.wbs_stopgap import parse_wbs
from test_samples import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "wbs"


def parse(name):
    return parse_wbs((FIXTURES / f"{name}.md").read_bytes().decode("utf-8"), "alpha")


def schedulable(data):
    return [leaf for leaf in data.leaves if leaf.open and leaf.key not in data.held_out]

RANK_TABLE = [
    ("P0", 0), ("P1", 1), ("P2", 2), ("P3", 3), ("P4", 4),
    (None, 3), ("P5", 3), ("P-1", 3), ("P7", 3), ("p1", 3), ("[P1]", 3),
    ("P" + "9" * 5000, 3),
    ('"P2"', 2), ("[P1", 1),
]
INVALID = {None, "P5", "P-1", "P7", "p1", "[P1]", "P" + "9" * 5000}


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


# --- P0 ----------------------------------------------------------------------

P0_TEXT = """- [ ] First {issue: Issue-1, priority: P1}
- [ ] Second {issue: Issue-2, priority: P0}
- [ ] Base {issue: Issue-3, priority: P3}
- [ ] Top {issue: Issue-4, priority: P0, depends_on: [Issue-3]}
- [ ] Odd {issue: Issue-5, priority: P5}
- [ ] Odder {issue: Issue-6, priority: P-1}
"""


def test_p0_sorts_before_p1_in_schedule():
    data = parse_wbs(P0_TEXT, "alpha")
    work = schedulable(data)
    eff = effective(work, data.links)
    preds = {leaf.key: [a for a, b in data.links if b == leaf.key] for leaf in work}
    items = [{"key": leaf.key, "hours": 6, "preds": preds[leaf.key], "eff_priority": eff[leaf.key],
              "wbs_index": i} for i, leaf in enumerate(work)]
    p = plan(items, 1)
    assert p["Issue-2"][0] == 0.0
    assert p["Issue-1"][0] > 0.0


def test_p0_inherited_by_prerequisite():
    data = parse_wbs(P0_TEXT, "alpha")
    assert effective(schedulable(data), data.links)["Issue-3"] == 0


def test_p0_not_reported_p5_p_minus_1_are():
    data = parse_wbs(P0_TEXT, "alpha")
    found = [e for e in data.report if e.kind == "bad_priority"]
    assert [e.line for e in found] == [5, 6]
    assert "'P5'" in found[0].message and "'P-1'" in found[1].message
    assert "is not P0, P1, P2, P3 or P4" in found[0].message
