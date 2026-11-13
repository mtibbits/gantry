"""The week planner (#61): the next 10 working days laid onto blocks per day.

plan_week plans every built site's open leaves from the build's resolved
inputs, the portfolio file's capacity and each leaf's attention rate
(gantry/attention.py). It reads no file and no clock and works in whole
minutes, so the same inputs and as-of date give the same plan on any
machine. docs/week-plan.md states the rules.
"""

import math
from dataclasses import dataclass
from datetime import timedelta

from gantry import schedule
from gantry.attention import leaf_attention, mapping_in_use, site_ratio_source
from gantry.model import HOURS_PER_DAY
from gantry.portfolio_file import DEFAULTS, WEEKDAYS

PLAN_VERSION = 1
ALLOCATION_RULE = "most urgent site first; intended shares not applied"
POOL_ATTENTION = "operator attention hours"
POOL_LANES = "agent lanes"
CAPACITY_SOURCE = "portfolio file"
NO_POOL = "no pool ran out"
HORIZON_DAYS = 10
WEEK_DAYS = 5
# Why there is no plan, in the order checked: the first that applies is the reason.
NO_PLAN = {
    "no_portfolio": "no portfolio_file is configured",
    "settings_not_read": "the portfolio file's settings were not read",
    "no_attention": "the portfolio file declares no usable attention hours after reserved blocks on any weekday",
    "no_lanes": "the portfolio file declares no usable agent_lanes, or 0",
    "no_sites": "no site was built",
}

# The caps that keep the arithmetic whole and finite whatever the portfolio
# file holds: no day has more than 24 hours, a lane count above this plans
# like it, and a leaf longer than this is cut to it.
DAY_MINUTES = 24 * 60
LANE_MINUTES = HOURS_PER_DAY * 60
MAX_LANES = 1_000_000
MAX_LEAF_MINUTES = 60 * 1_000_000_000
PPM = 1_000_000


@dataclass(frozen=True)
class WeekPlan:
    """The plan of one build, in the plan file's form: every nested value is
    a plain list or dict, hours are minutes / 60 and dates are YYYY-MM-DD text.

    Provisional in memory: the fields are the file's top-level keys, pinned
    by tests/fixtures/week_plan/shape-v1.json, but no view binds to this
    class yet. #24 or #33, the first to read it, fixes the in-memory form.
    """

    plan_version: int
    status: str  # "planned" or "no_plan"
    reason: str | None  # a NO_PLAN text when status is "no_plan"
    as_of: str
    allocation_rule: str
    unestimated_planned: int
    horizon_days: list
    planned_week: list
    mapping: dict
    pools: list
    binding: dict
    days: list
    leaves: list
    sites: list


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def to_minutes(hours) -> int:
    """`hours` in whole minutes, rounded once: int(hours * 60 + 0.5), from 0
    to a day's 1440. Never raises: a value that is not a number, is not
    finite or is below 0 reads 0, except that one too large reads 1440."""
    if not _is_number(hours) or hours != hours or hours <= 0:
        return 0
    if hours >= 24:
        return DAY_MINUTES
    return int(hours * 60 + 0.5)


def _leaf_minutes(hours) -> int:
    """A leaf's agent hours in whole minutes, at least 1."""
    if not _is_number(hours) or hours != hours or hours <= 0:
        return 1
    if hours >= MAX_LEAF_MINUTES // 60:
        return MAX_LEAF_MINUTES
    return max(1, int(hours * 60 + 0.5))


def _ppm(rate) -> int:
    """A rate in whole parts per million, rounded once; 0 is rate 0."""
    if not _is_number(rate) or not math.isfinite(rate) or rate <= 0:
        return 0
    return int(rate * PPM + 0.5)


def _hours(minutes) -> float:
    return minutes / 60


@dataclass(frozen=True)
class _Capacity:
    declared: dict  # weekday -> minutes before reserved blocks
    reserved: dict  # weekday -> [{start, hours, label}] in file order
    attention: dict  # weekday -> minutes after reserved blocks
    lanes: int
    block_min: int
    start_cost: int


def _setting(value, key):
    return DEFAULTS[key] if value is None else value


def _capacity(portfolio) -> _Capacity:
    """The portfolio file's capacity in whole minutes. attention_hours or
    agent_lanes that is absent or was rejected reads 0."""
    capacity = portfolio.capacity
    hours = capacity.attention_hours or {}
    declared = {day: to_minutes(hours.get(day, 0)) for day in WEEKDAYS}
    reserved = {day: [] for day in WEEKDAYS}
    taken = dict.fromkeys(WEEKDAYS, 0)
    for block in capacity.reserved:
        if block.day not in reserved:
            continue
        minutes = to_minutes(block.hours)
        reserved[block.day].append({"start": block.start, "hours": _hours(minutes), "label": block.label})
        taken[block.day] += minutes
    lanes = capacity.agent_lanes
    return _Capacity(
        declared=declared,
        reserved=reserved,
        attention={day: max(0, declared[day] - taken[day]) for day in WEEKDAYS},
        lanes=min(lanes, MAX_LANES) if _is_number(lanes) and lanes > 0 else 0,
        block_min=to_minutes(_setting(capacity.block_min_hours, "capacity.block_min_hours")),
        start_cost=to_minutes(_setting(capacity.block_start_cost_hours, "capacity.block_start_cost_hours")),
    )


def _built_sites(result) -> list[str]:
    """The sites a page was rendered for, in config order."""
    return [site.name for site in result.sites if site.name in result.pages]


def _no_plan(portfolio, result):
    """(NO_PLAN key, None) when there is no plan, else (None, capacity)."""
    if portfolio is None:
        return "no_portfolio", None
    if not portfolio.settings_read:
        return "settings_not_read", None
    capacity = _capacity(portfolio)
    if not any(capacity.attention.values()):
        return "no_attention", None
    if not capacity.lanes:
        return "no_lanes", None
    if not _built_sites(result):
        return "no_sites", None
    return None, capacity


def _working_days(as_of, capacity) -> list:
    """The first HORIZON_DAYS dates from `as_of` on whose weekday has attention
    left after reserved blocks. Bounded: some weekday has (no_attention)."""
    days, day = [], as_of
    while len(days) < HORIZON_DAYS:
        if capacity.attention[WEEKDAYS[day.weekday()]] > 0:
            days.append(day)
        day += timedelta(days=1)
    return days


def _pools(run_out):
    return [{"name": name, "capacity_source": CAPACITY_SOURCE, "days_run_out": run_out.get(name, [])}
            for name in (POOL_ATTENTION, POOL_LANES)]


def _site_work(name, inputs, mapping, unit) -> list[dict]:
    """One work item per open leaf of a site, in WBS.md order: what the day
    loop places. An item is also a schedule.pick_ready item."""
    data = inputs.data
    work = []
    for index, leaf in enumerate(data.leaves):
        if not leaf.open:
            continue
        estimate = leaf.estimate
        hours = (estimate.lo_h + estimate.hi_h) / 2 if estimate is not None else unit
        attention = leaf_attention(leaf, data, mapping)
        need = _leaf_minutes(hours)
        work.append({
            "site": name, "key": leaf.key, "eff_priority": 0, "wbs_index": index,
            "need": need, "left": need, "ppm": _ppm(attention.rate), "attention": attention,
            "unestimated": estimate is None or estimate.source == "default", "state": leaf.state,
            "days": [], "today": 0,
        })
    return work


def _site_order(ready) -> list[dict]:
    """`ready` in plan order: repeated schedule.pick_ready, the one ordering rule."""
    left, order = list(ready), []
    while left:
        pick = schedule.pick_ready(left)
        order.append(pick)
        left.remove(pick)
    return order


def _plan_day(day, capacity, work, placed_order) -> dict:
    """Place one day's work and return its record; changes the items in place."""
    weekday = WEEKDAYS[day.weekday()]
    lanes_left = capacity.lanes * LANE_MINUTES
    attention_left = capacity.attention[weekday]
    text = day.isoformat()
    lanes_row = []
    for items in work.values():
        for item in _site_order([item for item in items if item["left"] > 0]):
            room = min(item["left"], LANE_MINUTES - item["today"], lanes_left)
            if room <= 0:
                continue
            if not item["days"]:
                placed_order.append(item)
            item["left"] -= room
            item["today"] += room
            item["days"].append(text)
            lanes_left -= room
            lanes_row.append({"site": item["site"], "key": item["key"], "agent_hours": _hours(room)})
    today = [item for items in work.values() for item in items if item["today"]]
    for item in today:
        item["today"] = 0
    return {
        "date": text, "weekday": weekday,
        "attention_hours_declared": _hours(capacity.declared[weekday]),
        "attention_hours": _hours(capacity.attention[weekday]),
        "reserved": [dict(block) for block in capacity.reserved[weekday]],
        "lane_hours": _hours(capacity.lanes * LANE_MINUTES),
        "blocks": [], "lanes_row": lanes_row,
        "left": {"attention_hours": _hours(attention_left), "lane_hours": _hours(lanes_left)},
        "ran_out": [], "unestimated": sum(item["unestimated"] for item in today),
    }


def _leaf_record(item) -> dict:
    attention = item["attention"]
    return {
        "site": item["site"], "key": item["key"], "rate": item["ppm"] / PPM, "need": attention.need,
        "tag": attention.tag, "basis": attention.basis, "agent_hours": _hours(item["need"]),
        "unestimated": item["unestimated"], "state": item["state"],
        "hours_after_horizon": _hours(item["left"]), "days": list(item["days"]),
    }


def plan_week(result, portfolio, config) -> WeekPlan:
    """The week plan of one full build.

    `result` is build_all's BuildResult, `portfolio` the portfolio file as
    read (its settings_read and capacity) or None, and `config` gives as_of
    and default_unit_hours. A built site is one with a page in result.pages,
    in config order. Never raises on a portfolio file's content.
    """
    mapping = mapping_in_use(portfolio)
    fields = {
        "plan_version": PLAN_VERSION, "as_of": config.as_of.isoformat(), "allocation_rule": ALLOCATION_RULE,
        "mapping": {"values": list(mapping.values), "source": mapping.source, "rejected": mapping.rejected},
    }
    key, capacity = _no_plan(portfolio, result)
    if key is not None:
        return WeekPlan(status="no_plan", reason=NO_PLAN[key], unestimated_planned=0, horizon_days=[],
                        planned_week=[], pools=_pools({}),
                        binding={"pool": None, "first_day": None, "text": NO_POOL},
                        days=[], leaves=[], sites=[], **fields)
    names = _built_sites(result)
    work = {name: _site_work(name, result.inputs[name], mapping, config.default_unit_hours) for name in names}
    horizon = _working_days(config.as_of, capacity)
    placed_order = []
    days = [_plan_day(day, capacity, work, placed_order) for day in horizon]
    leaves = [_leaf_record(item) for name in names for item in placed_order if item["site"] == name]
    sites = []
    for name in names:
        placed = [item for item in placed_order if item["site"] == name]
        sites.append({
            "name": name, "ratio_source": site_ratio_source(),
            "attention_hours_charged": 0.0,
            "lane_hours_charged": _hours(sum(item["need"] - item["left"] for item in placed)),
            "unestimated_planned": sum(item["unestimated"] for item in placed),
            "unplanned": [],
        })
    dates = [day.isoformat() for day in horizon]
    return WeekPlan(status="planned", reason=None,
                    unestimated_planned=sum(item["unestimated"] for item in placed_order),
                    horizon_days=dates, planned_week=dates[:WEEK_DAYS], pools=_pools({}),
                    binding={"pool": None, "first_day": None, "text": NO_POOL},
                    days=days, leaves=leaves, sites=sites, **fields)
