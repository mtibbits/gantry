"""Priority: a priority: value's rank (#8).

P1 is the most urgent and P4 the least; a missing or invalid value counts as P3.
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
