"""The dependency network a worksite page draws (#9).

Nodes are the given keys with a link to another of them; a link is dropped
when either end is not a key, or when a longer chain already implies it
(transitive reduction). Columns are the earliest slot with one slot per node,
slack is how many slots a node can slip before the network gets longer, and an
edge crosses when its ends are in different lanes. No recursion anywhere.
"""

import heapq
from dataclasses import dataclass


@dataclass(frozen=True)
class Network:
    nodes: tuple[str, ...]  # in keys order
    edges: tuple[tuple[str, str], ...]  # in links order
    es: dict[str, int]  # 0-based earliest slot
    slack: dict[str, int]
    length: int  # the number of slots
    critical: tuple[str, ...]  # slack 0, in topological order
    crit_edges: tuple[tuple[str, str], ...]
    cross: frozenset[tuple[str, str]]


def _reaches(succ, start, goal, skip):
    """Whether goal is reachable from start without the edge (start, skip)."""
    stack = [w for w in succ[start] if w != skip]
    seen = set()
    while stack:
        w = stack.pop()
        if w == goal:
            return True
        if w in seen:
            continue
        seen.add(w)
        stack.extend(succ[w])
    return False


def _order(nodes, edges):
    """Kahn's order, ties in nodes order; ValueError on a cycle."""
    index = {n: i for i, n in enumerate(nodes)}
    succ = {n: [] for n in nodes}
    indeg = dict.fromkeys(nodes, 0)
    for u, v in edges:
        succ[u].append(v)
        indeg[v] += 1
    heap = [index[n] for n in nodes if indeg[n] == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        n = nodes[heapq.heappop(heap)]
        order.append(n)
        for v in succ[n]:
            indeg[v] -= 1
            if indeg[v] == 0:
                heapq.heappush(heap, index[v])
    if len(order) != len(nodes):
        raise ValueError("network needs acyclic links; leave held-out leaves out")
    return order


def build(keys, links, lane_of) -> Network:
    """The network over `keys`; `lane_of` maps each key to its lane."""
    inside = set(keys)
    kept = list(dict.fromkeys((u, v) for u, v in links if u in inside and v in inside))
    ends = {k for link in kept for k in link}
    nodes = tuple(k for k in keys if k in ends)
    succ = {n: [] for n in nodes}
    for u, v in kept:
        succ[u].append(v)
    edges = tuple((u, v) for u, v in kept if not _reaches(succ, u, v, v))
    preds = {n: [] for n in nodes}
    after = {n: [] for n in nodes}
    for u, v in edges:
        preds[v].append(u)
        after[u].append(v)
    order = _order(nodes, edges)
    es = {}
    for n in order:
        es[n] = max((es[p] + 1 for p in preds[n]), default=0)
    length = max(es.values(), default=-1) + 1
    ls = {}
    for n in reversed(order):
        ls[n] = min((ls[s] - 1 for s in after[n]), default=length - 1)
    slack = {n: ls[n] - es[n] for n in nodes}
    crit_edges = tuple((u, v) for u, v in edges
                       if slack[u] == 0 and slack[v] == 0 and es[v] == es[u] + 1)
    cross = frozenset((u, v) for u, v in edges if lane_of[u] != lane_of[v])
    return Network(nodes, edges, {n: es[n] for n in nodes}, slack, length,
                   tuple(n for n in order if slack[n] == 0), crit_edges, cross)


def heaviest_chain(net: Network, hours) -> tuple[tuple[str, ...], float]:
    """(keys, total hours) of the chain of edges with the most hours, by `hours[key]`."""
    preds = {n: [] for n in net.nodes}
    for u, v in net.edges:
        preds[v].append(u)
    best, back = {}, {}
    order = _order(net.nodes, net.edges)
    for n in order:
        p = max(preds[n], key=lambda q: best[q], default=None)
        best[n] = (best[p] if p is not None else 0.0) + hours[n]
        back[n] = p
    if not order:
        return (), 0.0
    tail = max(order, key=lambda n: best[n])
    total = best[tail]
    chain = []
    while tail is not None:
        chain.append(tail)
        tail = back[tail]
    return tuple(reversed(chain)), total
