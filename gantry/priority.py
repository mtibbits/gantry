"""Priority: a priority: value's rank and a leaf's effective priority (#8).

P1 is the most urgent and P4 the least; a missing or invalid value counts as P3.
A leaf's effective priority is the most urgent of its own rank and every open
leaf that waits on it, directly or through other open leaves. Call it over the
leaves a page schedules:

    effective([l for l in s.leaves if l.open and l.key not in s.held_out], s.links)
"""

import re
from graphlib import CycleError, TopologicalSorter

from gantry.model import meta_scalar

PRIORITY_RE = re.compile(r"P[1-4]")
DEFAULT_RANK = 3


def _valid(raw):
    """The P1-P4 value text, or None."""
    value = meta_scalar(raw)
    return value if value is not None and PRIORITY_RE.fullmatch(value) else None


def is_valid(raw: str | None) -> bool:
    return _valid(raw) is not None


def rank(raw: str | None) -> int:
    # fullmatch first: int() is never called on unbounded text.
    value = _valid(raw)
    return int(value[1]) if value is not None else DEFAULT_RANK


def effective(leaves, links) -> dict[str, int]:
    """{key: effective rank} over `leaves`; links with an end outside them are
    ignored. Topological order, last dependent first: no recursion."""
    eff = {leaf.key: rank(leaf.priority) for leaf in leaves}
    dependents = {key: [] for key in eff}
    sorter = TopologicalSorter(dict.fromkeys(eff, ()))
    for a, b in links:
        if a in eff and b in eff:
            dependents[a].append(b)
            sorter.add(b, a)
    try:
        order = list(sorter.static_order())
    except CycleError:
        raise ValueError("effective needs acyclic links; leave held-out leaves out") from None
    for key in reversed(order):
        for d in dependents[key]:
            eff[key] = min(eff[key], eff[d])
    return eff
