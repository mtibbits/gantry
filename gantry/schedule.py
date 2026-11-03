"""The lane scheduler: a plain function over leaf data (#9).

An item is {key, hours, preds, eff_priority, wbs_index}. plan() is a
non-preemptive list schedule: the first lane to come free takes the ready
item with the most urgent effective priority, then the lowest wbs_index,
then the earliest in the input. gantry/static/schedule.js is its line-for-line
twin, and tests/test_schedule_agreement.py holds the two to the same result.
It imports nothing from gantry, so a later planner extends it rather than
forking it.
"""

import math


def _check(items):
    keys = set()
    for item in items:
        if item["key"] in keys:
            raise ValueError(f"plan: duplicate key {item['key']!r}")
        keys.add(item["key"])
    for item in items:
        for pred in item["preds"]:
            if pred not in keys:
                raise ValueError(f"plan: {item['key']!r} waits on {pred!r}, which is not an item")
        hours = item["hours"]
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours) or hours <= 0:
            raise ValueError(f"plan: {item['key']!r} has hours {hours!r}; hours must be a finite number above 0")


def plan(items, lanes) -> dict[str, tuple[float, float, int]]:
    """{key: (start, end, lane)}, hours from 0.0; ValueError on bad input or a cycle."""
    if isinstance(lanes, bool) or not isinstance(lanes, int) or lanes < 1:
        raise ValueError("plan: lanes must be a whole number from 1")
    _check(items)
    free = [0.0] * lanes
    out = {}
    pending = list(items)
    while pending:
        li = 0
        for i in range(1, lanes):
            if free[i] < free[li]:
                li = i
        now = free[li]
        ready = [item for item in pending if all(p in out and out[p][1] <= now for p in item["preds"])]
        if not ready:
            later = [end for _, end, _ in out.values() if end > now]
            if not later:
                raise ValueError("plan: no item can start; the waits hold a cycle")
            free[li] = min(later)
            continue
        pick = ready[0]
        for item in ready[1:]:
            if (item["eff_priority"], item["wbs_index"]) < (pick["eff_priority"], pick["wbs_index"]):
                pick = item
        end = now + pick["hours"]
        out[pick["key"]] = (now, end, li)
        free[li] = end
        pending.remove(pick)
    return out


def chain_hours(items) -> float:
    """The hours of the heaviest chain of waits, over every item."""
    _check(items)
    succ = {item["key"]: [] for item in items}
    indeg = {item["key"]: len(item["preds"]) for item in items}
    by_key = {item["key"]: item for item in items}
    for item in items:
        for pred in item["preds"]:
            succ[pred].append(item["key"])
    queue = [item["key"] for item in items if indeg[item["key"]] == 0]
    best = {}
    head = 0
    while head < len(queue):
        key = queue[head]
        head += 1
        before = 0.0
        for pred in by_key[key]["preds"]:
            before = max(before, best[pred])
        best[key] = before + by_key[key]["hours"]
        for s in succ[key]:
            indeg[s] -= 1
            if indeg[s] == 0:
                queue.append(s)
    if len(queue) < len(items):
        raise ValueError("plan: no item can start; the waits hold a cycle")
    return max(best.values(), default=0.0)


def limit(items, lanes) -> str:
    """"chain" when the plan ends with the heaviest chain, else "capacity"."""
    p = plan(items, lanes)
    end = max((e for _, e, _ in p.values()), default=0.0)
    return "chain" if end <= chain_hours(items) else "capacity"
