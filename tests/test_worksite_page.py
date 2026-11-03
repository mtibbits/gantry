"""The worksite page: its model, its rendering and its script (#9).

The model tests need no Node; the page-script tests take the `node` fixture.
Every literal is hand-derived from tests/fixtures/wbs/.
"""

import dataclasses
import math
from types import SimpleNamespace

import pytest

from gantry.views.worksite import (
    INTERIM_NOTE,
    NO_TIERS,
    page_model,
)
from gantry.schedule import limit, plan
from gantry.wbs_stopgap import parse_wbs
from test_samples import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "wbs"


def fixture(name):
    return parse_wbs((FIXTURES / f"{name}.md").read_bytes().decode("utf-8"), "alpha")


def model(name="page", unit=24, data=None):
    return page_model("alpha", fixture(name) if data is None else data, default_unit_hours=unit)


def planned(m, lanes):
    return plan(m["schedule"]["items"], lanes)


def by_key(m):
    return {leaf["key"]: leaf for leaf in m["leaves"]}


def with_leaf(data, key, **changes):
    leaves = tuple(dataclasses.replace(leaf, **changes) if leaf.key == key else leaf for leaf in data.leaves)
    return dataclasses.replace(data, leaves=leaves)


# --- the page model ------------------------------------------------------------


def test_page_fixture_plan_at_4_lanes():
    m = model()
    assert planned(m, 4) == {
        "Issue-2": (0, 24, 0), "Issue-11": (0, 24, 1), "2.6": (0, 24, 2), "Issue-15": (0, 24, 3),
        "Issue-3": (24, 48, 0), "Issue-12": (24, 48, 1), "Issue-20": (24, 48, 2),
        "Issue-4": (48, 72, 0), "Issue-13": (72, 96, 0), "Issue-5": (72, 96, 1),
        "Issue-7": (96, 120, 0), "Issue-9": (96, 120, 1), "Issue-14": (96, 120, 2),
        "1.7": (120, 144, 0),
    }
    assert limit(m["schedule"]["items"], 4) == "chain"
    assert limit(m["schedule"]["items"], 1) == "capacity"


def test_rolling_wave_set():
    leaves = by_key(model())
    boxes = {k for k, leaf in leaves.items() if leaf["linked"] and leaf["wave_full"]}
    circles = {k for k, leaf in leaves.items() if leaf["linked"] and not leaf["wave_full"]}
    assert boxes == {"Issue-2", "Issue-3", "Issue-4", "Issue-5", "Issue-9", "1.7",
                     "Issue-7", "Issue-14", "Issue-11", "Issue-12"}
    assert circles == {"Issue-13"}
    # Each rule on its own: slack, own P1, ~ and the horizon.
    assert all(leaves[k]["slack"] == 0 for k in ("Issue-5", "Issue-9", "1.7"))
    plan4 = planned(model(), 4)
    assert all(plan4[k][0] >= 60 for k in ("Issue-5", "Issue-9", "1.7", "Issue-7", "Issue-14"))
    assert leaves["Issue-7"]["priority"] == "P1" and leaves["Issue-7"]["slack"] == 1
    assert leaves["Issue-14"]["state"] == "in_progress" and leaves["Issue-14"]["slack"] == 1
    assert leaves["Issue-11"]["slack"] == 4 and plan4["Issue-12"][0] < 60
    # Issue-13's P1 is effective (from Issue-7), not its own.
    assert leaves["Issue-13"]["eff"] == 1 and leaves["Issue-13"]["priority"] is None


def test_no_epic_lane():
    m = model()
    assert [lane["key"] for lane in m["lanes"]] == ["Issue-1", "Issue-10", None]
    assert [lane["name"] for lane in m["lanes"]] == ["Ground works", "Frame", "no epic"]
    assert by_key(m)["Issue-20"]["lane"] == 2


def test_panel_lines():
    assert model()["panel"] == [
        "2 leaves have no issue: id and are named by outline position",
        "WBS.md line 15: depends_on entry 'Issue-99' names no issue: id in this file",
    ]


def test_foreign_entries():
    leaves = by_key(model())
    assert leaves["Issue-15"]["foreign"] == ["beta:Issue-3 (on another site)", "gamma:Issue-1 (on another site)"]
    assert leaves["Issue-11"]["foreign"] == ["gamma:Issue-1 (on another site)"]
    ground = [leaf for leaf in leaves.values() if leaf["lane"] == 0]
    assert len(ground) == 6
    assert all(leaf["foreign"] == [] for leaf in ground)


def test_stuck_blocked_parked_are_open_work():
    m = model("states", unit=6)
    leaves = by_key(m)
    assert {k: leaves[k]["sc"] for k in ("Issue-2", "Issue-4", "Issue-6")} == {
        "Issue-2": "st-stuck", "Issue-4": "st-blocked", "Issue-6": "st-parked"}
    assert all(leaves[k]["linked"] for k in ("Issue-2", "Issue-4", "Issue-6"))
    assert {i["key"] for i in m["schedule"]["items"]} >= {"Issue-2", "Issue-4", "Issue-6"}
    assert planned(m, 4) == {
        "Issue-1": (0, 6, 0), "Issue-4": (0, 6, 1), "Issue-6": (0, 6, 2),
        "Issue-2": (6, 12, 0), "Issue-5": (6, 12, 1), "Issue-7": (6, 12, 2),
        "Issue-3": (12, 18, 0),
    }
    assert m["notes"]["stuck"] == "3 stuck, blocked or parked leaves are scheduled as if an agent could start them"
    assert model()["notes"]["stuck"] is None
    one = model("states", unit=6, data=parse_wbs("- [!] Alone {issue: Issue-1}\n", "alpha"))
    assert one["notes"]["stuck"] == "1 stuck, blocked or parked leaf is scheduled as if an agent could start it"


def test_cycle_fixture():
    m = model("cycle")
    assert m["network"]["nodes"] == ["Issue-4", "Issue-5"]
    assert {i["key"] for i in m["schedule"]["items"]} == {"Issue-4", "Issue-5", "Issue-6", "Issue-9"}
    assert m["panel"] == [
        "WBS.md line 2: dependency cycle among 'Issue-1', 'Issue-2'",
        "WBS.md line 2: 'Issue-1' waits on a cycle",
        "WBS.md line 3: 'Issue-2' waits on a cycle",
        "WBS.md line 4: 'Issue-3' waits on a cycle",
    ]
    for lanes in range(1, 9):
        assert len(planned(m, lanes)) == 4


def test_default_unit_hours_drive_durations():
    from test_samples import SAMPLES
    from sample_roles import UNESTIMATED

    data = parse_wbs((SAMPLES / UNESTIMATED / "WBS.md").read_bytes().decode("utf-8"), UNESTIMATED)
    six = page_model(UNESTIMATED, data, default_unit_hours=6)
    assert six["schedule"]["items"]
    assert all(i["hours"] == 6 for i in six["schedule"]["items"])
    assert all(leaf["unestimated"] for leaf in six["leaves"])
    assert six["notes"]["interim"] == INTERIM_NOTE
    other = page_model(UNESTIMATED, data, default_unit_hours=7.5)
    assert all(i["hours"] == 7.5 for i in other["schedule"]["items"])
    end = lambda m: max(e for _, e, _ in plan(m["schedule"]["items"], 4).values())
    assert end(other) != end(six)


def test_estimate_midpoint():
    data = with_leaf(fixture("page"), "Issue-2", estimate=SimpleNamespace(lo_h=2, hi_h=5))
    m = model(data=data)
    leaf = by_key(m)["Issue-2"]
    assert (leaf["hours"], leaf["unestimated"]) == (3.5, False)
    assert m["notes"]["interim"] is None
    for lo, hi in ((5, 2), (2, math.inf), (0, 2), (True, 2), ("2", 5)):
        bad = model(data=with_leaf(fixture("page"), "Issue-2", estimate=SimpleNamespace(lo_h=lo, hi_h=hi)))
        assert (by_key(bad)["Issue-2"]["hours"], by_key(bad)["Issue-2"]["unestimated"]) == (24, True), (lo, hi)
        assert bad["notes"]["interim"] == INTERIM_NOTE


def test_model_tiers_table_and_line():
    tiers = SimpleNamespace(thinking="opus", checking="fable")
    m = model(data=with_leaf(fixture("page"), "Issue-11", models=tiers))
    leaf = by_key(m)["Issue-11"]
    assert leaf["models"] == ["opus", "fable"]
    assert leaf["mc"] == ["m-opus", "m-fable"]
    frame = m["lanes"][1]
    assert {side: dict(pairs) for side, pairs in frame["models"].items()} == {
        "think": {"opus": 1}, "check": {"fable": 1}}
    assert m["notes"]["no_tiers"] is None
    plain = model()
    assert plain["notes"]["no_tiers"] == NO_TIERS
    assert all(leaf["models"] is None and leaf["mc"] is None for leaf in plain["leaves"])


def test_empty_and_all_finished_sites():
    empty = model("empty")
    assert empty["empty"] is True
    assert empty["panel"] == ["WBS.md has no node lines"]
    done = model(data=parse_wbs("- [x] Done {issue: Issue-1}\n", "alpha"))
    assert done["empty"] is False
    assert done["schedule"]["items"] == [] and done["lanes"] == []
