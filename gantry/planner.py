"""The week planner (#61): the next 10 working days laid onto blocks per day.

plan_week plans every built site's open leaves from the build's resolved
inputs, the portfolio file's capacity, devagent's capacity declaration when
the caller passes one (#39, gantry/capacity.py) and each leaf's attention rate
(gantry/attention.py). It reads no file and no clock; it charges whole
minutes and its targets are exact fractions, so the same inputs and as-of
date give the same plan on any machine. docs/week-plan.md states the rules.
"""

import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import timedelta
from fractions import Fraction
from graphlib import TopologicalSorter
from typing import NamedTuple

from gantry import priority, schedule
from gantry.attention import MAX_RATE, SiteRatio, leaf_attention, mapping_in_use, rate_problem, site_ratio_source
from gantry.deps import find_cycles, inside, open_sources
from gantry.inputs import scheduled
from gantry.model import HOURS_PER_DAY
from gantry.portfolio_file import DEFAULTS, WEEKDAYS, intended_shares
from gantry.roots import write_confined

PLAN_VERSION = 1
PLAN_FILE = "week-plan.json"
ALLOCATION_RULE = "fair share by intended shares"
POOL_ATTENTION = "operator attention hours"
POOL_LANES = "agent lanes"
POOLS = (POOL_ATTENTION, POOL_LANES)
CAPACITY_SOURCE = "portfolio file"
# Both pools' source when the plan is made from devagent's capacity declaration (#39).
CAPACITY_SOURCE_DEVAGENT = "devagent capacity declaration (this build machine)"
# The plan's `capacity.declaration` (#39): what devagent's capacity declaration
# is to the plan. Only a used one changes the capacity planned from.
DECLARATIONS = ("not_declared", "used", "invalid")
NOT_DECLARED, USED, INVALID = DECLARATIONS
# The plan's `capacity.attention.rule`: where each day's attention hours come
# from. The portfolio file's as they stand; a used declaration's weekly total
# scaled onto the portfolio file's weekdays; or, when the portfolio file sets
# no attention_hours, that total spread evenly over SPREAD_DAYS.
ATTENTION_RULES = ("portfolio_file", "scaled", "even_spread")
RULE_PORTFOLIO_FILE, RULE_SCALED, RULE_EVEN_SPREAD = ATTENTION_RULES
# A used declaration's operator_hours_per_agent_hour is every site's declared
# ratio (gantry/attention.py). RATIO_BASIS follows the ratio in the basis of a
# leaf that takes it: "declared: site ratio 0.25; " + RATIO_BASIS. One above
# MAX_RATE is valid for devagent and no rate here: it is not used, and
# RATIO_REFUSED is the reason in the plan's `capacity.declared_ratio`, {value}
# being the ratio as repr writes the number read.
RATIO_BASIS = CAPACITY_SOURCE_DEVAGENT
RATIO_REFUSED = "devagent declares {value} operator hours per agent hour, above 24; it is not used"
NO_POOL = "no pool ran out"
HORIZON_DAYS = 10
WEEK_DAYS = 5
# A leaf's priority tier, fixed for the whole plan (#37): effective P0, then
# started (in progress or stuck), then the rest, divided by intended shares.
TIERS = ("p0", "started", "fair_share")
TIER_P0, TIER_STARTED, TIER_FAIR = TIERS
STARTED_STATES = ("in_progress", "stuck")
SHARE_NOTE = "no intended share set; planned only into capacity left after the sites with shares"
# The kinds of a plan note, in the order a day's notes are written.
NOTE_KINDS = ("shares_dropped", "no_allocation")
SHARES_DROPPED, NO_ALLOCATION = NOTE_KINDS
NOTE_TEXTS = {
    SHARES_DROPPED: "shares dropped on {date} for projects that are not built sites: {projects}",
    NO_ALLOCATION: "no intended shares apply on {date}; every site planned with equal shares",
}
# Why there is no plan: the first that applies is the reason (_no_plan). The
# four after settings_not_read are a devagent capacity declaration's (#39).
# A used one gives the attention and the lanes, so no_lanes never applies and
# the two capacity_* texts are its reasons, with no_attention for an
# attention_hours table that gives no weekday a minute. An invalid one is
# named by the two invalid_* texts, in place of no_attention and no_lanes.
# {total} is the declared weekly hours, never rounded, as repr writes the
# number read (30.50 is "30.5", 0.00001 is "1e-05"), and {count} is
# "1 violation" or "N violations" (_reason).
NO_PLAN = {
    "no_portfolio": "no portfolio_file is configured",
    "settings_not_read": "the portfolio file's settings were not read",
    "capacity_reserved":
        "devagent declares {total} h a week, but the portfolio file reserves every weekday's attention hours",
    "capacity_too_small": "devagent declares {total} h a week, under one minute on every working day",
    "invalid_no_attention":
        "devagent's capacity declaration is invalid ({count}), and the portfolio file declares no attention hours",
    "invalid_no_lanes":
        "devagent's capacity declaration is invalid ({count}), and the portfolio file declares no agent lanes",
    "no_attention": "the portfolio file declares no usable attention hours after reserved blocks on any weekday",
    "no_lanes": "the portfolio file declares no usable agent_lanes, or 0",
    "no_sites": "no site was built",
}

# Why an open leaf of a built site is not in the 10 days: the first that
# applies. `paused` is every open leaf of a site a used capacity declaration
# pauses (#39), whatever else holds it.
UNPLANNED_REASONS = ("paused", "blocked_externally", "parked", "waits_on_cycle", "cross_site_cycle",
                     "waits_on_site_absent", "waits_on_unresolved", "waits_on_unplanned", "beyond_horizon")
(PAUSED, BLOCKED_EXTERNALLY, PARKED, WAITS_ON_CYCLE, CROSS_SITE_CYCLE, WAITS_ON_SITE_ABSENT,
 WAITS_ON_UNRESOLVED, WAITS_ON_UNPLANNED, BEYOND_HORIZON) = UNPLANNED_REASONS
# The kinds of a plan report entry, in report order: devagent's lines about
# its capacity declaration (#39), each violation and then each unknown key,
# then what the plan finds across sites (#62).
PLAN_REPORT_KINDS = ("capacity_invalid", "capacity_unknown_key", CROSS_SITE_CYCLE)
CAPACITY_INVALID, CAPACITY_UNKNOWN_KEY = PLAN_REPORT_KINDS[:2]

# The site report's kinds of a local depends_on entry that names nothing (#8):
# no dependency in the plan, only counted on the leaf's record (#62).
LOCAL_UNRESOLVED_KINDS = ("unresolved_ref", "malformed_ref")

# The caps that keep the arithmetic whole and finite whatever the portfolio
# file and devagent's capacity declaration hold: no day has more than 24
# hours, of attention or of one lane; a lane count above this plans like it;
# and a leaf longer than this is cut to it.
DAY_MINUTES = 24 * 60
LANE_MINUTES = HOURS_PER_DAY * 60
MAX_LANES = 1_000_000
MAX_LEAF_MINUTES = 60 * 1_000_000_000
PPM = 1_000_000
# A declared weekly total (#39) this many minutes or fewer from the portfolio
# file's weekday sum scales no day: 0.01 h, compared exactly.
SCALING_TOLERANCE = Fraction(6, 10)
# The weekdays a declared weekly total is spread over when the portfolio file sets no attention_hours.
SPREAD_DAYS = WEEKDAYS[:5]


@dataclass(frozen=True)
class WeekPlan:
    """The plan of one build, in the plan file's form: every nested value is
    a plain list or dict, hours are minutes / 60 and dates are YYYY-MM-DD text.

    Frozen at the top level only: every view of a build gets this one object,
    so a view must not change its lists or dicts.

    The fields are the file's top-level keys, pinned by
    tests/fixtures/week_plan/shape-v1.json. The week view (#38), its first
    reader, reads the file-form lists and dicts, so the file form is the
    in-memory form.
    """

    plan_version: int
    status: str  # "planned" or "no_plan"
    reason: str | None  # a NO_PLAN text, filled in, when status is "no_plan"
    as_of: str
    allocation_rule: str
    unestimated_planned: int
    horizon_days: list
    planned_week: list
    mapping: dict
    pools: list
    binding: dict
    capacity: dict
    days: list
    leaves: list
    sites: list
    report: list
    notes: list


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _whole_minutes(hours, cap) -> int:
    """`hours` in whole minutes, rounded once: int(hours * 60 + 0.5), from 0
    to `cap`. Never raises: a value that is not a number, is not finite or
    is below 0 reads 0, except that one too large reads `cap`."""
    if not _is_number(hours) or hours != hours or hours <= 0:
        return 0
    if hours >= cap // 60:
        return cap
    return int(hours * 60 + 0.5)


def to_minutes(hours) -> int:
    """`hours` in whole minutes, from 0 to a day's 1440 (_whole_minutes)."""
    return _whole_minutes(hours, DAY_MINUTES)


def _leaf_minutes(hours) -> int:
    """A leaf's agent hours in whole minutes, at least 1."""
    return max(1, _whole_minutes(hours, MAX_LEAF_MINUTES))


def _ppm(rate) -> int:
    """A rate in whole parts per million, rounded once; 0 is rate 0. Never
    raises: a rate above MAX_RATE reads MAX_RATE."""
    if not _is_number(rate) or rate != rate or rate <= 0:
        return 0
    if rate >= MAX_RATE:
        return MAX_RATE * PPM
    return int(rate * PPM + 0.5)


def is_rate_0(rate) -> bool:
    """Whether `rate` is planned as rate 0 (_ppm): under half a part per
    million, so a leaf at it needs no block and runs in the lanes row. The
    week view asks it of a declared ratio (#39), which the plan holds as
    declared."""
    return _ppm(rate) == 0


def _hours(minutes) -> float:
    return minutes / 60


@dataclass(frozen=True)
class _Capacity:
    declared: dict  # weekday -> minutes before reserved blocks
    reserved: dict  # weekday -> [{start, hours, label}] in file order
    taken: dict  # weekday -> minutes of its reserved blocks
    attention: dict  # weekday -> minutes after reserved blocks
    lost: dict  # weekday -> minutes of attention that do not fit in the day
    rule: dict  # the rule that gave `attention`, and its figures: the plan's `capacity.attention` but for `lost`
    lanes: int
    lane_minutes: int  # the agent minutes one lane, and so one leaf, takes in a day
    lane_week: int | float | None  # devagent's lane_hours_per_week as declared; None from the portfolio file
    lane_lost: int  # minutes a week of one lane that do not fit in its days
    block_min: int
    start_cost: int
    block_default: int  # never under block_min


def _setting(value, key):
    return DEFAULTS[key] if value is None else value


def _capacity(portfolio) -> _Capacity:
    """The portfolio file's capacity in whole minutes. attention_hours or
    agent_lanes that is absent or was rejected reads 0. Its hours fit in
    their days, so no minute is lost, and a lane's day is LANE_MINUTES.
    _declared_capacity makes a used capacity declaration's from it."""
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
    block_min = to_minutes(_setting(capacity.block_min_hours, "capacity.block_min_hours"))
    return _Capacity(
        declared=declared,
        reserved=reserved,
        taken=taken,
        attention={day: max(0, declared[day] - taken[day]) for day in WEEKDAYS},
        lost=dict.fromkeys(WEEKDAYS, 0),
        rule={"rule": RULE_PORTFOLIO_FILE, "total_hours": None, "weekday_sum_hours": None, "factor": None},
        lanes=min(lanes, MAX_LANES) if _is_number(lanes) and lanes > 0 else 0,
        lane_minutes=LANE_MINUTES,
        lane_week=None,
        lane_lost=0,
        block_min=block_min,
        start_cost=to_minutes(_setting(capacity.block_start_cost_hours, "capacity.block_start_cost_hours")),
        block_default=max(block_min, to_minutes(_setting(capacity.block_default_hours,
                                                          "capacity.block_default_hours"))),
    )


def _built_sites(result) -> list[str]:
    """The sites a page was rendered for, in config order."""
    return [site.name for site in result.sites if site.name in result.pages]


def _declaration(capacity) -> str:
    """What devagent's capacity declaration, a DeclaredCapacity or None, is to
    a plan, one of DECLARATIONS. Invalid with any violation, whether or not
    it has a [capacity] table: a project's capacity table without one is a
    violation. Else used when it declares capacity. None is not_declared."""
    if capacity is None:
        return NOT_DECLARED
    if capacity.violations:
        return INVALID
    return USED if capacity.usable else NOT_DECLARED


def _declared_ratio(capacity):
    """(every site's declared ratio or None, the plan's
    `capacity.declared_ratio`) from devagent's declaration `capacity`. Both
    are None unless it is used and sets operator_hours_per_agent_hour. A
    ratio that can be a rate is built, here and nowhere else; one that
    cannot is not built, and the record says why. A usable declaration's
    ratio is a number from 0 up (gantry/capacity.py), so the one reason is
    that it is above MAX_RATE."""
    if _declaration(capacity) != USED or capacity.operator_hours_per_agent_hour is None:
        return None, None
    value = capacity.operator_hours_per_agent_hour
    if rate_problem(value) is not None:
        return None, {"value": value, "used": False, "reason": RATIO_REFUSED.format(value=repr(value))}
    return SiteRatio(value, "declared", basis=RATIO_BASIS), {"value": value, "used": True, "reason": None}


def _token_budget(capacity):
    """The plan's `capacity.token_budget`: a used declaration's effective
    weekly token budget, the term that sets it and its unit, else None. The
    budget is recorded and never planned with, and 0 is a budget."""
    if _declaration(capacity) != USED or capacity.token_budget is None:
        return None
    return {"tokens_per_week": capacity.token_budget, "binding": capacity.token_binding_term,
            "unit": capacity.token_unit}


def _paused_sites(capacity, names) -> list[str]:
    """Those of `names`, the built sites in config order, that a used
    declaration pauses. A paused project that is not a built site is not in
    the plan."""
    if _declaration(capacity) != USED:
        return []
    paused = set(capacity.paused)
    return [name for name in names if name in paused]


def _exact_minutes(hours) -> Fraction:
    """A declared hour figure in minutes, exactly as written: from the
    number's shortest decimal form, as render.hundredths reads one. 20.01 h
    is 1200.6 minutes; the binary float times 60 is not."""
    return Fraction(repr(float(hours))) * 60


def _half_up(minutes) -> int:
    """An exact figure rounded once to whole minutes, a half up."""
    return math.floor(minutes + Fraction(1, 2))


def _declared_attention(portfolio, available, capacity):
    """A used declaration's attention before any day's cap: (NO_PLAN key,
    None, None) when the portfolio file's weekdays give its weekly total
    nowhere to go, else (None, {weekday: minutes}, the rule's record for
    the plan's `capacity.attention`).

    The total is scaled onto the portfolio file's weekdays after reserved
    blocks, each day rounded once, unless it is within SCALING_TOLERANCE of
    their sum: then the factor is 1 and each day is the file's. When the
    portfolio file sets no attention_hours it is spread evenly over
    SPREAD_DAYS instead. A table that leaves no weekday a minute is set, so
    nothing is spread over it: its reserved blocks took its hours
    (capacity_reserved), or it gave none (no_attention)."""
    total = _exact_minutes(capacity.operator_hours_per_week)
    record = {"rule": RULE_SCALED, "total_hours": capacity.operator_hours_per_week,
              "weekday_sum_hours": None, "factor": None}
    if portfolio.capacity.attention_hours is None:
        each = _half_up(total / len(SPREAD_DAYS))
        return None, {day: each if day in SPREAD_DAYS else 0 for day in WEEKDAYS}, record | {"rule": RULE_EVEN_SPREAD}
    weekday_sum = sum(available.attention.values())
    if weekday_sum == 0:
        return ("capacity_reserved" if any(available.declared.values()) else "no_attention"), None, None
    factor = total / weekday_sum if abs(total - weekday_sum) > SCALING_TOLERANCE else Fraction(1)
    minutes = {day: _half_up(available.attention[day] * factor) for day in WEEKDAYS}
    return None, minutes, record | {"weekday_sum_hours": _hours(weekday_sum), "factor": float(factor)}


def _declared_capacity(portfolio, available, capacity):
    """(NO_PLAN key, None) when a used declaration `capacity` gives no plan,
    else (None, the capacity planned from): `available`, the portfolio
    file's, with the declaration's attention and lanes.

    A day's attention (_declared_attention) is capped at the 24 hours less
    its reserved blocks, and what does not fit is the day's `lost`; its
    minutes before reserved blocks are its attention plus those blocks. A
    lane's day is lane_hours_per_week over the working days, the weekdays
    left with a minute of attention, rounded once, from 1 minute to the 24
    hours; what does not fit is `lane_lost`, a week. max_lanes above
    MAX_LANES plans as MAX_LANES. A usable declaration's numbers are in
    devagent's ranges (gantry/capacity.py), so none is checked again."""
    key, minutes, record = _declared_attention(portfolio, available, capacity)
    if key is not None:
        return key, None
    attention = {day: min(minutes[day], max(0, DAY_MINUTES - available.taken[day])) for day in WEEKDAYS}
    if not any(attention.values()):
        # Under a minute everywhere, or every day with a minute is reserved for its 24 hours.
        return ("capacity_reserved" if any(minutes.values()) else "capacity_too_small"), None
    working_days = sum(1 for day in WEEKDAYS if attention[day] > 0)
    lane = _half_up(_exact_minutes(capacity.lane_hours_per_week) / working_days)
    lane_minutes = min(max(lane, 1), DAY_MINUTES)
    return None, replace(
        available,
        declared={day: attention[day] + available.taken[day] for day in WEEKDAYS},
        attention=attention,
        lost={day: minutes[day] - attention[day] for day in WEEKDAYS},
        rule=record,
        lanes=min(capacity.max_lanes, MAX_LANES),
        lane_minutes=lane_minutes,
        lane_week=capacity.lane_hours_per_week,
        lane_lost=max(0, lane - lane_minutes) * working_days,
    )


def _no_plan(portfolio, result, capacity):
    """(NO_PLAN key, None) when there is no plan, else (None, the capacity
    planned from). `capacity` is devagent's declaration or None.

    A used declaration gives the attention and the lanes, so the portfolio
    file need set neither (_declared_capacity). With any other the portfolio
    file's capacity is planned from, and an invalid one names itself in the
    reason."""
    if portfolio is None:
        return "no_portfolio", None
    if not portfolio.settings_read:
        return "settings_not_read", None
    available = _capacity(portfolio)
    declaration = _declaration(capacity)
    if declaration == USED:
        key, available = _declared_capacity(portfolio, available, capacity)
        if key is not None:
            return key, None
    else:
        invalid = declaration == INVALID
        if not any(available.attention.values()):
            return ("invalid_no_attention" if invalid else "no_attention"), None
        if not available.lanes:
            return ("invalid_no_lanes" if invalid else "no_lanes"), None
    if not _built_sites(result):
        return "no_sites", None
    return None, available


def _reason(key, capacity) -> str:
    """The NO_PLAN text of `key`, with what it names of the declaration filled
    in: {total}, the declared weekly hours as repr writes the number read
    ("0.001"; 30.50 is "30.5"), never rounded, and {count}, how many
    violations it has."""
    if capacity is None:
        return NO_PLAN[key]
    count = len(capacity.violations)
    return NO_PLAN[key].format(total=repr(capacity.operator_hours_per_week),
                               count="1 violation" if count == 1 else f"{count} violations")


def _working_days(as_of, capacity) -> list:
    """The first HORIZON_DAYS dates from `as_of` on whose weekday has attention
    left after reserved blocks. Bounded: some weekday has (_no_plan)."""
    days, day = [], as_of
    while len(days) < HORIZON_DAYS:
        if capacity.attention[WEEKDAYS[day.weekday()]] > 0:
            days.append(day)
        day += timedelta(days=1)
    return days


def _note(kind, day, projects) -> dict:
    return {"kind": kind, "date": day.isoformat(), "projects": list(projects),
            "text": NOTE_TEXTS[kind].format(date=day.isoformat(), projects=", ".join(projects))}


class _DayShares(NamedTuple):
    shares: dict  # share site -> its share as a Fraction
    shareless: list
    notes: list


def _day_shares(portfolio, day, names) -> _DayShares:
    """The shares, share-less sites and notes of one planned day, against `names`, the built sites in config order.

    The allocation that applies on the day (intended_shares) loses every
    project that is not a built site (a shares_dropped note); the sites left
    with a share above 0 are the share sites, their shares scaled to sum to 1,
    and every other built site is share-less that day. With no allocation, or
    nothing left to scale, every built site is a share site at 1/n and the day
    has a no_allocation note."""
    found = intended_shares(portfolio, day)
    notes, shares = [], {}
    if found is not None:
        built = set(names)
        dropped = [project for project in found.shares if project not in built]
        if dropped:
            notes.append(_note(SHARES_DROPPED, day, dropped))
        kept = {name: Fraction(found.shares[name]) for name in names if found.shares.get(name, 0) > 0}
        total = sum(kept.values())
        shares = {name: value / total for name, value in kept.items()}
    if not shares:
        notes.append(_note(NO_ALLOCATION, day, []))
        return _DayShares(dict.fromkeys(names, Fraction(1, len(names))), [], notes)
    return _DayShares(shares, [name for name in names if name not in shares], notes)


def _ranked(data):
    """(the leaves the worksite page schedules, their effective priority)."""
    work = scheduled(data)
    return work, priority.effective(work, data.links)


def _p0_entries(work, effective) -> list[dict]:
    """The site's P0 list (priority.p0_keys over `_ranked`), each with the
    cross-site outcomes that hold it (crosssite keeps only open, absent and
    unresolved ones), in depends_on order."""
    by_key = {leaf.key: leaf for leaf in work}
    return [{"key": key, "held_by": [f"{o.target_site}:{o.target_issue}" for o in by_key[key].foreign_targets or ()]}
            for key in priority.p0_keys(work, effective)]


def _tier(eff_priority, state) -> str:
    if eff_priority == 0:
        return TIER_P0
    return TIER_STARTED if state in STARTED_STATES else TIER_FAIR


def _pools(run_out, capacity):
    """The plan's pools. Both read devagent's declaration as their source when
    it is used, on a no_plan plan too; else the portfolio file."""
    source = CAPACITY_SOURCE_DEVAGENT if _declaration(capacity) == USED else CAPACITY_SOURCE
    return [{"name": name, "capacity_source": source, "days_run_out": run_out.get(name, [])} for name in POOLS]


def _unresolved_local(data, open_leaves) -> dict:
    """{key: how many of the site report's local entries that name nothing sit
    on the leaf's line or on an open row above it}: #8 reports each entry once,
    at its node's line, and lines are unique within a WBS.md."""
    per_line = {}
    for entry in data.report:
        if entry.kind in LOCAL_UNRESOLVED_KINDS:
            per_line[entry.line] = per_line.get(entry.line, 0) + 1
    row_at = {row.outline: row for row in data.rows}
    return {leaf.key: sum(per_line.get(node.line, 0) for node in open_sources(leaf, row_at)) for leaf in open_leaves}


def _site_work(name, inputs, mapping, unit, effective, declared=None) -> list[dict]:
    """One item per open leaf of a site, in WBS.md order, its reason not yet
    decided. An item with no `reason` is work for the day loop, and a
    schedule.pick_ready item.

    `effective` is _ranked's: the worksite page's effective priority (every scheduled leaf, with
    blocked, parked and cross-site waiters), so an unplanned dependent still
    lifts its blocker; it never crosses sites. A leaf's predecessors are its
    open prerequisites within the site, as (site, key) ids for now.
    `declared` is the site's declared ratio (_declared_ratio) or None: the
    rate of a leaf with no h: score.
    """
    data = inputs.data
    open_leaves = [leaf for leaf in data.leaves if leaf.open]
    preds = {leaf.key: [] for leaf in open_leaves}
    for before, after in data.links:
        if before in preds and after in preds:
            preds[after].append((name, before))
    held = set(data.held_out)
    unresolved = _unresolved_local(data, open_leaves)
    index = {leaf.key: i for i, leaf in enumerate(data.leaves)}
    items = []
    for leaf in open_leaves:
        estimate = leaf.estimate
        hours = (estimate.lo_h + estimate.hi_h) / 2 if estimate is not None else unit
        attention = leaf_attention(leaf, data, mapping, declared=declared)
        need = _leaf_minutes(hours)
        eff = effective.get(leaf.key, priority.rank(leaf.priority))
        items.append({
            "site": name, "key": leaf.key, "wbs_index": index[leaf.key],
            "eff_priority": eff, "tier": _tier(eff, leaf.state),
            "preds": preds[leaf.key], "reason": None, "finished": None,
            "need": need, "left": need, "ppm": _ppm(attention.rate), "attention": attention,
            "unestimated": estimate is None or estimate.source == "default", "state": leaf.state,
            "days": [], "today": 0, "unresolved_local": unresolved[leaf.key],
            "held_out": leaf.key in held, "foreign": leaf.foreign_targets or (),
        })
    return items


def _cross_site_preds(result, items):
    """Add each leaf's cross-site predecessors from #68's outcomes, never
    resolved again: an open leaf target is that leaf, an open row target the
    open leaves under it in WBS.md order (#8's rule). A duplicate id is
    dropped, the first kept. An open outcome naming a site this plan does
    not plan (one with no page) adds none: _reasons holds the leaf."""
    under = {}

    def open_under(site, row_key):
        if (site, row_key) not in under:
            data = result.inputs[site].data
            row = next(row for row in data.rows if row.key == row_key)
            under[site, row_key] = [(site, leaf.key) for leaf in data.leaves if leaf.open and inside(leaf, row)]
        return under[site, row_key]

    for site_items in items.values():
        for item in site_items:
            seen = set(item["preds"])
            for outcome in item["foreign"]:
                if outcome.status != "open" or outcome.target_site not in items:
                    continue
                site = outcome.target_site
                found = open_under(site, outcome.target_key) if outcome.target_is_row else [(site, outcome.target_key)]
                for node in found:
                    if node not in seen:
                        seen.add(node)
                        item["preds"].append(node)


def _cross_site_cycles(by_node, paused=()) -> list[list[tuple[str, str]]]:
    """The dependency cycles over every built site's open leaves that span two
    or more sites: find_cycles' components, members in config order of site
    then WBS.md order, ordered by first member. A cycle within one site uses
    only its own links, so it lies in #8's cycle and stays waits_on_cycle.
    The leaves of the `paused` sites are no nodes here: a paused leaf is in
    no cycle, and a link to or from one closes none."""
    nodes = [node for node in by_node if node[0] not in paused]
    links = [(pred, node) for node in nodes for pred in by_node[node]["preds"]]
    return [cycle for cycle in find_cycles(nodes, links) if len({site for site, _ in cycle}) >= 2]


def _reasons(by_node, cycles, sites, paused=()) -> dict:
    """{(site, key): reason} for each open leaf the plan leaves out whatever
    the capacity: the first reason that applies, in UNPLANNED_REASONS' order.
    Every open leaf of a site in `paused` is paused, so such a site has no
    work for the day loop. An open outcome naming a site not in `sites`, the
    ones this plan plans (the same set _cross_site_preds holds on, the
    paused ones included), is unresolved too: work the plan cannot see.
    waits_on_unplanned is decided in one order over every site, so a chain
    through several sites is followed whatever their config order, and a
    leaf that waits on a paused one is found by it."""
    in_cycle = {node for cycle in cycles for node in cycle}
    reasons = {}
    for node, item in by_node.items():
        statuses = {outcome.status for outcome in item["foreign"]}
        unseen = any(o.status == "open" and o.target_site not in sites for o in item["foreign"])
        if item["site"] in paused:
            reasons[node] = PAUSED
        elif item["state"] == "blocked_external":
            reasons[node] = BLOCKED_EXTERNALLY
        elif item["state"] == "parked":
            reasons[node] = PARKED
        elif item["held_out"]:
            reasons[node] = WAITS_ON_CYCLE
        elif node in in_cycle:
            reasons[node] = CROSS_SITE_CYCLE
        elif "absent" in statuses:
            reasons[node] = WAITS_ON_SITE_ABSENT
        elif "unresolved" in statuses or unseen:
            reasons[node] = WAITS_ON_UNRESOLVED
    # Every cycle's leaves have a reason now, or it runs through a paused leaf, which has one:
    # each leaf left is reached after its predecessors.
    rest = TopologicalSorter({node: [p for p in item["preds"] if p not in reasons]
                              for node, item in by_node.items() if node not in reasons})
    for node in rest.static_order():
        if any(p in reasons for p in by_node[node]["preds"]):
            reasons[node] = WAITS_ON_UNPLANNED
    return reasons


def _report(capacity, cycles=()) -> list[dict]:
    """The plan's report entries, in PLAN_REPORT_KINDS' order. A capacity
    entry is one line devagent's reader writes about the declaration, as its
    `text`, and names no leaf: each violation, then each unknown key, in
    devagent's order, on a no_plan plan too. A cross_site_cycle entry names
    its leaves and has no `text`."""
    lines = []
    if capacity is not None:
        lines = ([(CAPACITY_INVALID, line) for line in capacity.violations]
                 + [(CAPACITY_UNKNOWN_KEY, line) for line in capacity.warnings])
    return ([{"kind": kind, "leaves": [], "text": line} for kind, line in lines]
            + [{"kind": CROSS_SITE_CYCLE, "leaves": [{"site": site, "key": key} for site, key in cycle], "text": None}
               for cycle in cycles])


def _site_order(ready) -> list[dict]:
    """`ready` in plan order: repeated schedule.pick_ready, the one ordering rule."""
    left, order = list(ready), []
    while left:
        pick = schedule.pick_ready(left)
        order.append(pick)
        left.remove(pick)
    return order


def _agent_fit(operator, ppm) -> int:
    """The most whole agent minutes whose operator minutes, rounded up, fit `operator`."""
    return operator * PPM // ppm if operator > 0 else 0


def _operator(agent, ppm) -> int:
    """The operator minutes of `agent` agent minutes, rounded up."""
    return (ppm * agent + PPM - 1) // PPM


class _Day:
    """One day's pools and placements while it is planned.

    The day runs in eight phases (docs/week-plan.md, Priority tiers) around
    at most one open block: a block stays open until a block of another site
    opens or the day ends, and a phase that gives the next block to the open
    block's site continues it, with no second start cost. Every charge to a
    site moves the targets of the sites that can use the pool (charge)."""

    def __init__(self, index, day, capacity, names, work, placed_order, ledger, day_shares, trace=None):
        self.index, self.text, self.capacity, self.names = index, day.isoformat(), capacity, names
        self.weekday = WEEKDAYS[day.weekday()]
        self.attention = capacity.attention[self.weekday]
        self.lane_minutes = capacity.lane_minutes
        self.lanes = capacity.lanes * self.lane_minutes
        self.placed_order, self.ledger = placed_order, ledger
        self.shares, self.shareless = day_shares.shares, day_shares.shareless
        # plan_week's trace: a list gets one entry per charge, lift and flag (#37); it changes no record.
        self.trace = trace
        self.position = {name: position for position, name in enumerate(names)}
        self.phase = 1
        self.today = []
        self.blocks, self.row = [], []
        # The open block: {"site", "record", "entries", "work", "phase"}, work
        # being the start cost plus the operator minutes placed; its padding
        # up to block_min is charged when it closes.
        self.open = None
        # The pools the day's block-leaf checks found run out (block_pools_run_out).
        self.ran = set()
        # Whether the placement that took the last lane minute also took the
        # last of the attention its block could give that leaf.
        self.tied = False
        # Whether a block stopped at the day's attention while its leaf could
        # still take a lane minute. Recorded then: a later placement that takes
        # the last lane minute does not hide it.
        self.stopped = False
        # Each site's ready leaves per tier in plan order, fixed for the day: a
        # leaf is ready when every predecessor finished on an earlier day.
        # Within a tier, leaves started on an earlier day go first.
        self.order = {}
        for name in names:
            ready = [item for item in work[name] if item["left"] > 0
                     and all(p["finished"] is not None and p["finished"] < index for p in item["preds"])]
            started = [item for item in ready if item["days"]]
            fresh = [item for item in ready if not item["days"]]
            ordered = _site_order(started) + _site_order(fresh)
            self.order[name] = {tier: [item for item in ordered if item["tier"] == tier] for tier in TIERS}

    def ready(self, name):
        """The site's ready leaves, every tier, in tier order."""
        return [item for tier in TIERS for item in self.order[name][tier]]

    def room(self, item) -> int:
        """The agent minutes the leaf could still take today, lanes allowing."""
        return min(item["left"], self.lane_minutes - item["today"], self.lanes)

    def waiting(self, item) -> bool:
        """Whether a ready leaf still has minutes to place and is under a lane's day today."""
        return item["left"] > 0 and item["today"] < self.lane_minutes

    # --- the ledger --------------------------------------------------------------------

    def users(self, pool) -> list:
        """The sites that can use `pool` now, in config order: a ready leaf
        under a lane's day today, of rate above 0 for attention."""
        return [name for name in self.names
                if any(self.waiting(item) and (item["ppm"] or pool == POOL_LANES)
                       for tier in TIERS for item in self.order[name][tier])]

    def accrual(self, name, pool, phase):
        """({site: its target's gain per minute charged to `name`}, the can-use
        set): the table in docs/week-plan.md, Fair share between sites."""
        users = self.users(pool)
        gains = {}
        if name in self.shares or phase <= 4:
            share_users = [user for user in users if user in self.shares]
            total = sum(self.shares[user] for user in share_users)
            gains = {user: self.shares[user] / total for user in share_users}
        if name not in self.shares:
            shareless = [user for user in users if user not in self.shares]
            for user in shareless:
                gains[user] = gains.get(user, 0) + Fraction(1, len(shareless))
        return gains, users

    def charge(self, name, pool, minutes, phase, what="work"):
        """Charge `minutes` of `pool` to the site, its accrual judged before
        the minutes are placed: a caller charges, then places."""
        if minutes <= 0:
            return
        gains, users = self.accrual(name, pool, phase)
        charged, target = self.ledger[pool]["charged"], self.ledger[pool]["target"]
        before = {site: (charged[site], target[site]) for site in self.names} if self.trace is not None else None
        for site, gain in gains.items():
            target[site] += minutes * gain
        charged[name] += minutes
        if self.trace is not None:
            self.trace.append({"date": self.text, "site": name, "pool": pool, "phase": phase, "minutes": minutes,
                               "what": what, "before": before, "users": users,
                               "after": {site: (charged[site], target[site]) for site in self.names}})

    def deficit(self, name, pool):
        return self.ledger[pool]["target"][name] - self.ledger[pool]["charged"][name]

    # --- blocks ------------------------------------------------------------------------

    def padding(self, work=None) -> int:
        """The open block's padding up to block_min, charged when it closes."""
        if self.open is None:
            return 0
        return max(0, self.capacity.block_min - (self.open["work"] if work is None else work))

    def block_fits(self, ppm, attention=None, work=None) -> bool:
        """Whether a new block could open and hold one agent minute at rate
        `ppm`: the attention left, less the open block's padding, holds
        block_min and the start cost plus one agent minute. `attention` and
        `work` default to now."""
        left = (self.attention if attention is None else attention) - self.padding(work)
        return (left >= self.capacity.block_min
                and _agent_fit(left - self.capacity.start_cost, ppm) >= 1)

    def open_for(self, name) -> bool:
        return self.open is not None and self.open["site"] == name

    def fits(self, item, attention=None, work=None) -> bool:
        """Whether a ready leaf fits now (docs/week-plan.md, A ready leaf that
        fits), or at `attention` left with the open block at `work`."""
        if not (self.lanes > 0 and self.waiting(item)):
            return False
        if not item["ppm"]:
            return True
        if self.open_for(item["site"]):
            return _agent_fit(self.attention if attention is None else attention, item["ppm"]) >= 1
        return self.block_fits(item["ppm"], attention, work)

    def place(self, item, room):
        """Place `room` agent minutes of the leaf: once in its days and in the
        day's count however many blocks of the day hold it."""
        if not item["days"]:
            self.placed_order.append(item)
        if item["today"] == 0:
            self.today.append(item)
        item["left"] -= room
        item["today"] += room
        if item["days"][-1:] != [self.text]:
            item["days"].append(self.text)
        self.lanes -= room

    def close(self):
        """Close the open block, if any: charge its padding and record its length."""
        block = self.open
        if block is None:
            return
        pad = self.padding()
        self.charge(block["site"], POOL_ATTENTION, pad, block["phase"], what="padding")
        self.attention -= pad
        block["record"]["length_hours"] = _hours(block["work"] + pad)
        block["record"]["leaves"] = [{"site": block["site"], "key": item["key"], "agent_hours": _hours(agent),
                                      "operator_hours": _hours(operator)}
                                     for item, agent, operator in block["entries"]]
        self.open = None

    def open_block(self, name):
        self.close()
        start = self.capacity.start_cost
        self.charge(name, POOL_ATTENTION, start, self.phase, what="start")
        self.attention -= start
        record = {"number": len(self.blocks) + 1, "site": name, "length_hours": None,
                  "start_cost_hours": _hours(start), "leaves": []}
        self.blocks.append(record)
        self.open = {"site": name, "record": record, "entries": [], "work": start, "phase": self.phase}

    def put(self, item, agent, fit):
        """Place `agent` agent minutes of the leaf in the open block and charge both pools."""
        name, phase, block = item["site"], self.phase, self.open
        self.tied = agent == self.lanes == fit
        operator = _operator(agent, item["ppm"])
        self.charge(name, POOL_ATTENTION, operator, phase)
        self.charge(name, POOL_LANES, agent, phase)
        self.place(item, agent)
        self.attention -= operator
        block["work"] += operator
        block["phase"] = phase
        if block["entries"] and block["entries"][-1][0] is item:
            last = block["entries"][-1]
            block["entries"][-1] = (item, last[1] + agent, last[2] + operator)
        else:
            block["entries"].append((item, agent, operator))
        return operator

    def block_pass(self, name, items):
        """One pass of `name`'s block over its `items` of rate above 0, in
        order, continuing the open block when it is the site's or opening one
        when a leaf fits, limited only by the day's pools and a lane's day."""
        rated = [item for item in items if item["ppm"]]
        if not self.open_for(name):
            if not any(self.fits(item) for item in rated):
                return
            self.open_block(name)
        for item in rated:
            fit = _agent_fit(self.attention, item["ppm"])
            want = self.room(item)
            self.stopped = self.stopped or fit < want
            agent = min(want, fit)
            if agent > 0:
                self.put(item, agent, fit)

    def row_place(self, item):
        """The rate-0 leaf joins the lanes row, up to a lane's day or the lanes left."""
        agent = self.room(item)
        self.charge(item["site"], POOL_LANES, agent, self.phase)
        self.place(item, agent)
        self.row.append({"site": item["site"], "key": item["key"], "agent_hours": _hours(agent)})

    def tier_phase(self, tier):
        """Phases 1 to 4: the sites with a ready leaf of the tier of rate above
        0, by the most urgent such leaf's effective priority then config
        order, fixed now, each get one block for those leaves; then the sites
        with a ready rate-0 leaf of the tier, ordered the same way by their
        rate-0 leaves, put those leaves in the lanes row. A leaf's priority
        orders only the pass that places it."""
        for name in self.tier_sites(tier, rated=True):
            self.block_pass(name, self.order[name][tier])
        self.block_pools_run_out(self.names, tier)
        self.phase += 1
        for name in self.tier_sites(tier, rated=False):
            for item in self.order[name][tier]:
                if not item["ppm"] and self.room(item) > 0:
                    self.row_place(item)
        self.phase += 1

    def tier_sites(self, tier, rated):
        """The sites with a ready leaf of `tier` of rate above 0 (`rated`) or of
        rate 0, by the most urgent such leaf's effective priority, then config order."""
        leaves = {name: [item for item in self.order[name][tier] if bool(item["ppm"]) == rated]
                  for name in self.names}
        return [name for _, _, name in sorted(
            (schedule.pick_ready(leaves[name])["eff_priority"], self.position[name], name)
            for name in self.names if leaves[name])]

    # --- fair share --------------------------------------------------------------------

    def fitting(self, name, rated, attention=None, work=None):
        """The site's ready fair_share leaves of rate above 0 (`rated`) or of
        rate 0 that fit, lazily, in order; `attention` and `work` as for fits."""
        return (item for item in self.order[name][TIER_FAIR]
                if bool(item["ppm"]) == rated and self.fits(item, attention, work))

    def others_fit(self, name, members, attention, work) -> bool:
        """Whether another site of the class has a ready fair_share leaf of rate
        above 0 that fits at `attention` left with the open block at `work`."""
        return any(any(self.fitting(other, True, attention, work)) for other in members if other != name)

    def cap(self, name, members):
        """(operator minutes the open block of `name` may still place now, of
        which those the extension bound allows): it may reach block_default
        over the whole block, then extends a minute at a time while
        charged + 1 - target after that minute <= block_default, or while no
        other site of the class has a leaf that fits. The bound's step is
        closed-form: the site's charge less target grows by 1 - its gain a
        minute, and the other sites' fit only shrinks as minutes are placed."""
        attention, work, default = self.attention, self.open["work"], self.capacity.block_default
        free = min(attention, max(0, default - work))
        gain = self.accrual(name, POOL_ATTENTION, self.phase)[0].get(name, 0)
        if gain >= 1:
            return attention, attention
        slack = default - (self.ledger[POOL_ATTENTION]["charged"][name] + free
                           - self.ledger[POOL_ATTENTION]["target"][name] - free * gain)
        bound = free + min(attention - free, max(0, math.floor(slack / (1 - gain))))
        if bound < attention and not self.others_fit(name, members, attention - bound, work + bound):
            return attention, bound
        return bound, bound

    def capped_pass(self, name, members, budget=None):
        """Place `name`'s fair_share leaves of rate above 0 in its open block,
        in order, each in whole agent minutes against the smaller of the day's
        attention and the block's cap. With a `budget`, that many operator
        minutes and at least one agent minute stand in for the cap. Returns
        the agent minutes placed."""
        placed = 0
        lifted = budget is not None
        for item in self.order[name][TIER_FAIR]:
            if not item["ppm"]:
                continue
            want = self.room(item)
            if want <= 0:
                continue
            fit = _agent_fit(self.attention, item["ppm"])
            if lifted:
                cap = max(budget, _operator(1, item["ppm"]) if not placed else 0)
            else:
                cap, bound = self.cap(name, members)
            agent = min(want, _agent_fit(min(self.attention, cap), item["ppm"]))
            # Stopped at the day's attention only when it, not the cap, cut the leaf.
            self.stopped = self.stopped or (fit < want and agent == fit)
            if agent <= 0:
                continue
            operator = self.put(item, agent, fit)
            placed += agent
            if lifted:
                budget = max(0, budget - operator)
            elif operator > bound and self.trace is not None:
                self.trace.append({"flag": True, "date": self.text, "site": name})
        return placed

    def fair_blocks(self, members):
        """Phase 5 among the share sites, phase 7 among the share-less ones:
        while a site of the class has a ready fair_share leaf of rate above 0
        that fits, the one with the largest attention deficit, ties to config
        order, continues or opens its block."""
        while True:
            fitting = [name for name in members if any(self.fitting(name, True))]
            if not fitting:
                return
            name = max(fitting, key=lambda n: (self.deficit(n, POOL_ATTENTION), -self.position[n]))
            if not self.open_for(name):
                self.open_block(name)
                if not self.capped_pass(name, members):
                    # A block that opens holds at least one agent minute.
                    self.capped_pass(name, members, budget=0)
            elif not self.capped_pass(name, members):
                # The continue rule: the open block's site can place no agent
                # minute under its cap while another site of the class fits.
                if self.trace is not None:
                    self.trace.append({"lift": True, "date": self.text, "site": name})
                self.capped_pass(name, members, budget=self.capacity.block_default)

    def fair_row(self, members):
        """Phase 6 among the share sites, phase 8 among the share-less ones:
        while lane minutes are left and a site of the class has a ready
        fair_share rate-0 leaf that fits, the one with the largest lane
        deficit, ties to config order, places its first such leaf."""
        while self.lanes > 0:
            first = {name: next(self.fitting(name, False), None) for name in members}
            first = {name: item for name, item in first.items() if item is not None}
            if not first:
                return
            name = max(first, key=lambda n: (self.deficit(n, POOL_LANES), -self.position[n]))
            self.row_place(first[name])

    def fair_phase(self, members):
        """Phases 5 and 6 among the share sites, 7 and 8 among the share-less
        ones: the class's fair_share blocks, then its lanes row."""
        self.fair_blocks(members)
        self.block_pools_run_out(members, TIER_FAIR)
        self.phase += 1
        self.fair_row(members)
        self.phase += 1

    def block_pools_run_out(self, members, tier):
        """Add to the day's pools those that ran out while a ready leaf of rate
        above 0 of `tier` and a site in `members` still waited, judged right
        after that tier's blocks and before its lanes row, against the block
        open then: a leaf of a tier is never placed after its blocks' phase.

        Lanes: no lane minute is left. Attention: a block stopped at the
        day's attention while its leaf could take a lane minute; or, with a
        lane minute free, the leaf's site's block is open without room for
        one more agent minute of it, or no block of its site could open now,
        for want of block_min_hours or of the start cost plus one agent minute
        (docs/week-plan.md). With no lane minute free, attention ran out too
        only when one placement took the last of both pools.
        """
        ran = self.ran
        if self.stopped:
            ran.add(POOL_ATTENTION)
        for name in members:
            for item in self.order[name][tier]:
                if not item["ppm"] or not self.waiting(item):
                    continue
                if self.lanes == 0:
                    ran.add(POOL_LANES)
                if self.open_for(name):
                    short = _agent_fit(self.attention, item["ppm"]) < 1
                else:
                    short = not self.block_fits(item["ppm"])
                if short and (self.lanes > 0 or self.tied):
                    ran.add(POOL_ATTENTION)

    def plan(self) -> dict:
        """Plan the day and return its record. The items and the ledger change in place."""
        capacity = self.capacity
        self.tier_phase(TIER_P0)  # phases 1 and 2
        self.tier_phase(TIER_STARTED)  # phases 3 and 4
        self.fair_phase([name for name in self.names if name in self.shares])  # phases 5 and 6
        self.fair_phase(self.shareless)  # phases 7 and 8
        self.close()
        ran = self.ran
        if any(not item["ppm"] and self.waiting(item) for name in self.names for item in self.ready(name)):
            ran.add(POOL_LANES)
        for item in self.today:
            item["today"] = 0
            if item["left"] == 0:
                item["finished"] = self.index
        return {
            "date": self.text, "weekday": self.weekday,
            "attention_hours_declared": _hours(capacity.declared[self.weekday]),
            "attention_hours": _hours(capacity.attention[self.weekday]),
            "attention_hours_lost_to_cap": _hours(capacity.lost[self.weekday]),
            "reserved": [dict(block) for block in capacity.reserved[self.weekday]],
            "lane_hours": _hours(capacity.lanes * capacity.lane_minutes),
            "blocks": self.blocks, "lanes_row": self.row,
            "left": {"attention_hours": _hours(self.attention), "lane_hours": _hours(self.lanes)},
            "ran_out": [pool for pool in POOLS if pool in ran],
            "unestimated": sum(item["unestimated"] for item in self.today),
        }


def _binding(days):
    """(each pool's days run out in the planned week, the binding record).

    The binding pool ran out on the earliest day; on the same day, on more
    days; still tied, operator attention hours.
    """
    week = days[:WEEK_DAYS]
    run_out = {pool: [day["date"] for day in week if pool in day["ran_out"]] for pool in POOLS}
    ran = [pool for pool in POOLS if run_out[pool]]
    if not ran:
        return run_out, {"pool": None, "first_day": None, "text": NO_POOL}
    pool = min(ran, key=lambda pool: (run_out[pool][0], -len(run_out[pool])))
    return run_out, {"pool": pool, "first_day": run_out[pool][0], "text": pool}


def _leaf_record(item) -> dict:
    attention = item["attention"]
    return {
        "site": item["site"], "key": item["key"], "rate": item["ppm"] / PPM, "need": attention.need,
        "tag": attention.tag, "basis": attention.basis, "agent_hours": _hours(item["need"]),
        "unestimated": item["unestimated"], "state": item["state"],
        "hours_after_horizon": _hours(item["left"]), "days": list(item["days"]),
        "unresolved_local": item["unresolved_local"], "tier": item["tier"],
    }


def _capacity_record(capacity, available, declared_ratio, paused) -> dict:
    """The plan's `capacity` object: what devagent's declaration `capacity` is
    to the plan, with the contract's schema once anything is declared, and
    the attention and lanes of `available`, the capacity planned from. That
    is None on a no_plan plan: such a plan has no `attention` and no `lanes`.
    `declared_ratio` is _declared_ratio's record and `paused` the paused
    built sites; they and the token budget are a used declaration's, held
    whether or not there is a plan, and null, null and empty with any other.

    `attention` is the rule and, as `weekdays_lost_to_cap`, each weekday's
    hours that do not fit in the day, Monday first, the weekdays with a
    loss only. A day of the plan holds its own weekday's loss too; a
    weekday that keeps no minute is no day of the plan, so its loss is
    held here alone."""
    declaration = _declaration(capacity)
    attention = lanes = None
    if available is not None:
        attention = dict(available.rule) | {"weekdays_lost_to_cap": [
            {"weekday": day, "hours": _hours(available.lost[day])} for day in WEEKDAYS if available.lost[day] > 0]}
        lanes = {"max_lanes": available.lanes, "lane_hours_per_week": available.lane_week,
                 "working_days": sum(1 for day in WEEKDAYS if available.attention[day] > 0),
                 "lane_hours_per_day": _hours(available.lane_minutes),
                 "hours_lost_to_cap": _hours(available.lane_lost)}
    return {"declaration": declaration,
            "capacity_schema": None if declaration == NOT_DECLARED else capacity.capacity_schema,
            "attention": attention, "lanes": lanes, "declared_ratio": declared_ratio,
            "token_budget": _token_budget(capacity), "paused": list(paused)}


def plan_week(result, portfolio, config, *, capacity=None, trace=None) -> WeekPlan:
    """The week plan of one full build.

    `result` is build_all's BuildResult, `portfolio` the portfolio file as
    read (its settings_read and capacity) or None, and `config` gives as_of
    and default_unit_hours. A built site is one with a page in result.pages,
    in config order. Never raises on a portfolio file's content. A `trace`
    list gets each day's charges, lifts and flags (_Day); the plan is the same.

    `capacity` is devagent's capacity declaration (gantry/capacity.py's
    DeclaredCapacity) or None (#39). A used one, declared and without a
    violation, gives the week's attention and the lanes in place of the
    portfolio file's (_declared_capacity) and is both pools' source. Its
    operator_hours_per_agent_hour, when it can be a rate, is every site's
    declared ratio: the rate of each leaf with no h: score
    (_declared_ratio). A site it pauses stays a site of the plan with every
    open leaf unplanned as paused: nothing is placed for it, and it accrues
    no share, so its share goes to the sites that can use it (_Day.accrual).
    Its token budget is recorded and never planned with, and a project's
    priority, lane cap, reserves and deadline are not read.

    None and a declaration that declares nothing give the same plan, from
    the portfolio file's capacity. So does an invalid one, whatever it
    holds, apart from what the plan says of it: the `capacity` object's
    `declaration`, a no_plan reason that names it, and devagent's lines,
    which lead the report of any plan (_report).
    """
    mapping = mapping_in_use(portfolio)
    fields = {
        "plan_version": PLAN_VERSION, "as_of": config.as_of.isoformat(), "allocation_rule": ALLOCATION_RULE,
        "mapping": {"values": list(mapping.values), "source": mapping.source, "rejected": mapping.rejected},
    }
    names = _built_sites(result)
    ratio, ratio_record = _declared_ratio(capacity)
    paused = _paused_sites(capacity, names)
    key, available = _no_plan(portfolio, result, capacity)
    if key is not None:
        run_out, binding = _binding([])
        return WeekPlan(status="no_plan", reason=_reason(key, capacity), unestimated_planned=0, horizon_days=[],
                        planned_week=[], pools=_pools(run_out, capacity), binding=binding,
                        capacity=_capacity_record(capacity, None, ratio_record, paused), days=[], leaves=[],
                        sites=[], report=_report(capacity), notes=[], **fields)
    ranked = {name: _ranked(result.inputs[name].data) for name in names}
    items = {name: _site_work(name, result.inputs[name], mapping, config.default_unit_hours, ranked[name][1],
                              declared=ratio)
             for name in names}
    _cross_site_preds(result, items)
    by_node = {(name, item["key"]): item for name in names for item in items[name]}
    cycles = _cross_site_cycles(by_node, paused)
    reasons = _reasons(by_node, cycles, items.keys(), paused)
    # A planned leaf's predecessors are all planned: hold them as items.
    for node, item in by_node.items():
        item["reason"] = reasons.get(node)
        item["preds"] = [by_node[pred] for pred in item["preds"]]
    work = {name: [item for item in items[name] if item["reason"] is None] for name in names}
    horizon = _working_days(config.as_of, available)
    day_shares = [_day_shares(portfolio, day, names) for day in horizon]
    placed_order = []
    # One ledger per site and pool for the horizon: whole minutes charged, an exact target.
    ledger = {pool: {"charged": dict.fromkeys(names, 0), "target": dict.fromkeys(names, Fraction(0))}
              for pool in POOLS}
    days = [_Day(index, day, available, names, work, placed_order, ledger, shares, trace).plan()
            for index, (day, shares) in enumerate(zip(horizon, day_shares))]
    run_out, binding = _binding(days)
    placed_by_site = {name: [] for name in names}
    for item in placed_order:
        placed_by_site[item["site"]].append(item)
    leaves = [_leaf_record(item) for name in names for item in placed_by_site[name]]
    sites = []
    for name in names:
        placed = placed_by_site[name]
        shareless_days = [day.isoformat() for day, found in zip(horizon, day_shares) if name in found.shareless]
        sites.append({
            "name": name, "ratio_source": site_ratio_source(declared=ratio),
            # Block lengths (start cost and minimum-length padding included) and agent hours placed.
            "attention_hours_charged": _hours(ledger[POOL_ATTENTION]["charged"][name]),
            "lane_hours_charged": _hours(ledger[POOL_LANES]["charged"][name]),
            # Each pool's target at the horizon's end (docs/week-plan.md, Fair share between sites).
            "attention_hours_target": float(_hours(ledger[POOL_ATTENTION]["target"][name])),
            "lane_hours_target": float(_hours(ledger[POOL_LANES]["target"][name])),
            "unestimated_planned": sum(item["unestimated"] for item in placed),
            # Never placed in the 10 days: beyond the horizon.
            "unplanned": [{"key": item["key"], "reason": item["reason"] or BEYOND_HORIZON, "state": item["state"],
                           "unresolved_local": item["unresolved_local"]}
                          for item in items[name] if not item["days"]],
            "shares": [{"date": day.isoformat(),
                        "share": float(100 * found.shares[name]) if name in found.shares else None}
                       for day, found in zip(horizon, day_shares)],
            "shareless_days": shareless_days,
            "share_note": SHARE_NOTE if shareless_days else None,
            "p0": _p0_entries(*ranked[name]),
        })
    dates = [day.isoformat() for day in horizon]
    return WeekPlan(status="planned", reason=None,
                    unestimated_planned=sum(item["unestimated"] for item in placed_order),
                    horizon_days=dates, planned_week=dates[:WEEK_DAYS], pools=_pools(run_out, capacity),
                    binding=binding, capacity=_capacity_record(capacity, available, ratio_record, paused),
                    days=days, leaves=leaves, sites=sites, report=_report(capacity, cycles),
                    notes=[note for found in day_shares for note in found.notes], **fields)


def plan_to_json(plan) -> str:
    """The plan file's text: JSON with sorted keys, non-ASCII text as itself
    and one trailing newline. The same plan gives the same text anywhere."""
    return json.dumps(asdict(plan), sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def write_plan(plan, output_dir, *, roots, text=None):
    """Write <output_dir>/week-plan.json as UTF-8 under the run's `roots`,
    through a temporary file in that folder and a rename (write_confined):
    no partial file. `text` is plan_to_json(plan) when the caller made it
    earlier, before a view could touch the plan. It makes no folder and
    catches nothing: OSError and OutsideRoots are the caller's."""
    if text is None:
        text = plan_to_json(plan)
    write_confined(roots, output_dir / PLAN_FILE, text.encode("utf-8"), key="output_dir")
