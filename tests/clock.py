"""A frozen clock for a child process's demo build (#45).

A plain module, never collected; stdlib only. `frozen_clock(day)` replaces
`datetime.date`, `datetime.datetime` and `time.time` for the duration of the
`with` block, so a build run inside it reads `day` wherever it asks Python
for the date or the time:

- `date.today()` is `day`; `datetime.now()` and `datetime.today()` are naive
  noon of `day`, `datetime.utcnow()` too; `datetime.now(tz)` is noon UTC of
  `day` converted to `tz`; `time.time()` is noon UTC of `day`.
- The replacements are plain subclasses (`__slots__ = ()`), the frozen
  datetime a subclass of the frozen date, so constructors, arithmetic and
  `isinstance` behave as the real classes do for every instance made while
  the clock is frozen.

It must be entered before `gantry` and `tomllib` are imported: a module that
did `from datetime import date` earlier holds the real class. It refuses
(RuntimeError) when either is already in sys.modules.

Limits: C-level clock reads (`time.localtime()`, `time.time_ns()`,
`time.monotonic()`, file times) are not replaced, and an instance made
before entering (`date.min`, say) is not an instance of the frozen classes.
A control run, which removes the pinned as-of date and expects the injected
day on every page, is what shows the build is reached.
"""

import datetime
import sys
import time
from contextlib import contextmanager

GUARDED = ("gantry", "tomllib")


@contextmanager
def frozen_clock(day):
    if any(name in sys.modules for name in GUARDED):
        raise RuntimeError("frozen_clock: gantry or tomllib is already imported")
    real_date, real_datetime, real_time = datetime.date, datetime.datetime, time.time
    y, m, d = day.year, day.month, day.day
    stamp = real_datetime(y, m, d, 12, tzinfo=datetime.timezone.utc).timestamp()

    class FrozenDate(real_date):
        __slots__ = ()

        @classmethod
        def today(cls):
            return cls(y, m, d)

    class FrozenDatetime(real_datetime, FrozenDate):
        __slots__ = ()

        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls(y, m, d, 12)
            return cls.fromtimestamp(stamp, tz)

        @classmethod
        def today(cls):
            return cls(y, m, d, 12)

        @classmethod
        def utcnow(cls):
            return cls(y, m, d, 12)

    FrozenDate.__name__ = FrozenDate.__qualname__ = "date"
    FrozenDatetime.__name__ = FrozenDatetime.__qualname__ = "datetime"
    FrozenDate.__module__ = FrozenDatetime.__module__ = "datetime"

    datetime.date, datetime.datetime, time.time = FrozenDate, FrozenDatetime, lambda: stamp
    try:
        yield
    finally:
        datetime.date, datetime.datetime, time.time = real_date, real_datetime, real_time
