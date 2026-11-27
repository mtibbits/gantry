"""Priority: the rank of a priority: value and effective priority (#8)."""

import pytest

import helpers_portfolio as hp
from gantry.priority import effective, is_valid, rank
from gantry.schedule import plan


def parse(name):
    return hp.fixture_site(name)


def schedulable(data):
    return [leaf for leaf in data.leaves if leaf.open and leaf.key not in data.held_out]

RANK_TABLE = [
    ("P0", 0), ("P1", 1), ("P2", 2), ("P3", 3), ("P4", 4),
    (None, 3), ("P5", 3), ("P-1", 3), ("P7", 3), ("p1", 3), ("[P1]", 3),
    ("P" + "9" * 5000, 3),
    # devagent has already read quotes and brackets: a stored value holding them is that text (#12).
    ('"P2"', 3), ("[P1", 3),
]
INVALID = {None, "P5", "P-1", "P7", "p1", "[P1]", "P" + "9" * 5000, '"P2"', "[P1"}


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
# P0_TEXT as devagent reads it (#12: the build reads nothing else).
P0_NODES = (
    hp.node("First", issue="Issue-1", priority="P1"),
    hp.node("Second", issue="Issue-2", priority="P0"),
    hp.node("Base", issue="Issue-3", priority="P3"),
    hp.node("Top", issue="Issue-4", priority="P0", depends_on=["Issue-3"]),
    hp.node("Odd", issue="Issue-5", priority="P5"),
    hp.node("Odder", issue="Issue-6", priority="P-1"),
)


def test_p0_sorts_before_p1_in_schedule():
    data = hp.site(*P0_NODES)
    work = schedulable(data)
    eff = effective(work, data.links)
    preds = {leaf.key: [a for a, b in data.links if b == leaf.key] for leaf in work}
    items = [{"key": leaf.key, "hours": 6, "preds": preds[leaf.key], "eff_priority": eff[leaf.key],
              "wbs_index": i} for i, leaf in enumerate(work)]
    p = plan(items, 1)
    assert p["Issue-2"][0] == 0.0
    assert p["Issue-1"][0] > 0.0


def test_p0_inherited_by_prerequisite():
    data = hp.site(*P0_NODES)
    assert effective(schedulable(data), data.links)["Issue-3"] == 0


def test_p0_not_reported_p5_p_minus_1_are():
    data = hp.site(*P0_NODES)
    found = [e for e in data.report if e.kind == "bad_priority"]
    assert [e.line for e in found] == [5, 6]
    assert "'P5'" in found[0].message and "'P-1'" in found[1].message
    assert "is not P0, P1, P2, P3 or P4" in found[0].message


def test_p0_keys_are_effective_p0_in_order():
    from gantry.priority import p0_keys

    data = hp.site(hp.node("Epic", hp.node("First", issue="Issue-1"), hp.node("Waited on", issue="Issue-2"),
                           hp.node("Urgent", issue="Issue-3", priority="P0", depends_on=["Issue-2"])))
    work = schedulable(data)
    eff = effective(work, data.links)
    assert p0_keys(work, eff) == ["Issue-2", "Issue-3"]
    assert p0_keys(list(reversed(work)), eff) == ["Issue-3", "Issue-2"]
    calm = [leaf for leaf in work if leaf.key == "Issue-1"]
    assert p0_keys(calm, effective(calm, [])) == []
