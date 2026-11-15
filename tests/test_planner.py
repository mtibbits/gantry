"""The week planner: gantry/planner.py (#61)."""

import itertools
import re
import shutil
import tomllib
from datetime import date
from fractions import Fraction

import pytest

from gantry import planner, schedule
from gantry.build import build_all
from gantry.config import load_config
from gantry.planner import NO_PLAN, plan_week
from gantry.portfolio_file import intended_shares, read_portfolio_file
from helpers_config import run_main, sample_build
from test_samples import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "week_plan"
FIXTURE_NAMES = ("binds_attention", "binds_lanes", "continues", "cross_site", "cross_site_cycle", "states",
                 # #37: fair share and priority tiers.
                 "late_ready", "no_allocation", "no_share", "p0", "share_unbuilt", "two_sites", "unequal_rates")
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


def with_portfolio(result):
    """(build result, its portfolio file as read, config) of a build of the samples."""
    return result, read_portfolio_file(result.roots, result.config.portfolio_file,
                                       br_namespace=result.config.requirements.br_namespace), result.config


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


def allocation(day, **shares):
    """One [[allocation]] table for portfolio_md(tail=...): `shares` from `day` (YYYY-MM-DD text)."""
    pairs = ", ".join(f"{name} = {value}" for name, value in shares.items())
    return f"\n[[allocation]]\nfrom = {day}\nshares = {{ {pairs} }}\n"


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
    assert plan.notes == []


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
    ("block_default 1e308", portfolio_md(more="block_default_hours = 1e308\n")),
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


def reasons_of(plan, site):
    """{key: reason} of a site's unplanned leaves."""
    return {key: reason for key, (reason, _) in unplanned(plan, site).items()}


def placed_keys(plan, site):
    return [entry["key"] for entry in plan.leaves if entry["site"] == site]


def test_states_question_and_parked_unplanned(tmp_path):
    plan = plan_of(tmp_path, "states")
    assert unplanned(plan, "a") == {
        "Issue-4": ("blocked_externally", "blocked_external"),
        "Issue-5": ("parked", "parked"),
        "Issue-6": ("waits_on_unplanned", "pending"),
    }
    # In-progress and stuck leaves are planned, as started work.
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
    assert reasons_of(plan, "a") == {
        "Issue-1": "blocked_externally", "Issue-2": "parked", "Issue-3": "blocked_externally",
        "Issue-4": "waits_on_cycle", "Issue-5": "cross_site_cycle", "Issue-6": "waits_on_site_absent",
        "Issue-7": "waits_on_unresolved", "Issue-8": "waits_on_unplanned",
    }
    assert reasons_of(plan, "b") == {
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
    others += [entry for site in plan.sites for entry in site["unplanned"]
               if (site["name"], entry["key"]) not in counted]
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
    reasons = reasons_of(plan_of(tmp_path, "cross_site"), "a")
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


def test_cross_site_hold_on_an_earlier_site_in_config_order(tmp_path):
    # b waits on a, the site placed first each day: still first ready the working day after.
    plan = plan_of(tmp_path, "states", wbs={"a": wbs_md(leaf(1, est="3h", h=4)),
                                            "b": wbs_md(leaf(1, est="1h", h=4, depends_on="[a:Issue-1]"))})
    assert placed(plan, "a", "Issue-1")["days"] == ["2026-01-19"]
    assert placed(plan, "b", "Issue-1")["days"] == ["2026-01-20"]


def test_no_leaf_is_waits_on_other_site(tmp_path):
    assert "waits_on_other_site" not in planner.UNPLANNED_REASONS
    for subject in invariant_subjects():
        plan, _ = subject_plan(tmp_path, subject)
        assert all(entry["reason"] != "waits_on_other_site" for site in plan.sites for entry in site["unplanned"])


def test_cross_site_target_without_a_page_is_waits_on_unresolved(tmp_path):
    # The planner holds only on a site it plans; an open outcome naming a site with no
    # page is work it cannot see, whatever the build passes it: never a crash.
    from dataclasses import replace

    result, portfolio, config = built(install(tmp_path, "cross_site"))
    result = replace(result, pages={name: page for name, page in result.pages.items() if name != "b"})
    plan = plan_week(result, portfolio, config)
    assert plan.status == "planned" and [site["name"] for site in plan.sites] == ["a"]
    reasons = reasons_of(plan, "a")
    assert reasons["Issue-1"] == reasons["Issue-8"] == "waits_on_unresolved"  # a leaf target, a row target
    assert placed(plan, "a", "Issue-2")["days"][0] == "2026-01-19"  # a finished target stays satisfied


def test_cross_site_target_whose_render_failed_is_waits_on_unresolved(tmp_path, monkeypatch):
    # Through the build: b's page fails to render, so b is not built and a's entries on it are unresolved.
    from test_crosssite import force_render

    force_render(monkeypatch, lambda site, nth: site == "b")
    plan = plan_week(*built(install(tmp_path, "cross_site")))
    assert plan.status == "planned" and [site["name"] for site in plan.sites] == ["a"]
    reasons = reasons_of(plan, "a")
    assert reasons["Issue-1"] == reasons["Issue-8"] == "waits_on_unresolved"


@pytest.mark.parametrize("base", ["cross_site", "cross_site_cycle"])
def test_cross_site_fixtures_have_spare_capacity(tmp_path, base):
    result, portfolio, config = built(install(tmp_path, base))
    plan = plan_week(result, portfolio, config)
    assert all(day["ran_out"] == [] for day in plan.days)
    assert set(intended_shares(portfolio, config.as_of).shares) >= {site["name"] for site in plan.sites} == {"a", "b"}


def open_keys(copy, site):
    result, _, _ = built(copy)
    return {found.key for found in result.inputs[site].data.leaves if found.open}


def test_fixture_names_cover_every_installation():
    found = {path.parent.name for path in FIXTURES.glob("*/gantry.toml")}
    assert set(FIXTURE_NAMES) == found
    assert len(found) == len(FIXTURE_NAMES) == 13


def fixture_facts(tmp_path, base):
    """(config order of sites, {site: [(leaf, rate, agent minutes)] of open leaves},
    the as-of day's intended shares or None) from the built inputs, not the plan."""
    from gantry.attention import leaf_attention, mapping_in_use

    result, portfolio, config = built(install(tmp_path, base))
    mapping = mapping_in_use(portfolio)
    sites = {}
    for site in result.sites:
        data = result.inputs[site.name].data
        sites[site.name] = [
            (found, leaf_attention(found, data, mapping).rate,
             planner._leaf_minutes((found.estimate.lo_h + found.estimate.hi_h) / 2))
            for found in data.leaves if found.open]
    shares = intended_shares(portfolio, config.as_of)
    return [site.name for site in result.sites], sites, None if shares is None else shares.shares


def test_new_fixtures_state_their_premises(tmp_path):
    from gantry.priority import rank

    order, sites, shares = fixture_facts(tmp_path, "two_sites")
    assert shares == {"a": 50, "b": 50}
    for name in ("a", "b"):
        assert all(rate == 1.0 for _, rate, _ in sites[name])
        assert all(rank(found.priority) != 0 and found.state == "pending" and not found.deps
                   for found, _, _ in sites[name])
        assert sum(minutes for _, _, minutes in sites[name]) == 60 * 60

    order, sites, shares = fixture_facts(tmp_path, "unequal_rates")
    assert sorted(rate for _, rate, _ in sites["a"] if rate) == [1.0, 1.0]
    assert sorted({rate for _, rate, _ in sites["b"] if rate}) == [0.1]
    assert [sum(rate == 0 for _, rate, _ in sites[name]) for name in ("a", "b")] == [9, 9]

    order, sites, shares = fixture_facts(tmp_path, "late_ready")
    (c_leaf, _, _), = sites["c"]
    for found, _, _ in sites["b"]:
        assert [(o.status, o.target_site, o.target_key) for o in found.foreign_targets] == [
            ("open", "c", c_leaf.key)]
    assert "c" not in shares

    order, sites, shares = fixture_facts(tmp_path, "no_share")
    assert order[0] == "c"
    assert [(rate, minutes) for _, rate, minutes in sites["a"]] == [(1.0, 720), (0.0, 30)]
    assert all(shares.get(name, 0) == 0 for name in ("c", "d"))

    order, sites, shares = fixture_facts(tmp_path, "p0")
    found, rate, minutes = next(entry for entry in sites["b"] if entry[0].key == "Issue-1")
    assert rank(found.priority) == 0 and planner._operator(minutes, planner._ppm(rate)) == 30


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
    """The invariants of the first acceptance criterion; returns (the number
    of blocks, the number of blocks holding leaves of two tiers)."""
    hours, lanes, block_min, start_cost = file_capacity(portfolio_path)
    rate = {(entry["site"], entry["key"]): entry["rate"] for entry in plan.leaves}
    tier = {(entry["site"], entry["key"]): entry["tier"] for entry in plan.leaves}
    two_tiers = 0
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
        assert all(a != b for a, b in zip(sites, sites[1:])), "no two consecutive blocks of a day are on one site"
        for block in day["blocks"]:
            blocks += 1
            assert block["leaves"], "a block that could hold no leaf is not opened"
            assert {entry["site"] for entry in block["leaves"]} == {block["site"]}, "no block mixes sites"
            assert block["start_cost_hours"] == pytest.approx(start_cost)
            assert block["length_hours"] >= block_min - EPS
            operator = sum(entry["operator_hours"] for entry in block["leaves"])
            # One start cost a block, merged or not: its length is the start cost plus its work, or block_min.
            assert block["length_hours"] == pytest.approx(max(block_min, operator + block["start_cost_hours"]))
            two_tiers += len({tier[entry["site"], entry["key"]] for entry in block["leaves"]}) > 1
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
    return blocks, two_tiers


def invariant_subjects():
    """Every installation under tests/fixtures/week_plan/, and the demo build."""
    return sorted(path.parent.name for path in FIXTURES.glob("*/gantry.toml")) + ["demo"]


def subject_plan(tmp_path, subject):
    """(plan, the portfolio file's path) of one invariant subject."""
    if subject == "demo":
        return plan_week(*with_portfolio(sample_build(tmp_path))), tmp_path / "samples" / "portfolio.md"
    copy = install(tmp_path, subject)
    return plan_week(*built(copy)), copy / "portfolio.md"


@pytest.mark.parametrize("subject", invariant_subjects())
def test_invariants_hold(tmp_path, subject):
    plan, path = subject_plan(tmp_path, subject)
    check_invariants(plan, path)


def test_invariant_subjects(tmp_path):
    subjects = invariant_subjects()
    assert set(FIXTURE_NAMES) | {"demo"} <= set(subjects)
    with_block, two_tiers = [], []
    for subject in subjects:
        blocks, mixed = check_invariants(*subject_plan(tmp_path, subject))
        if blocks > 0:
            with_block.append(subject)
        if mixed:
            two_tiers.append(subject)
    assert len(with_block) >= 3, with_block
    assert two_tiers, "some subject has a block holding leaves of two tiers"


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


def test_equal_deficit_goes_to_config_order(tmp_path):
    plan = plan_of(tmp_path, "states", wbs={
        "a": wbs_md(leaf(1, priority="P2", est="1h", h=1)),
        "b": wbs_md(leaf(1, priority="P2", est="1h", h=1)),
    })
    assert [block["site"] for block in plan.days[0]["blocks"]] == ["a", "b"]


def test_block_holds_its_sites_fitting_leaves(tmp_path):
    # b's leaf is more urgent than a's second leaf, yet a's block holds both of a's leaves.
    plan = plan_of(tmp_path, "states", wbs={
        "a": wbs_md(leaf(1, priority="P1", est="1h", h=1), leaf(2, priority="P3", est="1h", h=1)),
        "b": wbs_md(leaf(1, priority="P2", est="1h", h=1)),
    })
    first = plan.days[0]
    assert [(block["site"], [e["key"] for e in block["leaves"]]) for block in first["blocks"]] == [
        ("a", ["Issue-1", "Issue-2"]), ("b", ["Issue-1"])]


def test_lanes_row_by_lane_deficit(tmp_path):
    # Each placement goes to the site with the largest lane deficit, ties to config order:
    # a's first leaf, then b (deficit 30), then a and b again at 0 apiece.
    plan = plan_of(tmp_path, "states", wbs={
        "a": wbs_md(leaf(1, priority="P2", est="1h", h=0), leaf(2, priority="P4", est="1h", h=0)),
        "b": wbs_md(leaf(1, priority="P3", est="1h", h=0), leaf(2, priority="P1", est="1h", h=0)),
    })
    assert row_keys(plan.days[0]) == [("a", "Issue-1"), ("b", "Issue-2"), ("a", "Issue-2"), ("b", "Issue-1")]
    assert plan.days[0]["blocks"] == []
    # On equal deficits the sites go in config order.
    plan = plan_of(tmp_path, "states", wbs={
        "a": wbs_md(leaf(1, priority="P2", est="1h", h=0)),
        "b": wbs_md(leaf(1, priority="P2", est="1h", h=0)),
    })
    assert row_keys(plan.days[0]) == [("a", "Issue-1"), ("b", "Issue-1")]


def test_allocation_rule_text(tmp_path):
    plan = plan_of(tmp_path, "states")
    assert plan.allocation_rule == "fair share by intended shares"
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
    assert reasons_of(plan, "a") == {
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


# --- tiers, each day's shares, notes and the P0 list (#37) ---------------------------------------


def site_record(plan, name):
    return next(site for site in plan.sites if site["name"] == name)


def two_sites_portfolio(*allocations, hours=6, lanes=2, start_cost=0.25):
    """two_sites/' capacity with the given allocation tables in place of its own."""
    return portfolio_md(hours=hours, lanes=lanes, start_cost=start_cost, tail="".join(allocations))


def test_leaf_tier_values(tmp_path):
    plan = plan_of(tmp_path, "states")
    assert [placed(plan, "a", key)["tier"] for key in ("Issue-1", "Issue-2", "Issue-3")] == ["started"] * 3
    assert placed(plan, "b", "Issue-2")["tier"] == "fair_share"
    plan = plan_of(tmp_path, "p0")
    assert [placed(plan, "a", key)["tier"] for key in ("Issue-1", "Issue-2")] == ["p0", "p0"]
    assert planner.TIERS == ("p0", "started", "fair_share")
    for base in FIXTURE_NAMES:
        assert {entry["tier"] for entry in plan_of(tmp_path, base).leaves} <= set(planner.TIERS), base


def test_continues_leaf_keeps_fair_share_on_day_two(tmp_path):
    plan = plan_of(tmp_path, "continues")
    entry = placed(plan, "alpha", "Issue-1")
    assert entry["days"][:2] == plan.horizon_days[:2]
    assert entry["tier"] == "fair_share"


def p0_cases(tmp_path):
    """(plan, {site: the worksite page's notes.p0 or []}) of p0/, cross_site/ and the demo."""
    from gantry.views import worksite

    for base in ("p0", "cross_site", "demo"):
        result, portfolio, config = (with_portfolio(sample_build(tmp_path)) if base == "demo"
                                     else built(install(tmp_path, base)))
        pages = {}
        for name in result.pages:
            inputs = result.inputs[name]
            pages[name] = worksite.page_model(name, inputs.data, default_unit_hours=config.default_unit_hours,
                                              inputs=inputs)["notes"]["p0"] or []
        yield plan_week(result, portfolio, config), pages


def test_p0_list_matches_worksite_page(tmp_path):
    with_p0 = 0
    for plan, pages in p0_cases(tmp_path):
        assert {site["name"] for site in plan.sites} == set(pages)
        for site in plan.sites:
            assert [entry["key"] for entry in site["p0"]] == pages[site["name"]], site["name"]
            with_p0 += bool(site["p0"])
    assert with_p0 >= 2


def test_p0_leaf_held_by_other_site(tmp_path):
    entries = {e["key"]: e for e in site_record(plan_of(tmp_path, "p0"), "a")["p0"]}
    assert entries["Issue-3"] == {"key": "Issue-3", "held_by": ["b:Issue-3"]}
    assert entries["Issue-1"]["held_by"] == entries["Issue-2"]["held_by"] == []
    cross = {e["key"]: e for e in site_record(plan_of(tmp_path, "cross_site"), "a")["p0"]}
    assert cross["Issue-12"]["held_by"] == ["b:Issue-8"]
    # An absent and an unresolved outcome hold the leaf too, in depends_on order.
    text = (FIXTURES / "p0" / "a" / "WBS.md").read_bytes().decode("utf-8").replace(
        "depends_on: [b:Issue-3]", "depends_on: [zz:Issue-1, b:Issue-99, b:Issue-3]")
    entries = {e["key"]: e for e in site_record(plan_of(tmp_path, "p0", wbs={"a": text}), "a")["p0"]}
    assert entries["Issue-3"]["held_by"] == ["zz:Issue-1", "b:Issue-99", "b:Issue-3"]


def shares_of(plan, name):
    return [entry["share"] for entry in site_record(plan, name)["shares"]]


def test_day_shares_recorded(tmp_path):
    plan = plan_of(tmp_path, "two_sites")
    days = plan.horizon_days
    plan = plan_of(tmp_path, "two_sites", portfolio=dated_portfolio(days))
    assert [entry["date"] for entry in site_record(plan, "a")["shares"]] == days
    assert shares_of(plan, "a") == [50.0, 50.0] + [75.0] * 8
    assert shares_of(plan, "b") == [50.0, 50.0] + [25.0] * 8
    assert plan.notes == []


def test_shareless_site_is_marked(tmp_path):
    plan = plan_of(tmp_path, "no_share")
    for name in ("c", "d"):
        site = site_record(plan, name)
        assert site["share_note"] == planner.SHARE_NOTE
        assert site["shareless_days"] == plan.horizon_days
        assert shares_of(plan, name) == [None] * 10
    a = site_record(plan, "a")
    assert (shares_of(plan, "a"), a["shareless_days"], a["share_note"]) == ([100.0] * 10, [], None)


def test_no_allocation_notes_and_equal_shares(tmp_path):
    plan = plan_of(tmp_path, "no_allocation")
    assert plan.notes == [
        {"kind": "no_allocation", "date": day, "projects": [],
         "text": f"no intended shares apply on {day}; every site planned with equal shares"}
        for day in plan.horizon_days]
    for site in plan.sites:
        assert shares_of(plan, site["name"]) == [50.0] * 10
        assert (site["shareless_days"], site["share_note"]) == ([], None)


def test_shareless_days_only_while_an_allocation_applies(tmp_path):
    days = plan_of(tmp_path, "no_allocation").horizon_days
    plan = plan_of(tmp_path, "no_allocation", portfolio=two_sites_portfolio(allocation(days[2], a=100)))
    b = site_record(plan, "b")
    assert b["shareless_days"] == days[2:]
    assert b["share_note"] == planner.SHARE_NOTE
    assert shares_of(plan, "b") == [50.0, 50.0] + [None] * 8
    assert [(note["kind"], note["date"]) for note in plan.notes] == [("no_allocation", day) for day in days[:2]]


def test_unbuilt_share_dropped_and_rest_scaled(tmp_path):
    plan = plan_of(tmp_path, "share_unbuilt")
    days = plan.horizon_days
    early = [note for note in plan.notes if note["date"] in days[:5]]
    assert early == [{"kind": "shares_dropped", "date": day, "projects": ["zz"],
                      "text": f"shares dropped on {day} for projects that are not built sites: zz"}
                     for day in days[:5]]
    assert shares_of(plan, "a")[:5] == [float(Fraction(100, 3))] * 5
    assert shares_of(plan, "b")[:5] == [float(Fraction(200, 3))] * 5


def test_allocation_of_only_unbuilt_sites_is_a_no_allocation_day(tmp_path):
    plan = plan_of(tmp_path, "share_unbuilt")
    days = plan.horizon_days
    late = [(note["kind"], note["date"], note["projects"]) for note in plan.notes if note["date"] in days[5:]]
    assert late == [entry for day in days[5:]
                    for entry in (("shares_dropped", day, ["zz", "yy"]), ("no_allocation", day, []))]
    assert plan.notes[5]["text"] == f"shares dropped on {days[5]} for projects that are not built sites: zz, yy"
    for name in ("a", "b"):
        assert shares_of(plan, name)[5:] == [50.0] * 5
        assert site_record(plan, name)["shareless_days"] == []


def test_all_zero_remaining_shares_is_a_no_allocation_day(tmp_path):
    # A valid allocation sums to 100, so every built site at 0 means an unbuilt project holds the rest.
    plan = plan_of(tmp_path, "two_sites", portfolio=two_sites_portfolio(allocation("2026-01-05", a=0, b=0, zz=100)))
    assert [(note["kind"], note["projects"]) for note in plan.notes[:2]] == [
        ("shares_dropped", ["zz"]), ("no_allocation", [])]
    assert len(plan.notes) == 20
    for name in ("a", "b"):
        assert shares_of(plan, name) == [50.0] * 10
        assert site_record(plan, name)["shareless_days"] == []
    # The limb with nothing dropped, which no valid file reaches: straight through _day_shares.
    from types import SimpleNamespace

    from gantry.portfolio_file import Allocation

    portfolio = SimpleNamespace(allocations=(Allocation(date(2026, 1, 5), {"a": 0, "b": 0}, None),))
    shares, shareless, notes = planner._day_shares(portfolio, date(2026, 1, 19), ["a", "b"])
    assert [note["kind"] for note in notes] == ["no_allocation"]
    assert (shares, shareless) == ({"a": 0.5, "b": 0.5}, [])


def test_site_with_no_page_is_not_a_share_site(tmp_path):
    plan = plan_of(tmp_path, "two_sites", wbs={"b": DELETE})
    assert [site["name"] for site in plan.sites] == ["a"]
    assert {(note["kind"], tuple(note["projects"])) for note in plan.notes} == {("shares_dropped", ("b",))}
    assert shares_of(plan, "a") == [100.0] * 10


# --- priority tiers and merged blocks (#37) ------------------------------------------------------


def block_sites(day):
    return [block["site"] for block in day["blocks"]]


def blocks_of(day):
    return [(block["site"], [entry["key"] for entry in block["leaves"]]) for block in day["blocks"]]


def test_states_started_blocks_site_by_site(tmp_path):
    plan = plan_of(tmp_path, "states")
    assert blocks_of(plan.days[0]) == [("a", ["Issue-1", "Issue-2", "Issue-3"]), ("b", ["Issue-1"])]


def test_started_leaves_go_before_other_leaves_of_their_site(tmp_path):
    plan = plan_of(tmp_path, "states", wbs={"a": wbs_md(
        leaf(1, priority="P1", est="1h", h=1), leaf(2, "~", priority="P3", est="1h", h=1),
        leaf(3, "!", priority="P4", est="1h", h=1))})
    assert placed_keys(plan, "a") == ["Issue-2", "Issue-3", "Issue-1"]
    assert [placed(plan, "a", key)["tier"] for key in ("Issue-2", "Issue-3", "Issue-1")] == [
        "started", "started", "fair_share"]


def test_started_blocks_go_to_more_urgent_site_first(tmp_path):
    plan = plan_of(tmp_path, "states", wbs={"a": wbs_md(leaf(1, "~", priority="P3", est="1h", h=1)),
                                            "b": wbs_md(leaf(1, "~", priority="P1", est="1h", h=1))})
    assert block_sites(plan.days[0]) == ["b", "a"]


def test_p0_blocks_first_in_config_order(tmp_path):
    plan = plan_of(tmp_path, "p0")
    first = plan.days[0]["blocks"]
    assert [block["site"] for block in first[:2]] == ["a", "b"]
    assert [placed(plan, block["site"], block["leaves"][0]["key"])["tier"] for block in first[:2]] == ["p0", "p0"]
    assert [entry["share"] for site in plan.sites for entry in site["shares"][:1]] == [90.0, 10.0]


def test_p0_local_blocker_planned_in_tier_one_before_it(tmp_path):
    plan = plan_of(tmp_path, "p0")
    first = plan.days[0]["blocks"][0]
    assert (first["site"], first["leaves"][0]["key"]) == ("a", "Issue-2")
    assert placed(plan, "a", "Issue-2")["tier"] == "p0"
    a_blocks = [block for block in plan.days[1]["blocks"] if block["site"] == "a"]
    assert a_blocks[0]["leaves"][0]["key"] == "Issue-1"


def test_same_site_next_block_merges(tmp_path):
    plan = plan_of(tmp_path, "states")
    b_blocks = [block for block in plan.days[1]["blocks"] if block["site"] == "b"]
    assert len(b_blocks) == 1
    assert [entry["key"] for entry in b_blocks[0]["leaves"]] == ["Issue-1", "Issue-2"]
    assert [placed(plan, "b", key)["tier"] for key in ("Issue-1", "Issue-2")] == ["started", "fair_share"]
    assert b_blocks[0]["start_cost_hours"] == 0.25


MAPPING_2 = "attention_mapping = [0, 0.1, 0.25, 0.5, 2.0]\n"
BINDING_TIER_CELLS = [
    # A tier-1 block cut by the day's attention, a lane free: 105 minutes hold 52 agent minutes at rate 2.
    ("tier 1 cut", "binds_lanes", 2, 2, {"alpha": wbs_md(leaf(1, priority="P0", est="6h", h=4))}, [ATTENTION], 1),
    # A p0 leaf then a fair_share leaf in one merged block, cut the same way: attention, once.
    ("merged cut", "binds_lanes", 2, 2, {"alpha": wbs_md(
        leaf(1, priority="P0", est="0.25h", h=4), leaf(2, est="6h", h=4))}, [ATTENTION], 1),
    # b's block is not open and 15 minutes are left, under block_min; a lane is free.
    ("not open, lane free", "states", 2, 2, {"a": wbs_md(leaf(1, priority="P0", est="0.75h", h=4)),
                                             "b": wbs_md(leaf(1, est="1h", h=4))}, [ATTENTION], 1),
    # The same with a's leaf taking the one lane: lanes only.
    ("not open, no lane", "states", 1.5, 1, {"a": wbs_md(leaf(1, priority="P0", est="6h", h=1)),
                                             "b": wbs_md(leaf(1, est="1h", h=4))}, [LANES], 1),
    # One operator minute left in the open block holds no agent minute at rate 2.
    ("one minute left", "binds_lanes", 2, 2, {"alpha": wbs_md(
        leaf(1, priority="P0", est="3.4667h", h=3), leaf(2, est="1h", h=4))}, [ATTENTION], 1),
]


@pytest.mark.parametrize(("case", "base", "hours", "lanes", "wbs", "ran", "blocks"), BINDING_TIER_CELLS,
                         ids=[cell[0] for cell in BINDING_TIER_CELLS])
def test_binding_pool_cells_with_tiers(tmp_path, case, base, hours, lanes, wbs, ran, blocks):
    plan = plan_of(tmp_path, base, portfolio=portfolio_md(hours=hours, lanes=lanes, more=MAPPING_2), wbs=wbs)
    first = plan.days[0]
    assert first["ran_out"] == ran
    assert len(first["blocks"]) == blocks
    if case == "merged cut":
        assert [placed(plan, "alpha", e["key"])["tier"] for e in first["blocks"][0]["leaves"]] == ["p0", "fair_share"]
    if case == "one minute left":
        assert round(first["left"]["attention_hours"] * 60) == 1 and block_keys(first) == ["Issue-1"]


# --- fair share between sites (#37) ---------------------------------------------------------------

DEFAULT_MINUTES = 120  # block_default_hours' default, 2 h


@pytest.fixture
def trace():
    """The planner's charge trace: one entry per charge, plus lift and flag
    entries. Plan through traced(), which passes it to plan_week."""
    return []


def traced(trace, copy):
    """plan_week of an installation, the trace holding only its charges."""
    args = built(copy)
    trace.clear()
    return plan_week(*args, trace=trace)


def mins(hours):
    return round(hours * 60)


def day_charges(day):
    """[(site, pool, minutes)] of a day in record order: each block's length, then its
    leaves' agent minutes, then the lanes row."""
    out = []
    for block in day["blocks"]:
        out.append((block["site"], ATTENTION, mins(block["length_hours"])))
        out += [(entry["site"], LANES, mins(entry["agent_hours"])) for entry in block["leaves"]]
    return out + [(entry["site"], LANES, mins(entry["agent_hours"])) for entry in day["lanes_row"]]


def replay(plan, users=None):
    """Each day's end {pool: {site: (charged, target)}}, from the plan's day records
    and each site's recorded shares, with the test's own exact accrual: a charge to a
    share site adds share x minutes / the shares of the sites users(day index) names
    (default every share site that day) to each of their targets. Charges to
    share-less sites move no share site's target."""
    names = [site["name"] for site in plan.sites]
    share = {site["name"]: [None if e["share"] is None else Fraction(e["share"]) / 100 for e in site["shares"]]
             for site in plan.sites}
    state = {pool: {name: [0, Fraction(0)] for name in names} for pool in planner.POOLS}
    out = []
    for i, day in enumerate(plan.days):
        can = users(i) if users else [name for name in names if share[name][i] is not None]
        for site, pool, minutes in day_charges(day):
            state[pool][site][0] += minutes
            if share[site][i] is None or not can:
                continue
            total = sum(share[user][i] for user in can)
            for user in can:
                state[pool][user][1] += minutes * share[user][i] / total
        out.append({pool: {name: tuple(state[pool][name]) for name in names} for pool in planner.POOLS})
    return out


def flags(found):
    return [entry for entry in found if entry.get("flag")]


def charges(found, **match):
    return [entry for entry in found if "pool" in entry and all(entry[k] == v for k, v in match.items())]


def three_sites(tmp_path, shares, y_leaves, mapping=None, z_leaf=None, x_leaves=None, trace=None, hours=6,
                start_cost=0.25):
    """two_sites/ with sites x, y and z in that config order, `hours` of attention a
    weekday: `shares` maps a site to its share from 2026-01-05."""
    copy = install(tmp_path, "two_sites", portfolio=two_sites_portfolio(
        allocation("2026-01-05", **shares), hours=hours, start_cost=start_cost), config=None)
    shutil.move(copy / "a", copy / "x")
    shutil.move(copy / "b", copy / "y")
    (copy / "z").mkdir()
    (copy / "devagent.toml").write_bytes(
        b"".join(f'[project.{n}]\ndevdoc_dir = "{n}"\n\n'.encode() for n in ("x", "y", "z")))
    (copy / "x" / "WBS.md").write_bytes(
        (x_leaves or wbs_md(*(leaf(n, est="3w", h=4) for n in (1, 2, 3)))).encode())
    (copy / "y" / "WBS.md").write_bytes(y_leaves.encode())
    (copy / "z" / "WBS.md").write_bytes((z_leaf or wbs_md(leaf(1, est="3w", h=1))).encode())
    if mapping is not None:
        text = (copy / "portfolio.md").read_bytes().decode().replace("[capacity]\n", f"[capacity]\n{mapping}")
        (copy / "portfolio.md").write_bytes(text.encode())
    return traced(trace, copy) if trace is not None else plan_week(*built(copy))


P0_CHAIN = wbs_md(leaf(1, priority="P0", est="1h", h=4),
                  *(leaf(n, priority="P0", est="1h", h=4, depends_on=f"[Issue-{n - 1}]") for n in range(2, 11)))
# y's 2 h P0 chain and two fair_share leaves: y's P0 work leaves its deficit far below x's,
# while z (60, rate 0.1) holds a deficit it cannot use once at its 6 agent hours. When x's
# block reaches its cap, x still has the largest deficit of the sites that fit.
LIFT_Y = wbs_md(leaf(1, priority="P0", est="2h", h=4),
                *(leaf(n, priority="P0", est="2h", h=4, depends_on=f"[Issue-{n - 1}]") for n in range(2, 11)),
                leaf(11, est="3w", h=4), leaf(12, est="3w", h=4))
SEVENTY_FIVE = two_sites_portfolio(allocation("2026-01-05", a=75, b=25))


def dated_portfolio(days):
    return two_sites_portfolio(allocation("2026-01-05", a=50, b=50), allocation(days[2], a=75, b=25))


def test_two_sites_within_one_block_of_shares(tmp_path):
    days = plan_of(tmp_path, "two_sites").horizon_days
    for portfolio in (None, SEVENTY_FIVE, dated_portfolio(days)):
        plan = plan_of(tmp_path, "two_sites", portfolio=portfolio)
        ends = replay(plan)
        for end in ends:
            for name in ("a", "b"):
                charged, target = end[ATTENTION][name]
                assert abs(charged - target) <= DEFAULT_MINUTES, (portfolio, end)
                charged, target = end[LANES][name]
                assert abs(charged - target) <= 360, (portfolio, end)
        for site in plan.sites:
            assert site["attention_hours_target"] == float(ends[-1][ATTENTION][site["name"]][1] / 60)
            assert site["lane_hours_target"] == float(ends[-1][LANES][site["name"]][1] / 60)
            assert mins(site["attention_hours_charged"]) == ends[-1][ATTENTION][site["name"]][0]


def test_shares_move_both_splits(tmp_path):
    even = {site["name"]: site for site in plan_of(tmp_path, "two_sites").sites}
    more = {site["name"]: site for site in plan_of(tmp_path, "two_sites", portfolio=SEVENTY_FIVE).sites}
    for key in ("attention_hours_charged", "lane_hours_charged"):
        assert more["a"][key] > even["a"][key] and more["b"][key] < even["b"][key], key


def test_block_extends_minute_by_minute(tmp_path, trace):
    plan = traced(trace, install(tmp_path, "two_sites", portfolio=SEVENTY_FIVE))
    share = {"a": Fraction(3, 4), "b": Fraction(1, 4)}
    state = {"a": [0, Fraction(0)], "b": [0, Fraction(0)]}
    seen, checked = {}, 0
    later = [(i, k, entry["key"]) for i, day in enumerate(plan.days) for k, block in enumerate(day["blocks"])
             for entry in block["leaves"]]
    for i, day in enumerate(plan.days):
        for k, block in enumerate(day["blocks"]):
            length = mins(block["length_hours"])
            for name in state:
                state[name][1] += length * share[name]
            state[block["site"]][0] += length
            for entry in block["leaves"]:
                key = block["site"], entry["key"]
                seen[key] = seen.get(key, 0) + mins(entry["agent_hours"])
            if k + 1 == len(day["blocks"]) or day["blocks"][k + 1]["site"] == block["site"]:
                continue
            charged, target = state[block["site"]]
            share_of = share[block["site"]]
            assert charged - target <= DEFAULT_MINUTES
            assert charged + 1 - (target + share_of) > DEFAULT_MINUTES, (day["date"], block)
            last = block["leaves"][-1]
            done = seen[block["site"], last["key"]] == mins(placed(plan, block["site"], last["key"])["agent_hours"])
            again = any((j, n) > (i, k) and key == last["key"] for j, n, key in later
                        if plan.days[j]["blocks"][n]["site"] == block["site"])
            beyond = placed(plan, block["site"], last["key"])["hours_after_horizon"] > 0
            assert done or again or beyond, (day["date"], block)
            checked += 1
    assert checked >= 1
    assert [(b["site"], mins(b["length_hours"])) for b in plan.days[1]["blocks"]] == [("b", 280), ("a", 80)]
    assert flags(trace) == []


def test_two_sites_few_long_blocks(tmp_path):
    plan = plan_of(tmp_path, "two_sites")
    for day in plan.days:
        assert len(set(block_sites(day))) == len(block_sites(day)), day["date"]
    blocks = [block for day in plan.days for block in day["blocks"]]
    assert len(blocks) <= 11
    assert max(block["length_hours"] for block in blocks) > 2.0


def test_unequal_rates_attention_bound_and_lanes_row(tmp_path, trace):
    plan = traced(trace, install(tmp_path, "unequal_rates"))
    ends = replay(plan)
    for end in ends:
        for name in ("a", "b"):
            charged, target = end[ATTENTION][name]
            assert abs(charged - target) <= DEFAULT_MINUTES, end
    assert all(day["lanes_row"] for day in plan.days)
    rows = charges(trace, pool=LANES, phase=6)
    assert rows
    for entry in rows:
        deficit = {name: target - charged for name, (charged, target) in entry["before"].items()}
        best = max(deficit[name] for name in entry["users"])
        assert entry["site"] == next(name for name in entry["users"] if deficit[name] == best), entry
    for site in plan.sites:
        for pool, key in ((ATTENTION, "attention_hours"), (LANES, "lane_hours")):
            charged, target = ends[-1][pool][site["name"]]
            assert (mins(site[f"{key}_charged"]), site[f"{key}_target"]) == (charged, float(target / 60))


def test_late_ready_no_burst(tmp_path, trace):
    plan = traced(trace, install(tmp_path, "late_ready"))
    days = plan.horizon_days
    for day in plan.days[:3]:
        assert block_sites(day) == ["a"]
        assert row_keys(day) == [("c", "Issue-1")] and day["lanes_row"][0]["agent_hours"] == 6.0
    assert placed(plan, "c", "Issue-1")["days"] == days[:3]
    early = [entry for entry in charges(trace) if entry["date"] in days[:3]]
    assert early and all("b" not in entry["users"] for entry in early)
    ends = replay(plan, users=lambda i: ["a"] if i < 3 else ["a", "b"])
    for site in plan.sites[:2]:
        assert site["attention_hours_target"] == float(ends[-1][ATTENTION][site["name"]][1] / 60)
        assert site["lane_hours_target"] == float(ends[-1][LANES][site["name"]][1] / 60)
    base = ends[2][ATTENTION]
    for end in ends[3:]:
        since = {name: end[ATTENTION][name][0] - base[name][0] for name in ("a", "b")}
        assert abs(since["b"] - (since["a"] + since["b"]) / 2) <= DEFAULT_MINUTES, end
    assert any(block_sites(day) != ["b"] for day in plan.days[3:6])


def test_shareless_sites_take_only_the_remainder(tmp_path, trace):
    plan = traced(trace, install(tmp_path, "no_share"))
    for day in plan.days[:2]:
        assert day["left"] == {"attention_hours": 0.0, "lane_hours": 0.0}
        assert {entry["site"] for entry in day["lanes_row"]} | set(block_sites(day)) == {"a"}
    third = plan.days[2]
    assert third["blocks"][0]["site"] == "a" and mins(third["blocks"][0]["length_hours"]) == 60
    assert sum(mins(e["operator_hours"]) for e in third["blocks"][0]["leaves"]) + 15 == 45
    assert third["blocks"][1]["site"] == "c"
    on_day = [entry for entry in trace if entry.get("date") == third["date"] and "pool" in entry]
    padding = next(n for n, entry in enumerate(on_day) if entry["what"] == "padding")
    assert (on_day[padding]["site"], on_day[padding]["minutes"]) == ("a", 15)
    assert on_day[padding + 1]["site"] == "c" and on_day[padding + 1]["what"] == "start"
    assert {"c", "d"} <= {entry["site"] for day in plan.days[2:] for entry in day["lanes_row"]} | {
        site for day in plan.days[2:] for site in block_sites(day)}
    attention = {"c": 0, "d": 0}
    worst = 0
    for day in plan.days:
        for site, pool, minutes in day_charges(day):
            if pool == ATTENTION and site in attention:
                attention[site] += minutes
        worst = max(worst, abs(attention["c"] - (attention["c"] + attention["d"]) / 2))
    assert worst <= DEFAULT_MINUTES, worst


def test_phase_6_lanes_before_shareless_blocks(tmp_path):
    plan = plan_of(tmp_path, "no_share", wbs={"a": wbs_md(leaf(1, est="2h", h=4), leaf(2, est="3w", h=0))})
    first = plan.days[0]
    assert row_keys(first) == [("a", "Issue-2")] and first["lanes_row"][0]["agent_hours"] == 4.0
    for name in ("c", "d"):
        assert placed_keys(plan, name) == []


def test_shareless_started_leaf_accrues_to_both_classes(tmp_path, trace):
    plan = traced(trace, install(tmp_path, "no_share", wbs={"c": wbs_md(leaf(1, "~", est="3h", h=4))}))
    found = charges(trace, date=plan.horizon_days[0], site="c", pool=ATTENTION, phase=3)
    assert sum(entry["minutes"] for entry in found) == 195
    gained = {name: sum(entry["after"][name][1] - entry["before"][name][1] for entry in found)
              for name in ("a", "c", "d")}
    assert gained == {"a": 195, "c": Fraction(195, 2), "d": Fraction(195, 2)}
    # d with no ready leaf cannot use attention: c's share-less half goes to c alone.
    plan = traced(trace, install(tmp_path, "no_share", wbs={"c": wbs_md(leaf(1, "~", est="3h", h=4)),
                                                           "d": wbs_md(leaf(1, "?", est="3w", h=4))}))
    found = charges(trace, date=plan.horizon_days[0], site="c", pool=ATTENTION, phase=3)
    gained = {name: sum(entry["after"][name][1] - entry["before"][name][1] for entry in found)
              for name in ("a", "c", "d")}
    assert gained == {"a": 195, "c": 195, "d": 0}


def test_p0_planned_first_though_share_used_up(tmp_path):
    plan = plan_of(tmp_path, "p0")
    charged, target = replay(plan)[0][ATTENTION]["b"]
    assert target - charged < 0
    second = plan.days[1]
    held = next(n for n, block in enumerate(second["blocks"]) if "Issue-4" in [e["key"] for e in block["leaves"]]
                and block["site"] == "b")
    fair = [n for n, block in enumerate(second["blocks"]) for e in block["leaves"]
            if placed(plan, block["site"], e["key"])["tier"] == "fair_share"]
    assert fair and held < min(fair)


def test_short_p0_block_charged_block_min_alone(tmp_path):
    plan = plan_of(tmp_path, "p0")
    first = plan.days[0]
    assert block_sites(first) == ["a", "b", "a"]
    b = first["blocks"][1]
    assert [e["key"] for e in b["leaves"]] == ["Issue-1"] and b["length_hours"] == 1.0
    assert all(block["start_cost_hours"] == 0.25 for block in first["blocks"])


def test_short_p0_block_merges_with_its_sites_next_block(tmp_path):
    text = (FIXTURES / "p0" / "portfolio.md").read_bytes().decode().replace("a = 90, b = 10", "a = 10, b = 90")
    plan = plan_of(tmp_path, "p0", portfolio=text)
    b = [block for block in plan.days[0]["blocks"] if block["site"] == "b"]
    assert len(b) == 1
    keys = [e["key"] for e in b[0]["leaves"]]
    assert keys[0] == "Issue-1" and placed(plan, "b", keys[1])["tier"] == "fair_share"


def test_leaf_split_and_resumed_same_day_counts_once(tmp_path, trace):
    # a's Issue-1 has no estimate (12 h at the default unit), so the day's count can see it twice.
    text = (FIXTURES / "two_sites" / "a" / "WBS.md").read_bytes().decode().replace(
        "{issue: Issue-1, est: 12h, h: 4}", "{issue: Issue-1, h: 4}")
    plan = traced(trace, install(tmp_path, "two_sites", wbs={"a": text, "b": wbs_md(leaf(1, est="0.5h", h=4))},
                                 config={"default_unit_hours": "12"}))
    first = plan.days[0]
    assert block_sites(first) == ["a", "b", "a"]
    entry = placed(plan, "a", "Issue-1")
    assert entry["days"].count(first["date"]) == 1
    on_day = [e["agent_hours"] for block in first["blocks"] for e in block["leaves"] if e["key"] == "Issue-1"
              and block["site"] == "a"]
    assert len(on_day) == 2 and sum(on_day) <= 6
    assert entry["unestimated"] and first["unestimated"] == 1
    day = [e for e in charges(trace, date=first["date"])]
    finished = max(n for n, e in enumerate(day) if e["site"] == "b" and e["what"] == "work")
    assert all("b" in e["users"] for e in day[:finished])
    assert all("b" not in e["users"] for e in day[finished + 1:])


def test_three_z_day_five(tmp_path):
    # Hand-derived from the model lead: y's P0 block, z's, then x's long block, not y z x y.
    plan = three_sites(tmp_path, {"z": 40, "x": 30, "y": 30}, P0_CHAIN)
    assert block_sites(plan.days[4]) == ["y", "z", "x"]
    assert plan.days[4]["blocks"][2]["length_hours"] > 2.0


@pytest.mark.parametrize("mapping", [None, MAPPING_2], ids=["default mapping", "mapping 2.0"])
def test_three_sites_continue_rule(tmp_path, trace, mapping):
    plan = three_sites(tmp_path, {"z": 60, "x": 20, "y": 20}, LIFT_Y, mapping=mapping, trace=trace, hours=10)
    lifts = [entry for entry in trace if entry.get("lift")]
    assert lifts  # the case cannot pass vacuously
    for lift in lifts:
        day = next(d for d in plan.days if d["date"] == lift["date"])
        ran = []
        for e in trace[trace.index(lift) + 1:]:
            if "pool" not in e or e["site"] != lift["site"] or e["what"] != "work":
                break
            ran.append(e)
        operator = sum(e["minutes"] for e in ran if e["pool"] == ATTENTION)
        agent = sum(e["minutes"] for e in ran if e["pool"] == LANES)
        # The lifted block runs on, never ended by banning its site: at least one agent
        # minute and block_default more operator minutes, unless a pool of the day ends first.
        assert agent >= 1, (lift, ran)
        assert operator >= DEFAULT_MINUTES or (day["blocks"][-1]["site"] == lift["site"] and (
            day["left"]["lane_hours"] == 0 or day["left"]["attention_hours"] * 60 < 2)), (lift, ran)
    for day in plan.days:
        assert all(a != b for a, b in zip(block_sites(day), block_sites(day)[1:]))
        # A lifted pick continues its leaf in the same block: one entry, not two in a row.
        for block in day["blocks"]:
            keys = [e["key"] for e in block["leaves"]]
            assert all(a != b for a, b in zip(keys, keys[1:])), (day["date"], keys)


def test_tier_leaf_judged_before_its_tiers_lanes_row(tmp_path):
    # The P0 leaf finds a lane free but half an hour of attention, under block_min: attention
    # ran out for it, though the started rate-0 leaf then takes the one lane (#61's reading).
    plan = plan_of(tmp_path, "binds_lanes", portfolio=portfolio_md(hours=0.5, lanes=1),
                   wbs={"alpha": wbs_md(leaf(1, priority="P0", est="1h", h=4), leaf(2, "~", est="6h", h=0))})
    first = plan.days[0]
    assert (first["blocks"], row_keys(first)) == ([], [("alpha", "Issue-2")])
    assert first["ran_out"] == [ATTENTION]
    assert plan.binding["pool"] == ATTENTION


def test_rated_leaf_behind_an_earlier_tiers_row_waits_on_lanes(tmp_path):
    # A P0 rate-0 leaf takes the one lane in phase 2; the started leaf's block would fit the
    # attention, but no lane is free when its tier's turn comes: lanes alone ran out.
    plan = plan_of(tmp_path, "binds_lanes", portfolio=portfolio_md(hours=2, lanes=1),
                   wbs={"alpha": wbs_md(leaf(1, priority="P0", est="6h", h=0), leaf(2, "~", est="1h", h=4))})
    first = plan.days[0]
    assert (first["blocks"], row_keys(first)) == ([], [("alpha", "Issue-1")])
    assert first["ran_out"] == [LANES]


MAPPING_2_CAP = portfolio_md(hours=10, lanes=4, more=MAPPING_2)


def test_binding_pool_cells_under_fair_share(tmp_path):
    # A block closed by the cap whose site cannot open another that day, a lane free: attention.
    plan = plan_of(tmp_path, "two_sites", portfolio=two_sites_portfolio(
        allocation("2026-01-05", a=50, b=50), lanes=4).replace("[capacity]\n", "[capacity]\n" + MAPPING_2))
    first = plan.days[0]
    assert block_sites(first) == ["a", "b"] and first["left"]["lane_hours"] > 0
    assert first["ran_out"] == [ATTENTION]
    # A leaf cut by the cap and finished in its site's later block that day: nothing ran out.
    plan = plan_of(tmp_path, "two_sites", portfolio=MAPPING_2_CAP,
                   wbs={"a": wbs_md(leaf(1, est="3h", h=4)), "b": wbs_md(leaf(1, est="1h", h=4))})
    first = plan.days[0]
    assert block_sites(first) == ["a", "b", "a"]
    assert placed(plan, "a", "Issue-1")["days"] == [first["date"]]
    assert first["ran_out"] == []
    # A leaf cut by the cap, not by the day's attention, then the only lane runs out: lanes alone.
    plan = plan_of(tmp_path, "two_sites",
                   portfolio=portfolio_md(hours=6, lanes=1, tail=allocation("2026-01-05", a=50, b=50)),
                   wbs={"a": wbs_md(leaf(1, est="6h", h=4)), "b": wbs_md(leaf(1, est="2.25h", h=1))})
    first = plan.days[0]
    assert first["left"] == {"attention_hours": 1.0, "lane_hours": 0.0}
    assert first["ran_out"] == [LANES]


def test_unusable_share_is_not_accrued(tmp_path):
    plain = wbs_md(*(leaf(n, est="3w", h=4) for n in (1, 2, 3)))
    plan = three_sites(tmp_path, {"z": 60, "x": 20, "y": 20}, plain)
    assert sum(len(day["blocks"]) for day in plan.days) < 30
    total = sum(site["attention_hours_charged"] for site in plan.sites)
    z = site_record(plan, "z")
    assert z["attention_hours_target"] < 0.6 * total


def test_block_may_always_reach_block_default(tmp_path, trace):
    # x's started 30 h leaf leaves x and y over their targets while z (rate 0.1) holds a deficit
    # it cannot spend; a block whose site is past the bound when it opens still runs to
    # block_default, measured over the whole block.
    x = wbs_md(leaf(1, "~", est="30h", h=4), *(leaf(n, est="3w", h=4) for n in (2, 3, 4)))
    y = wbs_md(*(leaf(n, est="3w", h=4) for n in (1, 2, 3)))
    plan = three_sites(tmp_path, {"x": 34, "y": 33, "z": 33}, y, x_leaves=x,
                       z_leaf=wbs_md(leaf(1, est="3w", h=1)), hours=8, trace=trace)
    found = 0
    for day in plan.days:
        starts = charges(trace, date=day["date"], what="start")
        assert len(starts) == len(day["blocks"])
        for entry, block in zip(starts, day["blocks"]):
            charged, target = entry["before"][entry["site"]]
            if entry["phase"] != 5 or charged - target <= DEFAULT_MINUTES:
                continue
            found += 1
            if block is not day["blocks"][-1]:
                # Exactly block_default: no minute past it, since another site still fits.
                assert mins(block["length_hours"]) == DEFAULT_MINUTES, (day["date"], block["site"])
    assert found


def test_unusable_share_goes_to_sites_that_can(tmp_path):
    # z holds half the shares but no ready leaf: x and y split the day as two_sites/ at 50/50 does.
    plan = three_sites(tmp_path, {"z": 50, "x": 25, "y": 25}, wbs_md(*(leaf(n, est="12h", h=4) for n in (1, 2))),
                       x_leaves=wbs_md(*(leaf(n, est="12h", h=4) for n in (1, 2))),
                       z_leaf=wbs_md(leaf(1, "?", est="3w", h=4)))
    assert [(b["site"], mins(b["length_hours"])) for b in plan.days[0]["blocks"]] == [("x", 240), ("y", 120)]


def test_p0_tier_wins_over_started(tmp_path):
    plan = plan_of(tmp_path, "states", wbs={"a": wbs_md(leaf(1, "~", priority="P0", est="1h", h=1))})
    assert placed(plan, "a", "Issue-1")["tier"] == "p0"


def test_tier_blocks_before_its_rate_0_leaves(tmp_path):
    # One lane: the P0 leaf of rate above 0 takes it in phase 1 before the P0 rate-0 leaf's phase 2.
    plan = plan_of(tmp_path, "binds_lanes", portfolio=portfolio_md(hours=8, lanes=1), wbs={"alpha": wbs_md(
        leaf(1, priority="P0", est="6h", h=0), leaf(2, priority="P0", est="6h", h=4))})
    first = plan.days[0]
    assert block_keys(first) == ["Issue-2"] and first["lanes_row"] == []


def test_start_cost_at_block_default_still_fills_every_block(tmp_path):
    # A start cost of block_default leaves a block opened past the bound no allowance at all:
    # every block that opens still holds at least one agent minute.
    x = wbs_md(leaf(1, "~", est="30h", h=4), *(leaf(n, est="3w", h=4) for n in (2, 3, 4)))
    y = wbs_md(*(leaf(n, est="3w", h=4) for n in (1, 2, 3)))
    plan = three_sites(tmp_path, {"x": 34, "y": 33, "z": 33}, y, x_leaves=x,
                       z_leaf=wbs_md(leaf(1, est="3w", h=1)), hours=8, start_cost=2)
    blocks = [block for day in plan.days for block in day["blocks"]]
    assert blocks and all(block["leaves"] for block in blocks)


def test_rate_zero_work_does_not_cap_a_block(tmp_path, trace):
    # 285 minutes a day. a's block reaches its cap at 240 (block_default, then the bound);
    # the 45 left are under block_min, so b's rated leaf cannot open a block, while b's
    # rate-0 leaf has a lane: only rated work counts as another site that fits, so a's
    # block runs to the day's end on the "no other site fits" clause.
    plan = traced(trace, install(tmp_path, "two_sites", portfolio=two_sites_portfolio(
        allocation("2026-01-05", a=50, b=50), hours=4.75),
        wbs={"a": wbs_md(leaf(1, est="4.5h", h=4)), "b": wbs_md(leaf(1, est="3w", h=4), leaf(2, est="1h", h=0))}))
    first = plan.days[0]
    # The premise: b's rated leaf never fits on day 1 and its rate-0 leaf does.
    assert 285 - 240 < 60
    assert block_sites(first) == ["a"] and row_keys(first) == [("b", "Issue-2")]
    assert mins(first["blocks"][0]["length_hours"]) == 285 and first["left"]["attention_hours"] == 0
    assert {entry["date"] for entry in flags(trace)} == {first["date"]}
    assert not [entry for entry in trace if entry.get("lift")]


def test_equal_shares_byte_identical_under_hash_seeds(tmp_path):
    import os
    import subprocess
    import sys

    for base in ("share_unbuilt", "two_sites"):
        texts = set()
        for seed in ("1", "777", "31337"):
            folder = install(tmp_path, base)
            result = subprocess.run([sys.executable, "-m", "gantry", "build", "--config", str(folder / "gantry.toml")],
                                    cwd=ROOT, capture_output=True,
                                    env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONHASHSEED": seed})
            assert result.returncode == 0, result.stderr
            texts.add((folder / "out" / planner.PLAN_FILE).read_bytes())
        assert len(texts) == 1, base


# --- the priority property (#37) ----------------------------------------------------------------

NEVER = (float("inf"),)
PRIORITY_RE = re.compile(r"priority:\s*[^,}]*")


def first_placement(plan, site, key):
    """(day index, block number or blocks + 1 for the lanes row, position) of the leaf's
    first placement; NEVER, after every placement, when it is not placed."""
    for i, day in enumerate(plan.days):
        for block in day["blocks"]:
            for position, entry in enumerate(block["leaves"]):
                if (entry["site"], entry["key"]) == (site, key):
                    return i, block["number"], position
        for position, entry in enumerate(day["lanes_row"]):
            if (entry["site"], entry["key"]) == (site, key):
                return i, len(day["blocks"]) + 1, position
    return NEVER


def raised(text, line):
    """`text` with the leaf on 1-based `line` one priority step up; absent or invalid: P2."""
    from gantry.priority import is_valid, rank

    lines = text.split("\n")
    found = PRIORITY_RE.search(lines[line - 1])
    value = found.group(0).split(":", 1)[1].strip() if found else None
    step = f"P{rank(value) - 1}" if is_valid(value) else "P2"
    if found is None:
        lines[line - 1] = lines[line - 1].replace("{", f"{{priority: {step}, ", 1)
    else:
        lines[line - 1] = lines[line - 1][:found.start()] + f"priority: {step}" + lines[line - 1][found.end():]
    return "\n".join(lines)


def priority_subjects(result):
    """(site, key, line) of each open leaf of a built site with no open predecessor, local
    or across sites, whose own rank is not 0."""
    from gantry.priority import rank

    subjects = []
    for name in planner._built_sites(result):
        data = result.inputs[name].data
        open_keys = {found.key for found in data.leaves if found.open}
        waits = {after for before, after in data.links if before in open_keys}
        subjects += [(name, found.key, found.line) for found in data.leaves
                     if found.open and found.key not in waits and rank(found.priority) != 0
                     and not found.foreign_targets]
    return subjects


def priority_property(copy, build):
    """(subjects with a baseline placement, [(subject, before, after) moved later]) over every
    subject of the installation at `copy`; build() gives (build result, plan)."""
    result, before = build()
    placed_n, later = 0, []
    for site, key, line in priority_subjects(result):
        path = copy / site / "WBS.md"
        text = path.read_bytes().decode("utf-8")
        old = first_placement(before, site, key)
        placed_n += old != NEVER
        path.write_bytes(raised(text, line).encode("utf-8"))
        try:
            new = first_placement(build()[1], site, key)
        finally:
            path.write_bytes(text.encode("utf-8"))
        if new > old:
            later.append(((site, key), old, new))
    return placed_n, later


def fixture_build(copy):
    args = built(copy)
    return args[0], plan_week(*args)


def test_raising_priority_never_delays_first_placement(tmp_path):
    from helpers_config import copy_samples

    copy = copy_samples(tmp_path)

    def sample_build_and_plan():
        result = build_all(load_config(copy / "gantry.sample.toml"), samples=copy)
        return result, plan_week(*with_portfolio(result))

    placed_n, later = priority_property(copy, sample_build_and_plan)
    assert later == []
    assert placed_n >= 4, placed_n
    total = 0
    for base in FIXTURE_NAMES:
        copy = install(tmp_path, base)
        placed_n, later = priority_property(copy, lambda copy=copy: fixture_build(copy))
        assert later == [], base
        total += placed_n
    # The spike's per-installation counts over 12 fixtures sum to 71 (its "75" was a slip);
    # cross_site_cycle adds 2.
    assert total >= 73, total


def test_raising_priority_moves_a_tied_leaf_earlier(tmp_path):
    copy = install(tmp_path, "two_sites")
    result, before = fixture_build(copy)
    moved = 0
    for name in ("a", "b"):
        for found in [leaf for leaf in result.inputs[name].data.leaves if leaf.open][1:]:
            path = copy / name / "WBS.md"
            text = path.read_bytes().decode("utf-8")
            path.write_bytes(raised(text, found.line).encode("utf-8"))
            try:
                after = fixture_build(copy)[1]
            finally:
                path.write_bytes(text.encode("utf-8"))
            assert first_placement(after, name, found.key) < first_placement(before, name, found.key), found.key
            moved += 1
    assert moved >= 2


def test_raising_a_rate_zero_leaf_does_not_reorder_its_tiers_blocks(tmp_path):
    # Review counter-example (#37): a tier's blocks go by each site's most urgent leaf of rate
    # above 0 and its lanes row by its most urgent rate-0 leaf. Ordering both by any leaf of the
    # tier let raising a's rate-0 leaf put a's block first, whose leaf took the only lane.
    portfolio = portfolio_md(hours=6, lanes=1)
    b = wbs_md(leaf(1, "!", priority="P2", est="6h", h=3), leaf(2, "~", priority="P2", est="3w", h=4))
    found = {}
    for raise_it in (False, True):
        meta = {"priority": "P2"} if raise_it else {}
        a = wbs_md(leaf(1, "!", est="1h", h=0, **meta), leaf(2, "~", est="6h", h=2))
        plan = plan_of(tmp_path, "states", wbs={"a": a, "b": b}, portfolio=portfolio)
        found[raise_it] = first_placement(plan, "a", "Issue-1")
    assert found[False] != NEVER
    assert found[True] <= found[False], found


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
        "`unresolved_local`", *(f"`{kind}`" for kind in planner.PLAN_REPORT_KINDS),
        "first ready on the working day after", "never raises its blocker's priority",
        "local entry that names nothing is not a dependency", "cross-site blocker is itself `beyond_horizon`",
        planner.SHARE_NOTE, "no intended shares apply on", "one whole minute at a time", "exact fractions",
        "held by", "never two in a row", "accrues its whole share until it cannot",
        "judged when its tier's blocks have been planned",
    ]
    assert [w for w in wanted if w not in doc] == []
    # The interim rule is gone. Split, so this test is not itself a hit of the sweep's grep.
    absent = ["Until " + "#37", "most urgent site " + "first", "at most one block a day", "planned like any other"]
    assert [a for a in absent if a in doc] == []
    assert len(planner.UNPLANNED_REASONS) == 8 and len(NO_PLAN) == 5
    # The planner's rules are in its section, the file's in its own.
    the_planner = " ".join(doc_section(text, "## The planner").split())
    for topic in ("### Days and capacity", "### What each leaf costs", "### Blocks", "### A ready leaf that fits",
                  "### Order within a site", "### Priority tiers", "### Merged blocks", "### How long a block runs",
                  "### Fair share between sites", "### The lanes row", "### Share-less sites",
                  "### Started in this plan", "### States",
                  "### Dependencies within a site", "### Dependencies across sites",
                  "### Local entries that name nothing", "### Cycles across sites", "### Unplanned leaves",
                  "### Past the horizon",
                  "### The binding pool", "### When there is no plan"):
        assert topic in the_planner, topic
    assert "### Which site goes next" not in the_planner
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
    planned, no_plan, report, p0_entry, note, shareless = doc_json_blocks(section)
    # The no_plan form is a whole file, byte for byte.
    assert no_plan == plan_to_json(plan_of(tmp_path, "binds_lanes", config={"portfolio_file": None}))
    assert json.loads(no_plan)["status"] == "no_plan"
    # The planned example is binds_lanes' file with days, leaves, sites and notes, and the
    # site's shares, cut to their first item.
    document = json.loads(plan_to_json(plan_of(tmp_path, "binds_lanes")))
    for name in ("days", "leaves", "sites", "notes"):
        document[name] = document[name][:1]
    document["sites"][0]["shares"] = document["sites"][0]["shares"][:1]
    assert json.loads(planned) == document
    assert planned == json.dumps(document, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    assert document["status"] == "planned"
    # The report entry is cross_site_cycle/'s report, whole.
    found = json.loads(plan_to_json(plan_of(tmp_path, "cross_site_cycle")))["report"]
    assert json.loads(report) == found and found
    assert report == json.dumps(found, sort_keys=True, ensure_ascii=False, indent=2) + "\n"

    def site_of(base, name):
        return next(s for s in json.loads(plan_to_json(plan_of(tmp_path, base)))["sites"] if s["name"] == name)

    # Three excerpts of real plans: a held p0 entry, a shares_dropped note, a share-less site.
    excerpts = []
    entry = json.loads(p0_entry)
    assert entry["held_by"] and entry in site_of("p0", "a")["p0"]
    excerpts.append((p0_entry, entry))
    found = json.loads(plan_to_json(plan_of(tmp_path, "share_unbuilt")))["notes"][0]
    assert found["kind"] == "shares_dropped"
    excerpts.append((note, found))
    site = site_of("no_share", "c")
    found = {"name": "c", "share_note": site["share_note"], "shareless_days": site["shareless_days"][:1],
             "shares": site["shares"][:1]}
    assert found["shares"][0]["share"] is None and found["shareless_days"]
    excerpts.append((shareless, found))
    for text, value in excerpts:
        assert text == json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


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
