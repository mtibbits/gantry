"""The lane scheduler: gantry/schedule.py (#9)."""

import ast
import copy
import math
import sys

import pytest

from gantry import schedule
from gantry.schedule import chain_hours, limit, plan
from test_samples import ROOT


def it(key, hours, preds=(), eff=3, ix=0):
    return {"key": key, "hours": hours, "preds": list(preds), "eff_priority": eff, "wbs_index": ix}


def test_priority_beats_wbs_order():
    p = plan([it("a", 6, eff=3, ix=0), it("b", 4, eff=1, ix=5)], 1)
    assert p["b"] == (0.0, 4.0, 0)
    assert p["a"] == (4.0, 10.0, 0)


def test_wbs_order_breaks_ties():
    p = plan([it("late", 6, ix=4), it("early", 6, ix=1)], 1)
    assert p["early"][0] == 0.0
    assert p["late"][0] == 6.0


def test_input_order_breaks_full_ties():
    p = plan([it("first", 6, ix=1), it("second", 6, ix=1)], 1)
    assert p["first"][0] == 0.0


def test_waits_for_every_predecessor():
    p = plan([it("a", 6, ix=0), it("b", 10, ix=1), it("c", 2, preds=["a", "b"], ix=2)], 2)
    assert p["a"] == (0.0, 6.0, 0)
    assert p["b"] == (0.0, 10.0, 1)
    assert p["c"] == (10.0, 12.0, 0)


def test_free_lanes_fill_lowest_first():
    p = plan([it("x", 6, eff=3, ix=0), it("y", 6, eff=1, ix=1), it("z", 6, eff=2, ix=2)], 3)
    assert {k: v[2] for k, v in p.items()} == {"y": 0, "z": 1, "x": 2}


def chain_set():
    return [it("a", 6, ix=0), it("b", 6, ["a"], ix=1), it("c", 6, ["b"], ix=2),
            it("d", 6, ix=3), it("e", 6, ix=4)]


def test_limit_names_chain_or_capacity():
    items = chain_set()
    assert chain_hours(items) == 18.0
    assert max(v[1] for v in plan(items, 3).values()) == 18.0
    assert limit(items, 3) == "chain"
    assert max(v[1] for v in plan(items, 1).values()) == 30.0
    assert limit(items, 1) == "capacity"


def test_chain_hours_counts_unlinked_items():
    items = [it("a", 1, ix=0), it("b", 1, ["a"], ix=1), it("c", 5, ix=2)]
    assert chain_hours(items) == 5.0


REFUSALS = [
    ([it("a", 1)], 0, "plan: lanes must be a whole number from 1"),
    ([it("a", 1)], True, "plan: lanes must be a whole number from 1"),
    ([it("a", 1)], 2.0, "plan: lanes must be a whole number from 1"),
    ([it("a", 1), it("a", 2)], 1, "plan: duplicate key 'a'"),
    ([it("a", 1, ["z"])], 1, "plan: 'a' waits on 'z', which is not an item"),
    ([it("a", 0)], 1, "plan: 'a' has hours 0; hours must be a finite number above 0"),
    ([it("a", -1.5)], 1, "plan: 'a' has hours -1.5; hours must be a finite number above 0"),
    ([it("a", math.inf)], 1, "plan: 'a' has hours inf; hours must be a finite number above 0"),
    ([it("a", math.nan)], 1, "plan: 'a' has hours nan; hours must be a finite number above 0"),
    ([it("a", "3")], 1, "plan: 'a' has hours '3'; hours must be a finite number above 0"),
    ([it("a", True)], 1, "plan: 'a' has hours True; hours must be a finite number above 0"),
    ([it("a", 1, ["b"]), it("b", 1, ["a"])], 2, "plan: no item can start; the waits hold a cycle"),
    ([it("a", 1, ["a"])], 1, "plan: no item can start; the waits hold a cycle"),
]


@pytest.mark.parametrize(("items", "lanes", "message"), REFUSALS)
def test_plan_refusals(items, lanes, message):
    with pytest.raises(ValueError) as exc:
        plan(items, lanes)
    assert str(exc.value) == message


def test_fractional_hours_are_exact():
    p = plan([it("a", 0.1), it("b", 0.2, ["a"], ix=1)], 1)
    assert p["b"] == (0.1, 0.1 + 0.2, 0)


def test_plan_is_pure():
    items = chain_set()
    before = copy.deepcopy(items)
    first = plan(items, 2)
    assert items == before
    assert plan(items, 2) == first
    tree = ast.parse((ROOT / "gantry" / "schedule.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            imported.add(node.module.split(".")[0])
    assert imported <= sys.stdlib_module_names, imported
    assert schedule.__name__ == "gantry.schedule"


def test_pick_ready_most_urgent_first():
    urgent = it("urgent", 6, eff=1, ix=5)
    assert schedule.pick_ready([it("early", 6, eff=3, ix=0), urgent]) is urgent


def test_pick_ready_wbs_order_breaks_ties():
    early = it("early", 6, ix=1)
    assert schedule.pick_ready([it("late", 6, ix=4), early]) is early


def test_pick_ready_input_order_breaks_full_ties():
    first = it("first", 6, ix=1)
    assert schedule.pick_ready([first, it("second", 6, ix=1)]) is first


def test_plan_picks_through_pick_ready(monkeypatch):
    # plan() holds no second copy of the rule: it follows whatever pick_ready says.
    items = [it("a", 1, eff=1, ix=0), it("b", 1, eff=2, ix=1), it("c", 1, eff=3, ix=2)]
    assert list(plan(items, 1)) == ["a", "b", "c"]
    monkeypatch.setattr(schedule, "pick_ready", lambda ready: ready[-1])
    assert list(plan(items, 1)) == ["c", "b", "a"]


def test_repeated_picks_equal_plan_start_order():
    items = [it("a", 2, eff=3, ix=0), it("b", 5, eff=1, ix=1), it("c", 1, eff=3, ix=2),
             it("d", 3, eff=0, ix=3), it("e", 4, eff=1, ix=4), it("f", 6, eff=3, ix=0)]
    left = list(items)
    order = []
    while left:
        pick = schedule.pick_ready(left)
        order.append(pick["key"])
        left.remove(pick)
    assert order == list(plan(items, 1))
