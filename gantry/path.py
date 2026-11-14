"""The path to a requirement's work (#77): a plain function over leaf data.

For each requirement or use case row of the trace, the path set is every
scheduled leaf its open serving leaves wait on, directly or through other
open leaves, together with its scheduled serving leaves (the targets). The
set is worked in one lane through the unchanged schedule.plan, and its
heaviest chain of waits is walked here, held to schedule.chain_hours.

Imports only gantry.schedule and the standard library. Plain loops and no
recursion: Python's float sum is compensated and the page script's is not,
and a WBS can hold chains deeper than the recursion limit.
"""

from dataclasses import dataclass

from gantry import schedule


@dataclass(frozen=True)
class ReqPath:
    """One row's path. `order` is (key, "direct" | "indirect" | "upstream")
    in work order; `chain` runs first to last."""
    state: str
    order: tuple[tuple[str, str], ...]
    targets: tuple[str, ...]
    chain: tuple[str, ...]
    chain_hours: float
    hours: float
    unestimated: int
    reasons: tuple[tuple[str, str | None, object], ...]


def path_set(targets, held, links, scheduled) -> set[str]:
    """The scheduled leaves reached from `targets` and `held` by following
    `links` ((prerequisite, dependent) pairs) backwards, the targets included.
    The walk passes through leaves that are not scheduled (held-out ones) but
    keeps only those in `scheduled`."""
    before = {}
    for a, b in links:
        before.setdefault(b, []).append(a)
    seen = set()
    stack = [*targets, *held]
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        stack.extend(before.get(key, ()))
    return {k for k in seen if k in scheduled}


def work_order(items) -> list[str]:
    """The keys of `items` in one-lane work order: schedule.plan(items, 1), by start."""
    planned = schedule.plan(items, 1)
    return sorted(planned, key=lambda k: planned[k][0])


def _earlier(a, b, rank):
    """Whether item `a` wins a tie over `b`: the lower wbs_index, then the earlier item."""
    return (a["wbs_index"], rank[a["key"]]) < (b["wbs_index"], rank[b["key"]])


def heaviest(items) -> tuple[tuple[str, ...], float]:
    """(keys first to last, hours) of the heaviest chain of waits over every
    item, unlinked ones included: schedule.chain_hours's walk, step for step,
    so the hours are equal. A tie, among a leaf's predecessors or among chain
    ends, goes to the lower wbs_index, then to the earlier item."""
    if not items:
        return (), 0.0
    by_key = {item["key"]: item for item in items}
    rank = {item["key"]: i for i, item in enumerate(items)}
    succ = {item["key"]: [] for item in items}
    indeg = {item["key"]: len(item["preds"]) for item in items}
    for item in items:
        for pred in item["preds"]:
            succ[pred].append(item["key"])
    queue = [item["key"] for item in items if indeg[item["key"]] == 0]
    best, back = {}, {}
    head = 0
    while head < len(queue):
        key = queue[head]
        head += 1
        before, via = 0.0, None
        for pred in by_key[key]["preds"]:
            if via is None or best[pred] > before or (best[pred] == before
                                                       and _earlier(by_key[pred], by_key[via], rank)):
                before, via = max(before, best[pred]), pred
        best[key] = before + by_key[key]["hours"]
        back[key] = via
        for s in succ[key]:
            indeg[s] -= 1
            if indeg[s] == 0:
                queue.append(s)
    if len(queue) < len(items):
        raise ValueError("plan: no item can start; the waits hold a cycle")
    end = None
    for item in items:
        k = item["key"]
        if end is None or best[k] > best[end] or (best[k] == best[end] and _earlier(item, by_key[end], rank)):
            end = k
    chain = []
    k = end
    while k is not None:
        chain.append(k)
        k = back[k]
    chain.reverse()
    return tuple(chain), best[end]


def build_paths(serving, items, *, unestimated, links, foreign, scope,
                sites_not_read=None, other_serving=None) -> dict[str, ReqPath]:
    """{row id: ReqPath} for each row of `serving`.

    `serving` is {row id: (lit, held, finished_count)}: `lit` the row's
    scheduled serving leaves as (key, how), `held` its held-out serving leaf
    keys. `items` are the site's schedule items; a row's subset keeps their
    order and site-wide eff_priority, and its preds unfiltered, so a breach of
    the path set's closure raises in schedule.plan instead of hiding.
    `unestimated` is the set of unestimated keys; `links` the site's links.
    """
    scheduled = {item["key"] for item in items}
    out = {}
    for rid, (lit, held, _finished) in serving.items():
        how = {key: h for key, h in lit}
        targets = tuple(key for key, _ in lit)
        members = path_set(targets, held, links, scheduled)
        subset = [item for item in items if item["key"] in members]
        order = tuple((k, how.get(k, "upstream")) for k in work_order(subset))
        chain, chain_total = heaviest(subset)
        hours = 0.0
        for item in subset:
            hours += item["hours"]
        out[rid] = ReqPath(state="path", order=order, targets=targets, chain=chain, chain_hours=chain_total,
                           hours=hours, unestimated=sum(item["key"] in unestimated for item in subset),
                           reasons=())
    return out
