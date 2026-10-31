"""Priority: a priority: value's rank and a leaf's effective priority (#8).

P1 is the most urgent and P4 the least; a missing or invalid value counts as P3.
A leaf's effective priority is the most urgent of its own rank and every open
leaf that waits on it, directly or through other open leaves. Call it over the
leaves a page schedules:

    effective([l for l in s.leaves if l.open and l.key not in s.held_out], s.links)
"""

import re

from gantry.model import meta_value

PRIORITY_RE = re.compile(r"P[1-4]")
DEFAULT_RANK = 3


def is_valid(raw: str | None) -> bool:
    if raw is None:
        return False
    value = meta_value(raw)
    return isinstance(value, str) and PRIORITY_RE.fullmatch(value) is not None


def rank(raw: str | None) -> int:
    # fullmatch first: int() is never called on unbounded text.
    return int(meta_value(raw)[1]) if is_valid(raw) else DEFAULT_RANK


def effective(leaves, links) -> dict[str, int]:
    """{key: effective rank} over `leaves`; links with an end outside them are
    ignored. Kahn order, last dependent first: no recursion."""
    eff = {leaf.key: rank(leaf.priority) for leaf in leaves}
    dependents = {key: [] for key in eff}
    waiting = dict.fromkeys(eff, 0)
    for a, b in links:
        if a in eff and b in eff:
            dependents[a].append(b)
            waiting[b] += 1
    order = [key for key in eff if waiting[key] == 0]
    for key in order:
        for d in dependents[key]:
            waiting[d] -= 1
            if waiting[d] == 0:
                order.append(d)
    if len(order) < len(eff):
        raise ValueError("effective needs acyclic links; leave held-out leaves out")
    for key in reversed(order):
        for d in dependents[key]:
            eff[key] = min(eff[key], eff[d])
    return eff
