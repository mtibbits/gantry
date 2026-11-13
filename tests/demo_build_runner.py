"""The child side of tests/test_demo_build.py's clone runs (#45).

    python -P -B demo_build_runner.py GANTRY_ROOT DAY MODE

DAY is YYYY-MM-DD. MODE is one of:

- `main`: GANTRY_ROOT/tools/demo_build.py run as `__main__` (its CLI
  wrapper, no arguments) under `frozen_clock(DAY)`;
- `build-step`: `demo_build.STEPS[-1].run(GANTRY_ROOT)` under the clock,
  the control run (the history step needs the as-of date the control
  removes);
- `probe`: one JSON line of the clock's values inside the block, and
  whether the three names are restored after it.

The process exits with the call's code. It exits 3 with one
`demo_build_runner:` stderr line when `gantry` is already imported or would
not be imported from GANTRY_ROOT/gantry. A plain module, never collected:
stdlib only, and nothing of gantry is imported before the clock is frozen.
"""

import datetime
import json
import os
import runpy
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MODES = ("main", "build-step", "probe")


def _refuse(message):
    print(f"demo_build_runner: {message}", file=sys.stderr)
    sys.exit(3)


def _check_root(root):
    if "gantry" in sys.modules:
        _refuse("gantry is already imported")
    pkg = os.path.normcase(os.path.join(os.path.realpath(root), "gantry")) + os.sep
    import importlib.util

    spec = importlib.util.find_spec("gantry")
    origin = spec.origin if spec is not None else None
    if origin is None or not os.path.normcase(os.path.realpath(origin)).startswith(pkg):
        _refuse("gantry would not be imported from GANTRY_ROOT")


def _probe(real):
    real_date, real_datetime, real_time = real
    utc = datetime.timezone.utc
    minus5 = datetime.timezone(datetime.timedelta(hours=-5))
    date, dt = datetime.date, datetime.datetime
    today, now = date.today(), dt.now()
    return {
        "date.today": today.isoformat(),
        "datetime.now": now.isoformat(),
        "datetime.now(utc)": dt.now(utc).isoformat(),
        "datetime.now(-5)": dt.now(minus5).isoformat(),
        "datetime.today": dt.today().isoformat(),
        "datetime.utcnow": dt.utcnow().isoformat(),
        "time.time": time.time(),
        "isinstance": {
            "today is date": isinstance(today, date) and isinstance(today, real_date),
            "today is not datetime": not isinstance(today, dt) and not isinstance(today, real_datetime),
            "now is datetime": isinstance(now, dt) and isinstance(now, real_datetime),
            "now is date": isinstance(now, date) and isinstance(now, real_date),
            "made date is date": isinstance(date(2001, 2, 3), date),
            "made datetime is date": isinstance(dt(2001, 2, 3), date),
            "sum is datetime": isinstance(now + datetime.timedelta(days=1), dt),
            "difference is timedelta": isinstance(today - date(2001, 2, 3), datetime.timedelta),
        },
    }


def main(argv):
    if len(argv) != 3 or argv[2] not in MODES:
        _refuse("usage: GANTRY_ROOT DAY " + "|".join(MODES))
    root, day_text, mode = argv
    tools = os.path.join(os.path.realpath(root), "tools")
    sys.path[:0] = [tools, HERE]
    _check_root(root)
    real = (datetime.date, datetime.datetime, time.time)
    day = datetime.date.fromisoformat(day_text)
    from clock import frozen_clock

    if mode == "probe":
        with frozen_clock(day):
            values = _probe(real)
        values["restored"] = (datetime.date, datetime.datetime, time.time) == real
        print(json.dumps(values))
        return 0
    with frozen_clock(day):
        try:
            if mode == "main":
                script = os.path.join(tools, "demo_build.py")
                sys.argv = [script]
                runpy.run_path(script, run_name="__main__")
                return 0
            import demo_build

            return demo_build.STEPS[-1].run(demo_build.ROOT)
        except SystemExit as e:
            if e.code is None or isinstance(e.code, int):
                return e.code or 0
            print(e.code, file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
