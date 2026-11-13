"""The week planner (#61): the next 10 working days laid onto blocks per day.

plan_week plans every built site's open leaves from the build's resolved
inputs, the portfolio file's capacity and each leaf's attention rate
(gantry/attention.py). It reads no file and no clock and works in whole
minutes, so the same inputs and as-of date give the same plan on any
machine. docs/week-plan.md states the rules.
"""

import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from graphlib import TopologicalSorter

from gantry import priority, schedule
from gantry.deps import open_sources
from gantry.attention import MAX_RATE, leaf_attention, mapping_in_use, site_ratio_source
from gantry.inputs import scheduled
from gantry.model import HOURS_PER_DAY
from gantry.portfolio_file import DEFAULTS, WEEKDAYS
from gantry.roots import write_confined

PLAN_VERSION = 1
PLAN_FILE = "week-plan.json"
ALLOCATION_RULE = "most urgent site first; intended shares not applied"
POOL_ATTENTION = "operator attention hours"
POOL_LANES = "agent lanes"
POOLS = (POOL_ATTENTION, POOL_LANES)
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

# Why an open leaf of a built site is not in the 10 days: the first that applies.
UNPLANNED_REASONS = ("blocked_externally", "parked", "waits_on_cycle", "waits_on_other_site",
                     "waits_on_unplanned", "beyond_horizon")
(BLOCKED_EXTERNALLY, PARKED, WAITS_ON_CYCLE, WAITS_ON_OTHER_SITE,
 WAITS_ON_UNPLANNED, BEYOND_HORIZON) = UNPLANNED_REASONS

# The site report's kinds of a local depends_on entry that names nothing (#8):
# no dependency in the plan, only counted on the leaf's record (#62).
LOCAL_UNRESOLVED_KINDS = ("unresolved_ref", "malformed_ref")

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

    Frozen at the top level only: every view of a build gets this one object,
    so a view must not change its lists or dicts.

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
    report: list


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
            for name in POOLS]


def _reasons(data, open_leaves, preds) -> dict:
    """{key: reason} for each open leaf the plan leaves out whatever the
    capacity: the first reason that applies, in UNPLANNED_REASONS' order."""
    held = set(data.held_out)
    reasons = {}
    for leaf in open_leaves:
        if leaf.state == "blocked_external":
            reasons[leaf.key] = BLOCKED_EXTERNALLY
        elif leaf.state == "parked":
            reasons[leaf.key] = PARKED
        elif leaf.key in held:
            reasons[leaf.key] = WAITS_ON_CYCLE
        elif leaf.waits_on_other_site:
            reasons[leaf.key] = WAITS_ON_OTHER_SITE
    # The rest hold no cycle, so each leaf is reached after its predecessors.
    rest = TopologicalSorter({leaf.key: [p for p in preds[leaf.key] if p not in reasons]
                              for leaf in open_leaves if leaf.key not in reasons})
    for key in rest.static_order():
        if any(p in reasons for p in preds[key]):
            reasons[key] = WAITS_ON_UNPLANNED
    return reasons


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


def _site_work(name, inputs, mapping, unit) -> list[dict]:
    """One item per open leaf of a site, in WBS.md order. An item with no
    `reason` is work for the day loop, and a schedule.pick_ready item.

    Effective priority is the worksite page's (every scheduled leaf, with
    blocked, parked and cross-site waiters), so an unplanned dependent still
    lifts its blocker. A leaf's predecessors are its open prerequisites.
    """
    data = inputs.data
    open_leaves = [leaf for leaf in data.leaves if leaf.open]
    effective = priority.effective(scheduled(data), data.links)
    preds = {leaf.key: [] for leaf in open_leaves}
    for before, after in data.links:
        if before in preds and after in preds:
            preds[after].append(before)
    reasons = _reasons(data, open_leaves, preds)
    unresolved = _unresolved_local(data, open_leaves)
    index = {leaf.key: i for i, leaf in enumerate(data.leaves)}
    items = []
    for leaf in open_leaves:
        estimate = leaf.estimate
        hours = (estimate.lo_h + estimate.hi_h) / 2 if estimate is not None else unit
        attention = leaf_attention(leaf, data, mapping)
        need = _leaf_minutes(hours)
        items.append({
            "site": name, "key": leaf.key, "wbs_index": index[leaf.key],
            "eff_priority": effective.get(leaf.key, priority.rank(leaf.priority)),
            "preds": preds[leaf.key], "reason": reasons.get(leaf.key), "finished": None,
            "need": need, "left": need, "ppm": _ppm(attention.rate), "attention": attention,
            "unestimated": estimate is None or estimate.source == "default", "state": leaf.state,
            "days": [], "today": 0, "unresolved_local": unresolved[leaf.key],
        })
    # A planned leaf's predecessors are all planned: hold them as items.
    by_key = {item["key"]: item for item in items}
    for item in items:
        item["preds"] = [by_key[key] for key in item["preds"]]
    return items


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
    """One day's pools and placements while it is planned."""

    def __init__(self, index, day, capacity, names, work, placed_order, charged):
        self.index, self.text, self.capacity, self.names = index, day.isoformat(), capacity, names
        self.weekday = WEEKDAYS[day.weekday()]
        self.attention = capacity.attention[self.weekday]
        self.lanes = capacity.lanes * LANE_MINUTES
        self.placed_order, self.charged = placed_order, charged
        self.today = []
        # Whether the placement that took the last lane minute also took the
        # last of the attention its block could give that leaf.
        self.tied = False
        # Whether a block stopped at the day's attention while its leaf could
        # still take a lane minute. Recorded then: a later placement that takes
        # the last lane minute does not hide it.
        self.stopped = False
        # Each site's ready leaves in plan order, fixed for the day: a leaf
        # is ready when every predecessor finished on an earlier day.
        # Leaves started on an earlier day go first, then the others.
        self.order = {}
        for name in names:
            ready = [item for item in work[name] if item["left"] > 0
                     and all(p["finished"] is not None and p["finished"] < index for p in item["preds"])]
            started = [item for item in ready if item["days"]]
            fresh = [item for item in ready if not item["days"]]
            self.order[name] = _site_order(started) + _site_order(fresh)

    def room(self, item) -> int:
        """The agent minutes the leaf could still take today, lanes allowing."""
        return min(item["left"], LANE_MINUTES - item["today"], self.lanes)

    def block_fits(self, ppm) -> bool:
        """Whether attention left could open a block holding one agent minute at rate `ppm`."""
        return (self.attention >= self.capacity.block_min
                and _agent_fit(self.attention - self.capacity.start_cost, ppm) >= 1)

    def waiting(self, item) -> bool:
        """Whether a ready leaf still has minutes to place and is under its 6 agent hours today."""
        return item["left"] > 0 and item["today"] < LANE_MINUTES

    def place(self, item, room):
        if not item["days"]:
            self.placed_order.append(item)
        item["left"] -= room
        item["today"] += room
        item["days"].append(self.text)
        self.lanes -= room
        self.today.append(item)

    def opens_block(self, item) -> bool:
        """Whether a block of its site could open now and hold one agent minute of the leaf."""
        return item["ppm"] > 0 and self.waiting(item) and self.lanes > 0 and self.block_fits(item["ppm"])

    def next_block_site(self, closed):
        """The site with no block yet whose most urgent fitting leaf of rate
        above 0 is the most urgent; ties to config order. None: no block can open."""
        best = None
        for name in self.names:
            if name in closed:
                continue
            fitting = [item for item in self.order[name] if self.opens_block(item)]
            if not fitting:
                continue
            urgency = schedule.pick_ready(fitting)["eff_priority"]
            if best is None or urgency < best[0]:
                best = (urgency, name)
        return best[1] if best is not None else None

    def blocks(self):
        """(the day's blocks, {site: the operator minutes its closed block had left})."""
        capacity = self.capacity
        blocks, closed = [], {}
        while (name := self.next_block_site(closed)) is not None:
            room = self.attention - capacity.start_cost  # operator minutes for the block's work
            leaves = []
            # One pass: nothing regains room within a day.
            for item in self.order[name]:
                if not item["ppm"]:
                    continue
                fit = _agent_fit(room, item["ppm"])
                want = self.room(item)
                self.stopped = self.stopped or fit < want
                agent = min(want, fit)
                if agent <= 0:
                    continue
                self.tied = agent == self.lanes == fit
                operator = _operator(agent, item["ppm"])
                self.place(item, agent)
                room -= operator
                leaves.append({"site": name, "key": item["key"], "agent_hours": _hours(agent),
                               "operator_hours": _hours(operator)})
            length = max(capacity.block_min, self.attention - room)
            self.attention -= length
            self.charged[name] += length
            closed[name] = room
            blocks.append({"number": len(blocks) + 1, "site": name, "length_hours": _hours(length),
                           "start_cost_hours": _hours(capacity.start_cost), "leaves": leaves})
        return blocks, closed

    def block_pools_run_out(self, closed) -> set:
        """The pools that ran out while a ready leaf of rate above 0 still waited.

        Lanes: no lane minute is left. Attention: a block stopped at the
        day's attention while its leaf could take a lane minute; or, with a
        lane minute free, the leaf's site's block closed without room for
        one more agent minute of it, or no block of its site could open, for
        want of block_min_hours or of the start cost plus one agent minute
        (docs/week-plan.md). With no lane minute free, attention ran out
        too only when one placement took the last of both pools.
        """
        ran = {POOL_ATTENTION} if self.stopped else set()
        for name in self.names:
            for item in self.order[name]:
                if not item["ppm"] or not self.waiting(item):
                    continue
                if self.lanes == 0:
                    ran.add(POOL_LANES)
                if name in closed:
                    short = _agent_fit(closed[name], item["ppm"]) < 1
                else:
                    short = not self.block_fits(item["ppm"])
                if short and (self.lanes > 0 or self.tied):
                    ran.add(POOL_ATTENTION)
        return ran

    def lanes_row(self):
        """(the day's lanes row, whether a ready rate-0 leaf still waited for a lane)."""
        sites = []
        for position, name in enumerate(self.names):
            fitting = [item for item in self.order[name] if not item["ppm"] and self.waiting(item) and self.lanes > 0]
            if fitting:
                sites.append((schedule.pick_ready(fitting)["eff_priority"], position, name))
        row = []
        for _, _, name in sorted(sites):  # the site order is fixed here, once
            for item in self.order[name]:
                agent = self.room(item)
                if item["ppm"] or agent <= 0:
                    continue
                self.place(item, agent)
                row.append({"site": name, "key": item["key"], "agent_hours": _hours(agent)})
        waited = any(not item["ppm"] and self.waiting(item) for name in self.names for item in self.order[name])
        return row, waited

    def plan(self) -> dict:
        """Plan the day and return its record. The items change in place, and
        `charged` gains each site's block minutes."""
        capacity = self.capacity
        blocks, closed = self.blocks()
        ran = self.block_pools_run_out(closed)
        row, waited = self.lanes_row()
        if waited:
            ran.add(POOL_LANES)
        for item in self.today:
            item["today"] = 0
            if item["left"] == 0:
                item["finished"] = self.index
        return {
            "date": self.text, "weekday": self.weekday,
            "attention_hours_declared": _hours(capacity.declared[self.weekday]),
            "attention_hours": _hours(capacity.attention[self.weekday]),
            "reserved": [dict(block) for block in capacity.reserved[self.weekday]],
            "lane_hours": _hours(capacity.lanes * LANE_MINUTES),
            "blocks": blocks, "lanes_row": row,
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
        "unresolved_local": item["unresolved_local"],
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
        run_out, binding = _binding([])
        return WeekPlan(status="no_plan", reason=NO_PLAN[key], unestimated_planned=0, horizon_days=[],
                        planned_week=[], pools=_pools(run_out), binding=binding,
                        days=[], leaves=[], sites=[], report=[], **fields)
    names = _built_sites(result)
    items = {name: _site_work(name, result.inputs[name], mapping, config.default_unit_hours) for name in names}
    work = {name: [item for item in items[name] if item["reason"] is None] for name in names}
    horizon = _working_days(config.as_of, capacity)
    placed_order = []
    charged = dict.fromkeys(names, 0)
    days = [_Day(index, day, capacity, names, work, placed_order, charged).plan()
            for index, day in enumerate(horizon)]
    run_out, binding = _binding(days)
    placed_by_site = {name: [] for name in names}
    for item in placed_order:
        placed_by_site[item["site"]].append(item)
    leaves = [_leaf_record(item) for name in names for item in placed_by_site[name]]
    sites = []
    for name in names:
        placed = placed_by_site[name]
        sites.append({
            "name": name, "ratio_source": site_ratio_source(),
            # Block lengths (start cost and minimum-length padding included) and agent hours placed.
            "attention_hours_charged": _hours(charged[name]),
            "lane_hours_charged": _hours(sum(item["need"] - item["left"] for item in placed)),
            "unestimated_planned": sum(item["unestimated"] for item in placed),
            # Never placed in the 10 days: beyond the horizon.
            "unplanned": [{"key": item["key"], "reason": item["reason"] or BEYOND_HORIZON, "state": item["state"],
                           "unresolved_local": item["unresolved_local"]}
                          for item in items[name] if not item["days"]],
        })
    dates = [day.isoformat() for day in horizon]
    return WeekPlan(status="planned", reason=None,
                    unestimated_planned=sum(item["unestimated"] for item in placed_order),
                    horizon_days=dates, planned_week=dates[:WEEK_DAYS], pools=_pools(run_out), binding=binding,
                    days=days, leaves=leaves, sites=sites, report=[], **fields)


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
