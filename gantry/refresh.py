"""The refresh job (#35): `gantry refresh --config FILE`.

One run, under a lock in `[refresh] dir`, runs the installation's update
commands, records the update in update.json, builds a fresh generation in a
child process and swaps it in whole by rewriting `current`.
docs/refresh-and-serve.md states the job; this module touches files only
through gantry/roots.py, under roots built with purpose "refresh".

The update record is update.json: `record_bytes` writes it and
`read_update_record` reads it back (#34 reads it for the daily page).
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime

from gantry.build import strict_json_loads
from gantry.config import error_reason
from gantry.roots import OutsideRoots, read_bytes_confined

RECORD_FILE = "update.json"
RECORD_VERSION = 1
REFRESH_KEY = "refresh.dir"

# A failed step's reason: an exit code (any integer: POSIX signal deaths are
# negative, Windows codes reach ten digits), a timeout, or a start failure.
STEP_REASON_RE = re.compile(r"exit -?[0-9]+|timed out|could not start")
SWAP_STATES = ("swapped", "swap failed", "not swapped")
READ_STATUSES = ("ok", "missing", "unreadable", "unknown_version")


@dataclass(frozen=True)
class FailedStep:
    index: int  # from 1
    program: str  # the base name of the command's first word
    reason: str  # "exit N", "timed out" or "could not start"


@dataclass(frozen=True)
class LastBuild:
    at: str  # when the build child ended, ISO 8601 with an offset
    ok: bool  # exited 0 and was swapped in
    exit: int | None  # None when it timed out or could not start
    swap: str  # one of SWAP_STATES


@dataclass(frozen=True)
class UpdateRecord:
    record_version: int
    attempted_at: str
    ok: bool
    steps_run: int  # every command tried, the failing one included
    failed_step: FailedStep | None
    last_ok_at: str | None
    interval_minutes: int
    last_build: LastBuild | None


@dataclass(frozen=True)
class UpdateRead:
    status: str  # one of READ_STATUSES
    record: UpdateRecord | None
    reason: str | None  # one line, naming no path; None when ok


# --- the record ------------------------------------------------------------------------


def record_bytes(record: UpdateRecord) -> bytes:
    """update.json's bytes: the record's keys in order, indented, and LF."""
    step, build = record.failed_step, record.last_build
    value = {
        "record_version": record.record_version,
        "attempted_at": record.attempted_at,
        "ok": record.ok,
        "steps_run": record.steps_run,
        "failed_step": None if step is None else {
            "index": step.index, "program": step.program, "reason": step.reason},
        "last_ok_at": record.last_ok_at,
        "interval_minutes": record.interval_minutes,
        "last_build": None if build is None else {
            "at": build.at, "ok": build.ok, "exit": build.exit, "swap": build.swap},
    }
    return (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8")


class _Shape(ValueError):
    """A record that is valid JSON but not a well-formed version-1 record."""


def _is_int(value) -> bool:
    return type(value) is int


def _object(value, keys, what):
    if not isinstance(value, dict):
        raise _Shape(f"{what} is not an object")
    if set(value) != set(keys):
        raise _Shape(f"{what} does not have exactly the keys {', '.join(keys)}")
    return value


def _time(value, what):
    if not isinstance(value, str):
        raise _Shape(f"{what} is not a time")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise _Shape(f"{what} is not an ISO 8601 time") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _Shape(f"{what} has no UTC offset")
    return value


def _bool(value, what):
    if type(value) is not bool:
        raise _Shape(f"{what} is not true or false")
    return value


def _failed_step(value):
    if value is None:
        return None
    step = _object(value, ("index", "program", "reason"), "failed_step")
    if not _is_int(step["index"]) or step["index"] < 1:
        raise _Shape("failed_step.index is not an integer, 1 or more")
    if not isinstance(step["program"], str) or step["program"] == "":
        raise _Shape("failed_step.program is not a name")
    if not isinstance(step["reason"], str) or STEP_REASON_RE.fullmatch(step["reason"]) is None:
        raise _Shape("failed_step.reason is not exit N, timed out or could not start")
    return FailedStep(step["index"], step["program"], step["reason"])


def _last_build(value):
    if value is None:
        return None
    build = _object(value, ("at", "ok", "exit", "swap"), "last_build")
    if build["exit"] is not None and not _is_int(build["exit"]):
        raise _Shape("last_build.exit is not an integer or null")
    if build["swap"] not in SWAP_STATES:
        raise _Shape("last_build.swap is not swapped, swap failed or not swapped")
    return LastBuild(_time(build["at"], "last_build.at"), _bool(build["ok"], "last_build.ok"),
                     build["exit"], build["swap"])


def _record(value) -> UpdateRecord:
    keys = ("record_version", "attempted_at", "ok", "steps_run", "failed_step", "last_ok_at",
            "interval_minutes", "last_build")
    _object(value, keys, "the record")
    if not _is_int(value["steps_run"]) or value["steps_run"] < 0:
        raise _Shape("steps_run is not an integer, 0 or more")
    interval = value["interval_minutes"]
    if not _is_int(interval) or not 1 <= interval <= 1440:
        raise _Shape("interval_minutes is not an integer from 1 to 1440")
    last_ok_at = value["last_ok_at"]
    record = UpdateRecord(
        record_version=value["record_version"],
        attempted_at=_time(value["attempted_at"], "attempted_at"),
        ok=_bool(value["ok"], "ok"),
        steps_run=value["steps_run"],
        failed_step=_failed_step(value["failed_step"]),
        last_ok_at=None if last_ok_at is None else _time(last_ok_at, "last_ok_at"),
        interval_minutes=interval,
        last_build=_last_build(value["last_build"]),
    )
    if record.ok != (record.failed_step is None):
        raise _Shape("ok does not agree with failed_step")
    return record


def parse_update_record(data: bytes) -> UpdateRead:
    """Read update.json's bytes; never raises. The version is checked first,
    so a newer record reads as unknown_version, not as a bad shape."""
    try:
        value = strict_json_loads(data)
    except ValueError as e:
        return UpdateRead("unreadable", None, f"not valid JSON: {_one_line(str(e))}")
    if not isinstance(value, dict):
        return UpdateRead("unreadable", None, "not a JSON object")
    version = value.get("record_version")
    if not _is_int(version):
        return UpdateRead("unreadable", None, "record_version is missing or not an integer")
    if version != RECORD_VERSION:
        return UpdateRead("unknown_version", None,
                          f"record_version {version}; this Gantry reads record_version {RECORD_VERSION}")
    try:
        return UpdateRead("ok", _record(value), None)
    except _Shape as e:
        return UpdateRead("unreadable", None, str(e))


def read_update_record(roots, path) -> UpdateRead:
    """Read the update record at `path` under `roots` (key `refresh.dir`);
    never raises. No file is `missing`; one that cannot be read, or lies
    outside the roots, is `unreadable`, with a reason that names no path."""
    try:
        data = read_bytes_confined(roots, path, key=REFRESH_KEY)
    except FileNotFoundError:
        return UpdateRead("missing", None, f"no {RECORD_FILE} yet")
    except OutsideRoots as e:
        return UpdateRead("unreadable", None, str(e))
    except (OSError, ValueError) as e:
        # ValueError: an embedded NUL, or a path too long for Windows.
        return UpdateRead("unreadable", None, f"cannot read {RECORD_FILE}: {_one_line(error_reason(e))}")
    return parse_update_record(data)


def _one_line(text: str) -> str:
    return " ".join(text.split())
