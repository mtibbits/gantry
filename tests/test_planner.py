"""The week planner: gantry/planner.py (#61)."""

import itertools
import re
import shutil
import tomllib
from datetime import date

import pytest

from gantry import planner, schedule
from gantry.build import build_all
from gantry.config import load_config
from gantry.planner import NO_PLAN, plan_week
from gantry.portfolio_file import read_portfolio_file
from helpers_config import run_main, sample_build
from test_samples import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "week_plan"
FIXTURE_NAMES = ("binds_attention", "binds_lanes", "continues", "cross_site", "cross_site_cycle", "states")
DELETE = object()  # plan_of: remove the file instead of writing it
_copies = itertools.count()


def install(tmp_path, base, wbs=None, portfolio=None, config=None):
    """A copy of fixture `base` with the named files rewritten: the copy's folder.

    `wbs` maps a site to its WBS.md text; `portfolio` is the portfolio file's
    text; `config` maps a top-level config key to its raw TOML literal (None
    drops the key). DELETE removes a file.
    """
    copy = tmp_path / f"{base}-{next(_copies)}"
    shutil.copytree(FIXTURES / base, copy)
    files = {copy / site / "WBS.md": text for site, text in (wbs or {}).items()}
    if portfolio is not None:
        files[copy / "portfolio.md"] = portfolio
    for path, text in files.items():
        if text is DELETE:
            path.unlink()
        else:
            path.write_bytes(text.encode("utf-8"))
    if config:
        path = copy / "gantry.toml"
        lines = [line for line in path.read_bytes().decode("utf-8").splitlines()
                 if line.split(" = ")[0] not in config]
        lines += [f"{key} = {value}" for key, value in config.items() if value is not None]
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return copy


def built(copy):
    """(build result, the portfolio file as read or None, config) of an installation."""
    config = load_config(copy / "gantry.toml")
    result = build_all(config)
    portfolio = None
    if config.portfolio_file is not None:
        portfolio = read_portfolio_file(result.roots, config.portfolio_file,
                                        br_namespace=config.requirements.br_namespace)
    return result, portfolio, config


def plan_of(tmp_path, base, wbs=None, portfolio=None, config=None):
    return plan_week(*built(install(tmp_path, base, wbs, portfolio, config)))


WEEK = "{ mon = %s, tue = %s, wed = %s, thu = %s, fri = %s }"


def portfolio_md(hours=8, lanes=1, block_min=1, start_cost=0.25, attention=None, more="", tail=""):
    """A portfolio file's text. `attention` is the attention_hours literal (default:
    `hours` on Monday to Friday); None for `attention`'s literal, `lanes`,
    `block_min` or `start_cost` leaves the key out. `more` goes in [capacity],
    `tail` after it."""
    if attention is None:
        attention = WEEK % ((hours,) * 5)
    keys = {"attention_hours": attention, "agent_lanes": lanes, "block_min_hours": block_min,
            "block_start_cost_hours": start_cost}
    capacity = "".join(f"{key} = {value}\n" for key, value in keys.items() if value not in (None, ""))
    return ("# Portfolio\n\n## Business requirements\n\n- BR-1: Keep it small.\n\n## Settings\n\n"
            f"```toml\nportfolio_version = 1\n\n[capacity]\n{capacity}{more}{tail}```\n")


def reserved(day, hours, label=None, start="09:00"):
    text = f'\n[[capacity.reserved]]\nday = "{day}"\nstart = "{start}"\nhours = {hours}\n'
    return text + (f'label = "{label}"\n' if label is not None else "")


def leaf(n, glyph=" ", **meta):
    """One leaf line: issue Issue-<n> and the given metadata, in order."""
    pairs = "".join(f", {key}: {value}" for key, value in meta.items())
    return f"  - [{glyph}] Leaf {n} {{issue: Issue-{n}{pairs}}}\n"


def wbs_md(*leaves):
    return "# Site\n\n- [ ] Epic\n" + "".join(leaves)


def placed(plan, site, key):
    """The plan's record of a placed leaf."""
    found = [entry for entry in plan.leaves if (entry["site"], entry["key"]) == (site, key)]
    assert len(found) == 1, (site, key, found)
    return found[0]


def record(plan, site, key):
    """The plan's one record of a leaf: its placed record or its unplanned one."""
    found = [entry for entry in plan.leaves if (entry["site"], entry["key"]) == (site, key)]
    found += [entry for s in plan.sites if s["name"] == site for entry in s["unplanned"] if entry["key"] == key]
    assert len(found) == 1, (site, key, found)
    return found[0]


def weekday(text):
    return date.fromisoformat(text).weekday()


# --- days and capacity ----------------------------------------------------------------


def test_as_of_on_non_working_day_starts_next_working_day(tmp_path):
    plan = plan_of(tmp_path, "binds_lanes", config={"as_of": "2026-01-17"})  # a Saturday
    assert plan.status == "planned"
    assert plan.as_of == "2026-01-17"
    assert plan.horizon_days[0] == "2026-01-19"


def test_fully_reserved_weekday_is_not_working(tmp_path):
    plan = plan_of(tmp_path, "binds_lanes", portfolio=portfolio_md(tail=reserved("wed", 8)))
    assert len(plan.horizon_days) == 10
    assert all(weekday(day) != 2 for day in plan.horizon_days)
    assert plan.horizon_days[:3] == ["2026-01-19", "2026-01-20", "2026-01-22"]


def test_planned_week_is_first_five_days(tmp_path):
    plan = plan_of(tmp_path, "binds_lanes")
    assert len(plan.horizon_days) == 10 and len(plan.planned_week) == 5
    assert plan.horizon_days[:5] == plan.planned_week
    assert plan.horizon_days == sorted(set(plan.horizon_days))
    assert [day["date"] for day in plan.days] == plan.horizon_days


def test_day_capacity_is_after_reserved_blocks(tmp_path):
    plan = plan_of(tmp_path, "binds_lanes",
                   portfolio=portfolio_md(lanes=2, tail=reserved("tue", 1.5, "Standing meeting", "10:30")))
    monday, tuesday = plan.days[0], plan.days[1]
    assert (monday["weekday"], monday["attention_hours_declared"], monday["attention_hours"]) == ("mon", 8.0, 8.0)
    assert monday["reserved"] == [] and monday["lane_hours"] == 12.0
    assert (tuesday["weekday"], tuesday["attention_hours_declared"], tuesday["attention_hours"]) == ("tue", 8.0, 6.5)
    assert tuesday["reserved"] == [{"start": "10:30", "hours": 1.5, "label": "Standing meeting"}]


# --- when there is no plan --------------------------------------------------------------

NO_SETTINGS = "# Portfolio\n\n## Business requirements\n\n- BR-1: Keep it small.\n"
NO_PLAN_CASES = [
    ("no_portfolio", "no portfolio_file", {"config": {"portfolio_file": None}}),
    ("settings_not_read", "missing file", {"portfolio": DELETE}),
    ("settings_not_read", "no settings block", {"portfolio": NO_SETTINGS}),
    ("settings_not_read", "invalid TOML", {"portfolio": portfolio_md(more="agent_lanes = = 2\n")}),
    ("settings_not_read", "bad version", {"portfolio": portfolio_md().replace(
        "portfolio_version = 1", "portfolio_version = 2")}),
    ("no_attention", "absent", {"portfolio": portfolio_md(attention="")}),
    ("no_attention", "rejected", {"portfolio": portfolio_md(attention="3")}),
    ("no_attention", "0 on every weekday", {"portfolio": portfolio_md(hours=0)}),
    ("no_attention", "under half a minute", {"portfolio": portfolio_md(attention="{ mon = 0.004 }")}),
    ("no_attention", "reserved rounds up to the day", {"portfolio": portfolio_md(
        attention="{ mon = 1 }", tail=reserved("mon", 0.995))}),
    ("no_attention", "before no_lanes", {"portfolio": portfolio_md(hours=0, lanes=0)}),
    ("no_lanes", "absent", {"portfolio": portfolio_md(lanes=None)}),
    ("no_lanes", "rejected", {"portfolio": portfolio_md(lanes=-1)}),
    ("no_lanes", "0", {"portfolio": portfolio_md(lanes=0)}),
    ("no_sites", "no site built", {"wbs": {"alpha": DELETE}}),
]


def test_no_plan_cases_cover_every_reason():
    assert {key for key, _, _ in NO_PLAN_CASES} == set(NO_PLAN)
    assert list(NO_PLAN) == ["no_portfolio", "settings_not_read", "no_attention", "no_lanes", "no_sites"]


@pytest.mark.parametrize(("key", "case", "changes"), NO_PLAN_CASES, ids=[f"{k}: {c}" for k, c, _ in NO_PLAN_CASES])
def test_no_plan_reasons(tmp_path, key, case, changes):
    plan = plan_of(tmp_path, "binds_lanes", **changes)
    assert plan.status == "no_plan"
    assert plan.reason == NO_PLAN[key]
    assert str(tmp_path) not in plan.reason and "/" not in plan.reason
    assert plan.plan_version == planner.PLAN_VERSION == 1
    assert plan.as_of == "2026-01-19"
    assert plan.allocation_rule == planner.ALLOCATION_RULE
    assert (plan.horizon_days, plan.planned_week, plan.days, plan.leaves, plan.sites) == ([], [], [], [], [])
    assert plan.unestimated_planned == 0
    assert plan.pools == [
        {"name": planner.POOL_ATTENTION, "capacity_source": planner.CAPACITY_SOURCE, "days_run_out": []},
        {"name": planner.POOL_LANES, "capacity_source": planner.CAPACITY_SOURCE, "days_run_out": []},
    ]
    assert plan.binding == {"pool": None, "first_day": None, "text": planner.NO_POOL}
    assert plan.mapping["values"] == [0.0, 0.1, 0.25, 0.5, 1.0]
    assert plan.report == []


def test_planned_plan_has_no_reason(tmp_path):
    plan = plan_of(tmp_path, "binds_lanes")
    assert (plan.status, plan.reason) == ("planned", None)
    assert plan.report == []
    assert plan.mapping == {"values": [0.0, 0.1, 0.25, 0.5, 1.0], "source": "default", "rejected": None}


# --- numbers at the extremes: plan_week never raises ------------------------------------

EXTREMES = [
    ("block_min 1e308", portfolio_md(block_min="1e308")),
    ("400-digit start cost", portfolio_md(start_cost="9" * 400)),
    ("reserved 1e308 hours", portfolio_md(tail=reserved("mon", "1e308"))),
    ("400-digit agent_lanes", portfolio_md(lanes="9" * 400)),
    ("1e-7 rate", portfolio_md(more="attention_mapping = [0, 1e-7, 0.25, 0.5, 1.0]\n")),
]


@pytest.mark.parametrize(("case", "portfolio"), EXTREMES, ids=[case for case, _ in EXTREMES])
def test_extreme_numbers_still_plan(tmp_path, case, portfolio):
    copy = install(tmp_path, "binds_lanes", portfolio=portfolio)
    result, read, config = built(copy)
    assert read.settings_read and read.capacity.agent_lanes is not None, read.report
    plan = plan_week(result, read, config)
    assert plan.status == "planned"
    assert len(plan.horizon_days) == 10
    for day in plan.days:
        assert 0 <= day["attention_hours"] <= day["attention_hours_declared"] <= 24
        assert all(0 < block["hours"] <= 24 for block in day["reserved"])


def test_rate_below_half_a_part_per_million_is_rate_0(tmp_path):
    plan = plan_of(tmp_path, "binds_lanes",
                   portfolio=portfolio_md(more="attention_mapping = [0, 1e-7, 0.25, 0.5, 1.0]\n"))
    assert plan.mapping["source"] == "portfolio_file"
    assert placed(plan, "alpha", "Issue-1")["rate"] == 0.0


def test_to_minutes_rounds_once_and_clamps():
    to_minutes = planner.to_minutes
    assert [to_minutes(x) for x in (0, 0.004, 0.25, 0.995, 1, 1.5, 24)] == [0, 0, 15, 60, 60, 90, 1440]
    assert [to_minutes(x) for x in (25, 1e308, int("9" * 400), float("inf"))] == [1440] * 4
    assert [to_minutes(x) for x in (-1, float("nan"), None, True, "3")] == [0] * 5


def test_ppm_rounds_once_and_clamps():
    ppm, cap = planner._ppm, planner.MAX_RATE * planner.PPM
    assert [ppm(x) for x in (0, 4e-7, 5e-7, 0.25, 1)] == [0, 0, 1, 250_000, 1_000_000]
    assert [ppm(x) for x in (24, 25, 1e308, int("9" * 400), float("inf"))] == [cap] * 5
    assert [ppm(x) for x in (-1, float("nan"), None, True, "3")] == [0] * 5


# --- what each leaf costs ---------------------------------------------------------------


def test_in_progress_charged_full_midpoint(tmp_path):
    plan = plan_of(tmp_path, "states")
    entry = placed(plan, "a", "Issue-1")
    assert entry["state"] == "in_progress"
    assert entry["agent_hours"] == 6.0
    assert not entry["unestimated"]


def test_default_unit_hours_changes_hours(tmp_path):
    entry = placed(plan_of(tmp_path, "states"), "b", "Issue-2")
    assert (entry["agent_hours"], entry["unestimated"]) == (6.0, True)
    entry = placed(plan_of(tmp_path, "states", config={"default_unit_hours": "4"}), "b", "Issue-2")
    assert (entry["agent_hours"], entry["unestimated"]) == (4.0, True)


def test_leaf_record_carries_its_attention(tmp_path):
    entry = placed(plan_of(tmp_path, "binds_attention"), "alpha", "Issue-1")
    assert (entry["rate"], entry["need"], entry["tag"], entry["basis"]) == (1.0, 4, "scored", "scored: h: 4")


# --- which leaves are planned, and the unplanned list -------------------------------------


def unplanned(plan, site):
    """{key: (reason, state)} of a site's unplanned leaves; no key is listed twice."""
    entries = next(s for s in plan.sites if s["name"] == site)["unplanned"]
    assert len({entry["key"] for entry in entries}) == len(entries)
    return {entry["key"]: (entry["reason"], entry["state"]) for entry in entries}


def placed_keys(plan, site):
    return [entry["key"] for entry in plan.leaves if entry["site"] == site]


def test_states_question_and_parked_unplanned(tmp_path):
    plan = plan_of(tmp_path, "states")
    assert unplanned(plan, "a") == {
        "Issue-4": ("blocked_externally", "blocked_external"),
        "Issue-5": ("parked", "parked"),
        "Issue-6": ("waits_on_unplanned", "pending"),
    }
    # In-progress and stuck leaves are planned like any other open leaf.
    assert sorted(placed_keys(plan, "a")) == ["Issue-1", "Issue-2", "Issue-3"]
    assert [placed(plan, "a", key)["state"] for key in ("Issue-1", "Issue-3")] == ["in_progress", "stuck"]
    assert unplanned(plan, "b") == {}


def test_unplanned_list_is_in_wbs_order(tmp_path):
    plan = plan_of(tmp_path, "states", wbs={"a": wbs_md(
        leaf(1, h=1, depends_on="[Issue-2]"), leaf(2, "P", h=1), leaf(3, h=1), leaf(4, "?", h=1))})
    assert list(unplanned(plan, "a")) == ["Issue-1", "Issue-2", "Issue-4"]


def test_cycle_is_waits_on_cycle(tmp_path):
    plan = plan_of(tmp_path, "states", wbs={"a": wbs_md(
        leaf(1, h=1, depends_on="[Issue-2]"), leaf(2, h=1, depends_on="[Issue-1]"),
        leaf(3, h=1, depends_on="[Issue-2]"), leaf(4, h=1))})
    assert unplanned(plan, "a") == {key: ("waits_on_cycle", "pending") for key in ("Issue-1", "Issue-2", "Issue-3")}
    assert placed_keys(plan, "a") == ["Issue-4"]


def test_reason_precedence_before_the_horizon(tmp_path):
    plan = plan_of(tmp_path, "states", wbs={
        "a": wbs_md(
            leaf(1, "?", h=1, depends_on="[b:Issue-2]"),  # blocked externally, and waits on another site
            leaf(2, "P", h=1, depends_on="[zz:Issue-1]"),  # parked, and waits on an absent site
            leaf(3, "?", h=1, depends_on="[Issue-4]"),  # blocked externally, in a cycle within and across sites
            leaf(4, h=1, depends_on="[Issue-3, b:Issue-3]"),  # in a site's own cycle and one across sites
            leaf(5, h=1, depends_on="[b:Issue-1, zz:Issue-1]"),  # in a cycle across sites, and on an absent site
            leaf(6, h=1, depends_on="[b:Issue-99, zz:Issue-1]"),  # unresolved, and on an absent site
            leaf(7, h=1, depends_on="[Issue-2, b:Issue-99]"),  # unresolved, and on a parked leaf
            leaf(8, h=1, depends_on="[Issue-7]"),  # waits on a leaf unplanned for another reason
        ),
        "b": wbs_md(leaf(1, h=1, depends_on="[a:Issue-5]"), leaf(2, h=1), leaf(3, h=1, depends_on="[a:Issue-4]")),
    })
    assert {key: reason for key, (reason, _) in unplanned(plan, "a").items()} == {
        "Issue-1": "blocked_externally", "Issue-2": "parked", "Issue-3": "blocked_externally",
        "Issue-4": "waits_on_cycle", "Issue-5": "cross_site_cycle", "Issue-6": "waits_on_site_absent",
        "Issue-7": "waits_on_unresolved", "Issue-8": "waits_on_unplanned",
    }
    assert {key: reason for key, (reason, _) in unplanned(plan, "b").items()} == {
        "Issue-1": "cross_site_cycle", "Issue-3": "cross_site_cycle"}
    # A cycle across sites that holds a site's own cycle is reported whole; first-leaf order.
    assert [[(e["site"], e["key"]) for e in entry["leaves"]] for entry in plan.report] == [
        [("a", "Issue-3"), ("a", "Issue-4"), ("b", "Issue-3")], [("a", "Issue-5"), ("b", "Issue-1")]]


def test_unplanned_dependent_still_lifts_its_blocker(tmp_path):
    # Effective priority is the worksite page's: the parked P0 leaf lifts the leaf it waits on.
    plan = plan_of(tmp_path, "states", wbs={"a": wbs_md(
        leaf(1, priority="P1", h=0), leaf(2, h=0), leaf(3, "P", priority="P0", h=0, depends_on="[Issue-2]"))})
    assert placed_keys(plan, "a") == ["Issue-2", "Issue-1"]


def test_unresolved_local_counts_own_and_row_entries(tmp_path):
    # A local entry that names nothing, or a malformed one, is no dependency: counted, never held on.
    plan = plan_of(tmp_path, "cross_site")
    assert record(plan, "a", "Issue-16")["unresolved_local"] == 2  # its own Issue-97 and its row's 9.9
    for key in ("Issue-13", "Issue-14"):
        assert placed(plan, "a", key)["days"][0] == "2026-01-19"
        assert placed(plan, "a", key)["unresolved_local"] == 1
    assert record(plan, "a", "Issue-7")["unresolved_local"] == 1  # its row's 9.9
    assert "Issue-9" in unplanned(plan, "a")
    assert record(plan, "a", "Issue-9")["unresolved_local"] == 1  # Issue-98
    counted = {("a", key) for key in ("Issue-7", "Issue-9", "Issue-13", "Issue-14", "Issue-16")}
    others = [entry for entry in plan.leaves if (entry["site"], entry["key"]) not in counted]
    others += [entry for site in plan.sites for entry in site["unplanned"] if (site["name"], entry["key"]) not in counted]
    assert others and all(entry["unresolved_local"] == 0 for entry in others)


# --- dependencies across sites (#62) -------------------------------------------------------


def test_cross_site_hold_until_blocker_finishes(tmp_path):
    plan = plan_of(tmp_path, "cross_site")
    assert placed(plan, "b", "Issue-1")["days"][-1] == "2026-01-20"
    assert placed(plan, "a", "Issue-1")["days"][0] == "2026-01-21"
    assert placed(plan, "a", "Issue-15")["days"][0] == "2026-01-22"  # local, behind the held leaf


def test_cross_site_finished_blocker_is_satisfied(tmp_path):
    assert placed(plan_of(tmp_path, "cross_site"), "a", "Issue-2")["days"][0] == "2026-01-19"


def test_cross_site_absent_and_unresolved(tmp_path):
    reasons = {key: reason for key, (reason, _) in unplanned(plan_of(tmp_path, "cross_site"), "a").items()}
    assert reasons["Issue-3"] == "waits_on_site_absent"
    assert reasons["Issue-4"] == "waits_on_unresolved"
    assert reasons["Issue-5"] == "waits_on_site_absent"  # both; the unresolved entry is first in WBS.md


def test_cross_site_row_entry_holds_leaves_under_it(tmp_path):
    plan = plan_of(tmp_path, "cross_site")
    assert placed(plan, "a", "Issue-7")["days"][0] == "2026-01-21" == placed(plan, "a", "Issue-1")["days"][0]


def test_cross_site_row_target_waits_for_last_open_leaf(tmp_path):
    plan = plan_of(tmp_path, "cross_site")
    assert placed(plan, "b", "Issue-6")["days"][-1] == "2026-01-19"
    assert placed(plan, "b", "Issue-7")["days"][-1] == "2026-01-21"
    assert placed(plan, "a", "Issue-8")["days"][0] == "2026-01-22"


def test_cross_site_unplanned_blocker_is_waits_on_unplanned(tmp_path):
    plan = plan_of(tmp_path, "cross_site")
    assert unplanned(plan, "b")["Issue-3"] == ("parked", "parked")
    assert unplanned(plan, "a")["Issue-9"][0] == "waits_on_unplanned"


def test_cross_site_blocker_past_horizon_is_beyond_horizon(tmp_path):
    plan = plan_of(tmp_path, "cross_site")
    assert placed(plan, "b", "Issue-4")["hours_after_horizon"] == 30.0
    assert unplanned(plan, "b")["Issue-10"][0] == "beyond_horizon"
    assert unplanned(plan, "a")["Issue-10"][0] == "beyond_horizon"
    assert unplanned(plan, "a")["Issue-11"][0] == "beyond_horizon"


def test_cross_site_reason_order_is_global(tmp_path):
    # a Issue-17 waits on b Issue-13, which waits on b Issue-12, which waits on an absent site:
    # a reason pass per site, in config order, would leave a Issue-17 to the day loop.
    plan = plan_of(tmp_path, "cross_site")
    assert unplanned(plan, "b")["Issue-12"][0] == "waits_on_site_absent"
    assert unplanned(plan, "b")["Issue-13"][0] == "waits_on_unplanned"
    assert unplanned(plan, "a")["Issue-17"][0] == "waits_on_unplanned"


def test_priority_does_not_cross_sites(tmp_path):
    plan = plan_of(tmp_path, "cross_site")
    order = placed_keys(plan, "b")
    assert order.index("Issue-9") < order.index("Issue-8")  # P2 first, though the P0 on a waits on the P3
    assert placed(plan, "b", "Issue-9")["days"][0] == placed(plan, "b", "Issue-8")["days"][0] == "2026-01-19"
    assert placed(plan, "a", "Issue-12")["days"][0] == "2026-01-20"


def test_cross_site_cycle_reported_once(tmp_path):
    plan = plan_of(tmp_path, "cross_site_cycle")
    assert plan.status == "planned" and len(plan.days) == 10
    assert plan.report == [
        {"kind": "cross_site_cycle", "leaves": [
            {"site": "a", "key": "Issue-1"}, {"site": "a", "key": "Issue-2"}, {"site": "b", "key": "Issue-1"}]},
        {"kind": "cross_site_cycle", "leaves": [{"site": "a", "key": "Issue-5"}, {"site": "b", "key": "Issue-7"}]},
    ]
    a, b = unplanned(plan, "a"), unplanned(plan, "b")
    for site, key in (("a", "Issue-1"), ("a", "Issue-2"), ("b", "Issue-1"), ("a", "Issue-5"), ("b", "Issue-7")):
        assert {"a": a, "b": b}[site][key][0] == "cross_site_cycle", (site, key)
    assert a["Issue-3"][0] == b["Issue-2"][0] == "waits_on_unplanned"
    assert b["Issue-4"][0] == b["Issue-5"][0] == "waits_on_cycle"  # a site's own cycle: not in report
    assert "Issue-6" not in b  # the row target is never a leaf
    assert placed(plan, "a", "Issue-4")["days"][0] == placed(plan, "b", "Issue-3")["days"][0] == "2026-01-19"


def test_cross_site_hold_in_a_block_at_rate_above_1(tmp_path):
    plan = plan_of(tmp_path, "states",
                   portfolio=portfolio_md(hours=8, lanes=3, more="attention_mapping = [0, 0.1, 0.25, 0.5, 2.0]\n"),
                   wbs={"a": wbs_md(leaf(1, priority="P1", est="1h", h=4, depends_on="[b:Issue-1]")),
                        "b": wbs_md(leaf(1, est="3h", h=4))})
    assert placed(plan, "b", "Issue-1")["days"] == ["2026-01-19"]
    assert placed(plan, "a", "Issue-1")["days"] == ["2026-01-20"]
    assert block_keys(plan.days[0]) == ["Issue-1"] and plan.days[0]["blocks"][0]["site"] == "b"
    assert block_keys(plan.days[1]) == ["Issue-1"] and plan.days[1]["blocks"][0]["site"] == "a"


def test_no_leaf_is_waits_on_other_site(tmp_path):
    assert "waits_on_other_site" not in planner.UNPLANNED_REASONS
    for subject in invariant_subjects():
        plan, _ = subject_plan(tmp_path, subject)
        assert all(entry["reason"] != "waits_on_other_site" for site in plan.sites for entry in site["unplanned"])


def in_force_shares(portfolio, as_of):
    """The shares of the allocation in force on `as_of`: the latest from on or before it."""
    return max((a for a in portfolio.allocations if a.from_date <= as_of), key=lambda a: a.from_date).shares


@pytest.mark.parametrize("base", ["cross_site", "cross_site_cycle"])
def test_cross_site_fixtures_have_spare_capacity(tmp_path, base):
    result, portfolio, config = built(install(tmp_path, base))
    plan = plan_week(result, portfolio, config)
    assert all(day["ran_out"] == [] for day in plan.days)
    assert set(in_force_shares(portfolio, config.as_of)) >= {site["name"] for site in plan.sites} == {"a", "b"}


def open_keys(copy, site):
    result, _, _ = built(copy)
    return {found.key for found in result.inputs[site].data.leaves if found.open}


@pytest.mark.parametrize("base", FIXTURE_NAMES)
def test_every_open_leaf_is_placed_or_unplanned_once(tmp_path, base):
    copy = install(tmp_path, base)
    plan = plan_week(*built(copy))
    assert plan.sites
    for site in plan.sites:
        on_days = placed_keys(plan, site["name"])
        listed = unplanned(plan, site["name"])
        assert len(set(on_days)) == len(on_days)
        assert not set(on_days) & set(listed)
        assert set(on_days) | set(listed) == open_keys(copy, site["name"])


# --- the day loop: blocks, the lanes row, started leaves, the binding pool ------------------

ATTENTION, LANES = planner.POOL_ATTENTION, planner.POOL_LANES
EPS = 1e-9


def file_capacity(portfolio_path):
    """The capacity the portfolio file states, read by the test itself: (hours
    after reserved blocks per weekday, lanes, block_min, start cost)."""
    text = portfolio_path.read_bytes().decode("utf-8")
    settings = tomllib.loads(re.search(r"```toml\n(.*?)```", text, re.S).group(1))["capacity"]
    hours = dict.fromkeys(planner.WEEKDAYS, 0) | settings["attention_hours"]
    for block in settings.get("reserved", []):
        hours[block["day"]] = max(0, hours[block["day"]] - block["hours"])
    return (hours, settings["agent_lanes"], settings.get("block_min_hours", 1),
            settings.get("block_start_cost_hours", 0.25))


def check_invariants(plan, portfolio_path):
    """The invariants of the first acceptance criterion; returns the number of blocks."""
    hours, lanes, block_min, start_cost = file_capacity(portfolio_path)
    rate = {(entry["site"], entry["key"]): entry["rate"] for entry in plan.leaves}
    on_days = {key: 0 for key in rate}
    charged = {site["name"]: [0, 0] for site in plan.sites}
    blocks = 0
    assert plan.status == "planned" and len(plan.days) == 10
    for day in plan.days:
        attention = hours[day["weekday"]]
        assert attention > 0
        assert day["attention_hours"] == pytest.approx(attention)
        assert day["lane_hours"] == lanes * 6
        today = {}
        assert [block["number"] for block in day["blocks"]] == list(range(1, len(day["blocks"]) + 1))
        sites = [block["site"] for block in day["blocks"]]
        assert len(set(sites)) == len(sites), "a site has at most one block a day"
        for block in day["blocks"]:
            blocks += 1
            assert block["leaves"], "a block that could hold no leaf is not opened"
            assert {entry["site"] for entry in block["leaves"]} == {block["site"]}, "no block mixes sites"
            assert block["start_cost_hours"] == pytest.approx(start_cost)
            assert block["length_hours"] >= block_min - EPS
            operator = sum(entry["operator_hours"] for entry in block["leaves"])
            assert operator + block["start_cost_hours"] <= block["length_hours"] + EPS
            charged[block["site"]][0] += block["length_hours"]
            for entry in block["leaves"]:
                assert rate[entry["site"], entry["key"]] > 0
                assert entry["operator_hours"] >= entry["agent_hours"] * rate[entry["site"], entry["key"]] - EPS
        for entry in day["lanes_row"]:
            assert rate[entry["site"], entry["key"]] == 0
        for entry in [e for block in day["blocks"] for e in block["leaves"]] + day["lanes_row"]:
            assert entry["agent_hours"] > 0
            key = (entry["site"], entry["key"])
            today[key] = today.get(key, 0) + entry["agent_hours"]
            charged[entry["site"]][1] += entry["agent_hours"]
        lengths = sum(block["length_hours"] for block in day["blocks"])
        assert lengths <= attention + EPS
        assert all(agent <= 6 + EPS for agent in today.values())
        assert sum(today.values()) <= lanes * 6 + EPS
        assert day["left"]["attention_hours"] == pytest.approx(attention - lengths)
        assert day["left"]["lane_hours"] == pytest.approx(lanes * 6 - sum(today.values()))
        assert set(day["ran_out"]) <= {ATTENTION, LANES}
        for key, agent in today.items():
            on_days[key] += agent
    for entry in plan.leaves:
        key = (entry["site"], entry["key"])
        assert on_days[key] + entry["hours_after_horizon"] == pytest.approx(entry["agent_hours"])
        assert on_days[key] > 0
    for site in plan.sites:
        assert site["attention_hours_charged"] == pytest.approx(charged[site["name"]][0])
        assert site["lane_hours_charged"] == pytest.approx(charged[site["name"]][1])
    return blocks


def invariant_subjects():
    """Every installation under tests/fixtures/week_plan/, and the demo build."""
    return sorted(path.parent.name for path in FIXTURES.glob("*/gantry.toml")) + ["demo"]


def subject_plan(tmp_path, subject):
    """(plan, the portfolio file's path) of one invariant subject."""
    if subject == "demo":
        result = sample_build(tmp_path)
        path = tmp_path / "samples" / "portfolio.md"
        portfolio = read_portfolio_file(result.roots, result.config.portfolio_file,
                                        br_namespace=result.config.requirements.br_namespace)
        return plan_week(result, portfolio, result.config), path
    copy = install(tmp_path, subject)
    return plan_week(*built(copy)), copy / "portfolio.md"


@pytest.mark.parametrize("subject", invariant_subjects())
def test_invariants_hold(tmp_path, subject):
    plan, path = subject_plan(tmp_path, subject)
    check_invariants(plan, path)


def test_invariant_subjects(tmp_path):
    subjects = invariant_subjects()
    assert set(FIXTURE_NAMES) | {"demo"} <= set(subjects)
    with_block = [s for s in subjects if check_invariants(*subject_plan(tmp_path, s)) > 0]
    assert len(with_block) >= 3, with_block


def block_keys(day, n=0):
    return [entry["key"] for entry in day["blocks"][n]["leaves"]]


def row_keys(day):
    return [(entry["site"], entry["key"]) for entry in day["lanes_row"]]


def run_out(plan, pool):
    return next(p for p in plan.pools if p["name"] == pool)["days_run_out"]


def test_binds_attention_names_pool_and_first_day(tmp_path):
    plan = plan_of(tmp_path, "binds_attention")
    assert plan.binding == {"pool": ATTENTION, "first_day": "2026-01-19", "text": ATTENTION}
    assert run_out(plan, ATTENTION) == ["2026-01-19", "2026-01-20", "2026-01-21"]
    assert run_out(plan, LANES) == []
    first = plan.days[0]
    assert first["ran_out"] == [ATTENTION]
    assert len(first["blocks"]) == 1
    assert first["blocks"][0] == {
        "number": 1, "site": "alpha", "length_hours": 2.0, "start_cost_hours": 0.25,
        "leaves": [{"site": "alpha", "key": "Issue-1", "agent_hours": 1.75, "operator_hours": 1.75}],
    }
    assert first["left"] == {"attention_hours": 0.0, "lane_hours": 12 - 1.75}
    # The fourth day ends Issue-1 and has room for all of Issue-2: nothing runs out.
    assert block_keys(plan.days[3]) == ["Issue-1", "Issue-2"]
    assert plan.days[3]["ran_out"] == []


def test_binds_lanes_names_pool_and_first_day(tmp_path):
    plan = plan_of(tmp_path, "binds_lanes")
    assert plan.binding == {"pool": LANES, "first_day": "2026-01-19", "text": LANES}
    assert run_out(plan, LANES) == ["2026-01-19", "2026-01-20"]
    assert run_out(plan, ATTENTION) == []
    assert all(ATTENTION not in day["ran_out"] for day in plan.days)
    first = plan.days[0]
    assert first["ran_out"] == [LANES]
    # The block's work needs 0.45 h (0.25 to start, 0.2 for Issue-1) and is charged the minimum.
    assert first["blocks"] == [{
        "number": 1, "site": "alpha", "length_hours": 1.0, "start_cost_hours": 0.25,
        "leaves": [{"site": "alpha", "key": "Issue-1", "agent_hours": 2.0, "operator_hours": 0.2}],
    }]
    assert first["lanes_row"] == [{"site": "alpha", "key": "Issue-2", "agent_hours": 4.0}]
    assert placed(plan, "alpha", "Issue-3")["days"] == ["2026-01-20", "2026-01-21"]
    site = plan.sites[0]
    assert (site["attention_hours_charged"], site["lane_hours_charged"]) == (1.0, 14.0)
    assert site["ratio_source"] == "estimated"


def test_ample_capacity_no_pool_ran_out(tmp_path):
    plan = plan_of(tmp_path, "binds_lanes", portfolio=portfolio_md(lanes=5))
    assert plan.binding == {"pool": None, "first_day": None, "text": planner.NO_POOL}
    assert run_out(plan, ATTENTION) == [] and run_out(plan, LANES) == []
    assert all(day["ran_out"] == [] for day in plan.days)
    assert len(plan.leaves) == 3


def test_attention_end_splits_leaf_and_continues_first(tmp_path):
    plan = plan_of(tmp_path, "binds_attention")
    first, second = plan.days[0], plan.days[1]
    assert first["blocks"][0]["leaves"][0]["agent_hours"] * 60 == 105
    assert sum(block["length_hours"] for block in first["blocks"]) * 60 == 120
    assert first["attention_hours"] == 2.0
    assert block_keys(second)[0] == "Issue-1"
    assert placed(plan, "alpha", "Issue-1")["days"][:2] == ["2026-01-19", "2026-01-20"]


def test_block_goes_on_with_a_leaf_that_still_fits(tmp_path):
    # At rate 2, 105 operator minutes hold 52 agent minutes of Issue-1 and leave 1,
    # which holds 10 agent minutes of Issue-2 at rate 0.1.
    plan = plan_of(tmp_path, "binds_attention",
                   portfolio=portfolio_md(hours=2, lanes=2, more="attention_mapping = [0, 0.1, 0.25, 0.5, 2.0]\n"),
                   wbs={"alpha": wbs_md(leaf(1, priority="P1", est="6h", h=4), leaf(2, priority="P2", est="1h", h=1))})
    block = plan.days[0]["blocks"][0]
    assert [(e["key"], round(e["agent_hours"] * 60), round(e["operator_hours"] * 60)) for e in block["leaves"]] == [
        ("Issue-1", 52, 104), ("Issue-2", 10, 1)]
    assert block["length_hours"] == 2.0
    assert plan.days[0]["ran_out"] == [ATTENTION]


def test_block_leaf_with_no_lane_records_lanes(tmp_path):
    plan = plan_of(tmp_path, "binds_lanes", wbs={"alpha": wbs_md(
        leaf(1, priority="P1", est="6h", h=1), leaf(2, priority="P2", est="1h", h=1))})
    first = plan.days[0]
    assert block_keys(first) == ["Issue-1"]
    assert first["ran_out"] == [LANES]
    assert first["left"]["lane_hours"] == 0.0 and first["left"]["attention_hours"] == 7.0
    assert block_keys(plan.days[1]) == ["Issue-2"]


def test_first_block_goes_to_more_urgent_site(tmp_path):
    plan = plan_of(tmp_path, "states", wbs={
        "a": wbs_md(leaf(1, priority="P2", est="1h", h=1)),
        "b": wbs_md(leaf(1, priority="P1", est="1h", h=1)),
    })
    assert [block["site"] for block in plan.days[0]["blocks"]] == ["b", "a"]
    assert [block["number"] for block in plan.days[0]["blocks"]] == [1, 2]


def test_equal_urgency_goes_to_config_order(tmp_path):
    plan = plan_of(tmp_path, "states", wbs={
        "a": wbs_md(leaf(1, priority="P2", est="1h", h=1)),
        "b": wbs_md(leaf(1, priority="P2", est="1h", h=1)),
    })
    assert [block["site"] for block in plan.days[0]["blocks"]] == ["a", "b"]


def test_one_block_per_site_per_day(tmp_path):
    # b's leaf is more urgent than a's second leaf, yet a's block holds both of a's leaves.
    plan = plan_of(tmp_path, "states", wbs={
        "a": wbs_md(leaf(1, priority="P1", est="1h", h=1), leaf(2, priority="P3", est="1h", h=1)),
        "b": wbs_md(leaf(1, priority="P2", est="1h", h=1)),
    })
    first = plan.days[0]
    assert [(block["site"], [e["key"] for e in block["leaves"]]) for block in first["blocks"]] == [
        ("a", ["Issue-1", "Issue-2"]), ("b", ["Issue-1"])]
    for day in plan_of(tmp_path, "states").days:
        sites = [block["site"] for block in day["blocks"]]
        assert len(sites) == len(set(sites))


def test_lanes_row_site_by_site(tmp_path):
    # b holds the most urgent rate-0 leaf, so all of b's come before any of a's.
    plan = plan_of(tmp_path, "states", wbs={
        "a": wbs_md(leaf(1, priority="P2", est="1h", h=0), leaf(2, priority="P4", est="1h", h=0)),
        "b": wbs_md(leaf(1, priority="P3", est="1h", h=0), leaf(2, priority="P1", est="1h", h=0)),
    })
    assert row_keys(plan.days[0]) == [("b", "Issue-2"), ("b", "Issue-1"), ("a", "Issue-1"), ("a", "Issue-2")]
    assert plan.days[0]["blocks"] == []
    # On equal urgency the sites go in config order.
    plan = plan_of(tmp_path, "states", wbs={
        "a": wbs_md(leaf(1, priority="P2", est="1h", h=0)),
        "b": wbs_md(leaf(1, priority="P2", est="1h", h=0)),
    })
    assert row_keys(plan.days[0]) == [("a", "Issue-1"), ("b", "Issue-1")]


def test_allocation_rule_text(tmp_path):
    plan = plan_of(tmp_path, "states")
    assert plan.allocation_rule == "most urgent site first; intended shares not applied"
    assert plan.allocation_rule == planner.ALLOCATION_RULE


@pytest.mark.parametrize("h", [1, 0])
def test_dependent_starts_day_after_blocker_finishes(tmp_path, h):
    plan = plan_of(tmp_path, "binds_lanes", portfolio=portfolio_md(lanes=5), wbs={"alpha": wbs_md(
        leaf(1, est="1h", h=h), leaf(2, priority="P0", est="1h", h=h, depends_on="[Issue-1]"))})
    assert placed(plan, "alpha", "Issue-1")["days"] == ["2026-01-19"]
    assert placed(plan, "alpha", "Issue-2")["days"] == ["2026-01-20"]
    first = plan.days[0]
    assert first["left"]["attention_hours"] >= 7 and first["left"]["lane_hours"] == 29.0
    assert first["ran_out"] == []


def test_dependent_waits_for_a_blocker_that_takes_two_days(tmp_path):
    plan = plan_of(tmp_path, "binds_lanes", portfolio=portfolio_md(lanes=5), wbs={"alpha": wbs_md(
        leaf(1, est="9h", h=0), leaf(2, est="1h", h=0, depends_on="[Issue-1]"))})
    assert placed(plan, "alpha", "Issue-1")["days"] == ["2026-01-19", "2026-01-20"]
    assert placed(plan, "alpha", "Issue-2")["days"] == ["2026-01-21"]


# One lane and 6.25 h of attention: a rate-1 leaf's 6 agent hours take the whole lane
# and, with the start cost, the whole day's attention, in one placement.
TIE = {"portfolio": portfolio_md(hours=6.25, lanes=1)}


def test_binding_tie_more_days_then_attention(tmp_path):
    both = wbs_md(leaf(1, priority="P1", est="12h", h=4), leaf(2, priority="P2", est="6h", h=4))
    plan = plan_of(tmp_path, "binds_lanes", wbs={"alpha": both}, **TIE)
    assert [day["ran_out"] for day in plan.days[:3]] == [[ATTENTION, LANES], [ATTENTION, LANES], []]
    assert run_out(plan, ATTENTION) == run_out(plan, LANES) == ["2026-01-19", "2026-01-20"]
    # A full tie: operator attention hours.
    assert plan.binding == {"pool": ATTENTION, "first_day": "2026-01-19", "text": ATTENTION}
    # A rate-0 leaf waits for a lane on the third day too: agent lanes ran out on more days.
    more = both + leaf(3, priority="P3", est="1h", h=0)
    plan = plan_of(tmp_path, "binds_lanes", wbs={"alpha": more}, **TIE)
    assert [day["ran_out"] for day in plan.days[:4]] == [[ATTENTION, LANES], [ATTENTION, LANES], [LANES], []]
    assert plan.binding == {"pool": LANES, "first_day": "2026-01-19", "text": LANES}


def test_binding_is_the_pool_that_ran_out_first(tmp_path):
    # Lanes run out on day 1 only; attention on days 2 and 3: the earlier day wins over more days.
    plan = plan_of(tmp_path, "binds_lanes", portfolio=portfolio_md(hours=2, lanes=1), wbs={"alpha": wbs_md(
        leaf(1, priority="P1", est="6h", h=0), leaf(2, priority="P2", est="1h", h=0),
        leaf(3, priority="P3", est="5h", h=4, depends_on="[Issue-2]"))})
    assert [day["ran_out"] for day in plan.days[:5]] == [[LANES], [], [ATTENTION], [ATTENTION], []]
    assert plan.binding == {"pool": LANES, "first_day": "2026-01-19", "text": LANES}


def test_block_stopped_at_attention_counts_though_a_later_leaf_takes_the_last_lane(tmp_path):
    # Rate 2 lets a block stop at the day's attention on Issue-2 with operator minutes
    # left for Issue-3, which then takes the last lane minutes: attention ran out
    # at the stop, so day 1 records both pools and attention, on more days, binds.
    plan = plan_of(tmp_path, "binds_lanes", portfolio=portfolio_md(
        hours=2, lanes=1, more="attention_mapping = [0, 0.1, 0.25, 0.5, 2.0]\n"), wbs={"alpha": wbs_md(
        leaf(1, priority="P0", est="5.3167h", h=1), leaf(2, priority="P1", est="6h", h=4),
        leaf(3, priority="P2", est="6h", h=1))})
    first = plan.days[0]
    assert [entry["key"] for entry in first["blocks"][0]["leaves"]] == ["Issue-1", "Issue-2", "Issue-3"]
    assert first["left"] == {"attention_hours": 0.0, "lane_hours": 0.0}
    assert first["ran_out"] == [ATTENTION, LANES]
    assert run_out(plan, LANES) == ["2026-01-19"]
    assert run_out(plan, ATTENTION) == plan.planned_week
    assert plan.binding == {"pool": ATTENTION, "first_day": "2026-01-19", "text": ATTENTION}


def test_below_block_min_opens_no_block(tmp_path):
    plan = plan_of(tmp_path, "binds_attention", portfolio=portfolio_md(hours=0.75, lanes=2),
                   wbs={"alpha": wbs_md(leaf(1, est="1h", h=2))})
    assert len(plan.days) == 10
    assert all(day["blocks"] == [] and day["ran_out"] == [ATTENTION] for day in plan.days)
    assert all(day["left"]["attention_hours"] == 0.75 for day in plan.days)
    # The pools' days are the planned week's; each day's own list covers all 10.
    assert run_out(plan, ATTENTION) == plan.planned_week
    assert unplanned(plan, "alpha") == {"Issue-1": ("beyond_horizon", "pending")}


def test_start_cost_not_fitting_counts_as_attention_ran_out(tmp_path):
    # The deliberate reading: block_min_hours is left, but not the start cost plus one agent minute.
    plan = plan_of(tmp_path, "binds_attention", portfolio=portfolio_md(hours=1, lanes=5, block_min=1, start_cost=1),
                   wbs={"alpha": wbs_md(leaf(1, est="1h", h=2))})
    assert all(day["blocks"] == [] for day in plan.days)
    assert [day["ran_out"] for day in plan.days[:5]] == [[ATTENTION]] * 5
    assert plan.binding == {"pool": ATTENTION, "first_day": plan.planned_week[0], "text": ATTENTION}
    assert run_out(plan, LANES) == []


def test_continues_started_leaf_first_on_day_two(tmp_path):
    plan = plan_of(tmp_path, "continues")
    assert block_keys(plan.days[0]) == ["Issue-2", "Issue-1", "Issue-4"]
    # Issue-3 (P1) became ready on day 2; the started leaves still go first, in their own order.
    assert block_keys(plan.days[1]) == ["Issue-1", "Issue-4", "Issue-3"]
    assert placed(plan, "alpha", "Issue-3")["days"] == ["2026-01-20"]


def test_too_long_leaf_keeps_placements(tmp_path):
    plan = plan_of(tmp_path, "continues")
    entry = placed(plan, "alpha", "Issue-4")
    assert entry["days"] == plan.horizon_days
    assert (entry["agent_hours"], entry["hours_after_horizon"]) == (90.0, 30.0)
    assert unplanned(plan, "alpha") == {"Issue-5": ("beyond_horizon", "pending")}
    assert all(e["hours_after_horizon"] == 0 for e in plan.leaves if e["key"] != "Issue-4")


def test_fifteen_hour_leaf_over_three_days(tmp_path):
    plan = plan_of(tmp_path, "continues")
    assert placed(plan, "alpha", "Issue-1")["days"] == plan.horizon_days[:3]
    hours = [entry["agent_hours"] for day in plan.days[:3] for entry in day["blocks"][0]["leaves"]
             if entry["key"] == "Issue-1"]
    assert hours == [6.0, 6.0, 3.0]


def test_unestimated_counts(tmp_path):
    plan = plan_of(tmp_path, "states", config={"default_unit_hours": "15"})
    entry = placed(plan, "b", "Issue-2")
    assert entry["unestimated"] and len(entry["days"]) == 3
    # A leaf on three days counts once.
    assert plan.unestimated_planned == 1
    assert {site["name"]: site["unestimated_planned"] for site in plan.sites} == {"a": 0, "b": 1}
    assert [day["unestimated"] for day in plan.days] == [int(day["date"] in entry["days"]) for day in plan.days]
    # A recount over a plan with several, on both sites.
    plan = plan_of(tmp_path, "states", wbs={
        "a": wbs_md(leaf(1, h=1), leaf(2, est="2h", h=1), leaf(3, h=0)),
        "b": wbs_md(leaf(1, h=1), leaf(2, est="?w", h=0)),
    })
    flagged = {(e["site"], e["key"]) for e in plan.leaves if e["unestimated"]}
    assert flagged == {("a", "Issue-1"), ("a", "Issue-3"), ("b", "Issue-1"), ("b", "Issue-2")}
    assert plan.unestimated_planned == 4
    assert {site["name"]: site["unestimated_planned"] for site in plan.sites} == {"a": 2, "b": 2}
    for day in plan.days:
        on_day = {(e["site"], e["key"]) for block in day["blocks"] for e in block["leaves"]} | set(row_keys(day))
        assert day["unestimated"] == len(on_day & flagged)


RATE_0 = [  # (hours, priority) in WBS.md order
    (9, "P3"), (2, "P1"), (6, "P3"), (1, "P4"), (7, "P0"), (3, "P1"), (4, None), (5, "P2"),
]


@pytest.mark.parametrize("lanes", [1, 2, 3])
def test_rate_zero_first_planned_order_equals_plan_start_order(tmp_path, lanes):
    leaves = [leaf(n, est=f"{hours}h", h=0, **({"priority": p} if p else {}))
              for n, (hours, p) in enumerate(RATE_0, 1)]
    plan = plan_of(tmp_path, "binds_lanes", portfolio=portfolio_md(lanes=lanes), wbs={"alpha": wbs_md(*leaves)})
    items = [{"key": f"Issue-{n}", "hours": hours, "preds": [], "eff_priority": int(p[1]) if p else 3, "wbs_index": n}
             for n, (hours, p) in enumerate(RATE_0, 1)]
    assert placed_keys(plan, "alpha") == list(schedule.plan(items, lanes))
    assert len(plan.leaves) == len(RATE_0)


def test_planner_orders_through_schedule_pick_ready(tmp_path, monkeypatch):
    # One ordering rule, not a fork: the planner's order follows whatever pick_ready says.
    copy = install(tmp_path, "binds_lanes", portfolio=portfolio_md(lanes=1), wbs={"alpha": wbs_md(
        leaf(1, priority="P1", est="6h", h=0), leaf(2, priority="P2", est="6h", h=0),
        leaf(3, priority="P3", est="6h", h=1), leaf(4, priority="P4", est="6h", h=1))})
    args = built(copy)
    assert placed_keys(plan_week(*args), "alpha") == ["Issue-3", "Issue-4", "Issue-1", "Issue-2"]
    monkeypatch.setattr(schedule, "pick_ready", lambda ready: ready[-1])
    assert placed_keys(plan_week(*args), "alpha") == ["Issue-4", "Issue-3", "Issue-2", "Issue-1"]


def test_same_inputs_same_plan(tmp_path):
    for base in FIXTURE_NAMES:
        first, second = plan_of(tmp_path, base), plan_of(tmp_path, base)
        assert first == second and first is not second
        assert first.status == "planned"


def test_never_fits_is_beyond_horizon_with_dependent(tmp_path):
    # One hour of attention a day: the P1 leaf at rate 1 takes it all, every day.
    plan = plan_of(tmp_path, "binds_attention", portfolio=portfolio_md(hours=1, lanes=2), wbs={"alpha": wbs_md(
        leaf(1, priority="P1", est="3w", h=4), leaf(2, priority="P4", h=4), leaf(3, h=4, depends_on="[Issue-2]"))})
    assert placed_keys(plan, "alpha") == ["Issue-1"]
    assert placed(plan, "alpha", "Issue-1")["hours_after_horizon"] == 90 - 7.5
    assert unplanned(plan, "alpha") == {
        "Issue-2": ("beyond_horizon", "pending"), "Issue-3": ("beyond_horizon", "pending")}


def test_reason_precedence(tmp_path):
    plan = plan_of(tmp_path, "states", portfolio=portfolio_md(hours=1, lanes=2), wbs={
        "a": wbs_md(
            leaf(1, priority="P1", est="3w", h=4),
            leaf(2, priority="P4", h=4),  # never fits
            leaf(3, "P", h=4),
            leaf(4, h=4, depends_on="[Issue-2, Issue-3]"),  # waits on a parked leaf and on one beyond the horizon
            leaf(5, "?", h=4, depends_on="[b:Issue-2]"),  # blocked externally, with a cross-site entry
        ),
        "b": wbs_md(leaf(2, priority="P4", h=4)),
    })
    assert {key: reason for key, (reason, _) in unplanned(plan, "a").items()} == {
        "Issue-2": "beyond_horizon", "Issue-3": "parked", "Issue-4": "waits_on_unplanned",
        "Issue-5": "blocked_externally",
    }


@pytest.mark.parametrize("base", FIXTURE_NAMES)
def test_unplanned_reasons_are_the_eight(tmp_path, base):
    plan = plan_of(tmp_path, base)
    reasons = {entry["reason"] for site in plan.sites for entry in site["unplanned"]}
    assert reasons <= set(planner.UNPLANNED_REASONS)
    assert planner.UNPLANNED_REASONS == (
        "blocked_externally", "parked", "waits_on_cycle", "cross_site_cycle", "waits_on_site_absent",
        "waits_on_unresolved", "waits_on_unplanned", "beyond_horizon")


def test_extreme_block_min_opens_no_block(tmp_path):
    plan = plan_of(tmp_path, "binds_lanes", portfolio=portfolio_md(block_min="1e308"))
    assert all(day["blocks"] == [] for day in plan.days)
    assert plan.days[0]["ran_out"] == [ATTENTION, LANES]  # Issue-1 has no block; Issue-3 has no lane


def test_rate_0_by_rounding_goes_in_the_lanes_row(tmp_path):
    plan = plan_of(tmp_path, "binds_lanes",
                   portfolio=portfolio_md(more="attention_mapping = [0, 1e-7, 0.25, 0.5, 1.0]\n"))
    assert plan.days[0]["blocks"] == []
    assert row_keys(plan.days[0]) == [("alpha", "Issue-1"), ("alpha", "Issue-2")]


# --- through the build ------------------------------------------------------------------------


@pytest.mark.parametrize(("key", "case", "changes"), NO_PLAN_CASES, ids=[f"{k}: {c}" for k, c, _ in NO_PLAN_CASES])
def test_no_plan_file_still_written(tmp_path, key, case, changes):
    import json

    copy = install(tmp_path, "binds_lanes", **changes)
    stale = copy / "out" / planner.PLAN_FILE
    stale.parent.mkdir()
    stale.write_bytes(b'{"status": "planned"}\n')  # an earlier build's plan never stands in
    code, stdout, stderr = run_main("--config", str(copy / "gantry.toml"))
    # A no_plan plan leaves the exit code as the build sets it: 1 only when no page was written.
    assert code == (1 if key == "no_sites" else 0), stderr
    document = json.loads(stale.read_bytes().decode("utf-8"))
    assert (document["status"], document["reason"]) == ("no_plan", NO_PLAN[key])
    assert document["plan_version"] == 1 and document["days"] == []
    assert str(stale) in stdout.splitlines()
    assert "week-plan.json" not in stderr


# --- the documentation ----------------------------------------------------------------------

WEEK_PLAN_DOC = ROOT / "docs" / "week-plan.md"


def doc_section(text, heading):
    """The text of one level-2 section, heading line included. A CRLF checkout
    (Windows CI, autocrlf) reads as LF."""
    text = text.replace("\r\n", "\n")
    start = text.index(f"\n{heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else len(text)]


def test_week_plan_doc_states_planner():
    text = WEEK_PLAN_DOC.read_bytes().decode("utf-8")
    doc = " ".join(text.split())
    wanted = [
        "## The planner", "## The plan file", planner.ALLOCATION_RULE, planner.POOL_ATTENTION, planner.POOL_LANES,
        planner.NO_POOL, *NO_PLAN.values(), *(f"`{reason}`" for reason in planner.UNPLANNED_REASONS),
        "hours_after_horizon", planner.PLAN_FILE, "plan_version", "shape-v1.json", planner.CAPACITY_SOURCE,
        "never belongs in a repository", "deliberate reading", "start cost plus one agent minute",
        "`WBS.md` states only", "int(hours * 60 + 0.5)", "parts per million", "no build time",
        "gantry: error: cannot write week-plan.json: <reason>",
    ]
    assert [w for w in wanted if w not in doc] == []
    assert len(planner.UNPLANNED_REASONS) == 8 and len(NO_PLAN) == 5
    # The planner's rules are in its section, the file's in its own.
    the_planner = " ".join(doc_section(text, "## The planner").split())
    for topic in ("### Days and capacity", "### What each leaf costs", "### Blocks", "### A ready leaf that fits",
                  "### Order within a site", "### Which site goes next", "### Started in this plan", "### States",
                  "### Dependencies within a site", "### Unplanned leaves", "### Past the horizon",
                  "### The binding pool", "### When there is no plan"):
        assert topic in the_planner, topic
    the_file = " ".join(doc_section(text, "## The plan file").split())
    for topic in ("### Fields", "### The version rule", "### The pinned shape", "### Determinism"):
        assert topic in the_file, topic
    assert "The plan itself is #61" not in doc and "Neither exists yet" not in doc


def doc_json_blocks(text):
    return re.findall(r"```json\n(.*?)```", text, re.S)


def test_week_plan_doc_examples_are_real(tmp_path):
    import json

    from gantry.planner import plan_to_json

    section = doc_section(WEEK_PLAN_DOC.read_bytes().decode("utf-8"), "## The plan file")
    planned, no_plan = doc_json_blocks(section)
    # The no_plan form is a whole file, byte for byte.
    assert no_plan == plan_to_json(plan_of(tmp_path, "binds_lanes", config={"portfolio_file": None}))
    assert json.loads(no_plan)["status"] == "no_plan"
    # The planned example is binds_lanes' file with days, leaves and sites cut to their first item.
    document = json.loads(plan_to_json(plan_of(tmp_path, "binds_lanes")))
    for name in ("days", "leaves", "sites"):
        document[name] = document[name][:1]
    assert json.loads(planned) == document
    assert planned == json.dumps(document, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    assert document["status"] == "planned"


def test_week_plan_doc_names_every_field():
    from test_plan_file import V1_FIELDS

    doc = WEEK_PLAN_DOC.read_bytes().decode("utf-8")
    section = doc_section(doc, "## The plan file")
    names = {part.removesuffix("[]") for path in V1_FIELDS for part in path.split(".")[1:]}
    assert [name for name in sorted(names) if f"`{name}`" not in section] == []


def test_week_plan_doc_plan_file_names_every_reason():
    # A reader of the plan file finds every unplanned reason in the file's own section.
    the_file = doc_section(WEEK_PLAN_DOC.read_bytes().decode("utf-8"), "## The plan file")
    the_file = " ".join(the_file.split())
    assert [r for r in planner.UNPLANNED_REASONS if f"`{r}`" not in the_file] == []


def test_attention_not_recorded_for_a_leaf_with_no_lane_free(tmp_path):
    # a's block takes the one lane and leaves 0.25 h of attention. b's leaf never had a lane
    # minute free, so only agent lanes ran out: attention needs "a lane minute free".
    plan = plan_of(tmp_path, "states", portfolio=portfolio_md(hours=2, lanes=1), wbs={
        "a": wbs_md(leaf(1, priority="P1", est="60h", h=2)),
        "b": wbs_md(leaf(1, priority="P2", est="60h", h=2)),
    })
    assert plan.days[0]["left"] == {"attention_hours": 0.25, "lane_hours": 0.0}
    assert [day["ran_out"] for day in plan.days] == [[LANES]] * 10
    assert plan.binding == {"pool": LANES, "first_day": "2026-01-19", "text": LANES}
    # Within one block too: the first leaf ends the lanes, so the leaf behind it waits on lanes alone.
    plan = plan_of(tmp_path, "binds_lanes", portfolio=portfolio_md(hours=2, lanes=1), wbs={"alpha": wbs_md(
        leaf(1, priority="P1", est="60h", h=2), leaf(2, priority="P2", est="6h", h=4))})
    assert plan.days[0]["ran_out"] == [LANES]
