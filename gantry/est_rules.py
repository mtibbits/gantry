"""est: values, read by five classes: a stopgap for mtibbits/devagent#737 (#10).

An est: value is absent, a placeholder (`?`, `?w`), a time (`3h`, `1.5d`,
`2w`), a non-time quantity (`48pr`) or unparseable. Units are case-sensitive,
so `3H` is listed rather than summed. #13 deletes this module once devagent's
own classes reach the portfolio JSON. docs/worksite-page.md states the rules.
"""

import math
import re
from dataclasses import dataclass

from gantry.model import DAYS_PER_WEEK, HOURS_PER_DAY, meta_scalar

# Bounded digit groups: float() is exact on them and int() never sees a long run.
NUM = r"[0-9]{1,9}(?:\.[0-9]{1,9})?"
TIME_RE = re.compile(rf"(?P<number>{NUM})(?P<unit>[hdw])")
PLACEHOLDER_RE = re.compile(r"\?[A-Za-z]*")
NON_TIME_RE = re.compile(rf"(?P<number>{NUM})(?P<unit>[A-Za-z]+)")
HOURS_PER_UNIT = {"h": 1, "d": HOURS_PER_DAY, "w": HOURS_PER_DAY * DAYS_PER_WEEK}


@dataclass(frozen=True)
class EstValue:
    kind: str  # "absent", "placeholder", "time", "non_time" or "unparseable"
    lo_h: float | None = None
    hi_h: float | None = None
    number: float | None = None
    unit: str | None = None


def _positive(text):
    """The number text as a float above 0, else None."""
    x = float(text)
    return x if math.isfinite(x) and x > 0 else None


def classify_est(raw: str | None) -> EstValue:
    """An est: raw text's class; a time value's hours are lo_h == hi_h."""
    if raw is None:
        return EstValue("absent")
    value = meta_scalar(raw)
    if not value:
        return EstValue("unparseable")
    m = TIME_RE.fullmatch(value)
    if m:
        number = _positive(m.group("number"))
        if number is None:
            return EstValue("unparseable")
        hours = number * HOURS_PER_UNIT[m.group("unit")]
        return EstValue("time", hours, hours, number, m.group("unit"))
    if PLACEHOLDER_RE.fullmatch(value):
        return EstValue("placeholder")
    m = NON_TIME_RE.fullmatch(value)
    if m and m.group("unit").lower() not in HOURS_PER_UNIT:
        number = _positive(m.group("number"))
        if number is not None:
            return EstValue("non_time", number=number, unit=m.group("unit"))
    return EstValue("unparseable")
