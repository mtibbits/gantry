"""The week planner: gantry/planner.py (#61)."""

import itertools
import shutil
from datetime import date

import pytest

from gantry import planner
from gantry.build import build_all
from gantry.config import load_config
from gantry.planner import NO_PLAN, plan_week
from gantry.portfolio_file import read_portfolio_file
from test_samples import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "week_plan"
FIXTURE_NAMES = ("binds_attention", "binds_lanes", "continues", "states")
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


def test_planned_plan_has_no_reason(tmp_path):
    plan = plan_of(tmp_path, "binds_lanes")
    assert (plan.status, plan.reason) == ("planned", None)
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


def test_rate_below_one_part_per_million_is_rate_0(tmp_path):
    plan = plan_of(tmp_path, "binds_lanes",
                   portfolio=portfolio_md(more="attention_mapping = [0, 1e-7, 0.25, 0.5, 1.0]\n"))
    assert plan.mapping["source"] == "portfolio_file"
    assert placed(plan, "alpha", "Issue-1")["rate"] == 0.0


def test_to_minutes_rounds_once_and_clamps():
    to_minutes = planner.to_minutes
    assert [to_minutes(x) for x in (0, 0.004, 0.25, 0.995, 1, 1.5, 24)] == [0, 0, 15, 60, 60, 90, 1440]
    assert [to_minutes(x) for x in (25, 1e308, int("9" * 400), float("inf"))] == [1440] * 4
    assert [to_minutes(x) for x in (-1, float("nan"), None, True, "3")] == [0] * 5


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
