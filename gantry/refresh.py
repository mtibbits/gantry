"""The refresh job (#35): `gantry refresh --config FILE`.

One run, under a lock in `[refresh] dir`, runs the installation's update
commands, records the update in update.json, builds a fresh generation in a
child process and swaps it in whole by rewriting `current`.
docs/refresh-and-serve.md states the job; this module touches files only
through gantry/roots.py, under roots built with purpose "refresh".

The update record is update.json: `record_bytes` writes it and
`read_update_record` reads it back (#34 reads it for the daily page).

Each update command and the build run through `run_step`: no shell, stdin
from the null device, stdout and stderr into last-run.log, a time limit, and
on timeout (or an interrupt) the whole process tree stopped through the live
`Popen`, never a recorded PID: on POSIX the child leads a new session and its
group is killed; on Windows `taskkill /T /F` kills the tree, by full path
under SYSTEMROOT. A bare program name is looked up only in PATH's absolute
entries (gantry.roots.find_program), never in the current folder.
"""

import json
import os
import re
import signal
import subprocess
from dataclasses import dataclass
from datetime import datetime

from gantry.build import strict_json_loads
from gantry.config import error_reason
from gantry.roots import OutsideRoots, find_program, open_confined, read_bytes_confined

# Both lock branches stay in this file, so neither's allow-list entries in
# tests/test_confine_scan.py go stale on the other host (the scan is static).
if os.name == "nt":
    import msvcrt
else:
    import fcntl

RECORD_FILE = "update.json"
RECORD_VERSION = 1
REFRESH_KEY = "refresh.dir"

# A failed step's reason: an exit code (any integer: POSIX signal deaths are
# negative, Windows codes reach ten digits), a timeout, or a start failure.
STEP_REASON_RE = re.compile(r"exit -?[0-9]+|timed out|could not start")
SWAP_STATES = ("swapped", "swap failed", "not swapped")
READ_STATUSES = ("ok", "missing", "unreadable", "unknown_version")
# How long to wait for taskkill, and for a stopped step to exit, before going on.
KILL_WAIT_SECONDS = 10


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


# --- one step ----------------------------------------------------------------------------


@dataclass(frozen=True)
class StepResult:
    kind: str  # "exit", "timed out" or "could not start"
    code: int | None  # the exit code; None unless kind is "exit"


def log_line(log, text):
    """One line of Gantry's own into last-run.log, flushed before any child writes."""
    log.write((text + "\n").encode("utf-8", "backslashreplace"))
    log.flush()


def taskkill_argv(pid, environ):
    """taskkill's command that kills `pid` and its tree, by full path under
    SYSTEMROOT; None when the environment sets no SYSTEMROOT."""
    root = environ.get("SYSTEMROOT")
    if not root:
        return None
    return [os.path.join(root, "System32", "taskkill.exe"), "/T", "/F", "/PID", str(pid)]


def kill_tree(proc, log, environ=None):
    """Stop the live child `proc` and every process it started, then wait for
    it, at most KILL_WAIT_SECONDS. Problems go to the log; nothing raises."""
    if os.name == "nt":
        argv = taskkill_argv(proc.pid, os.environ if environ is None else environ)
        if argv is None:
            log_line(log, "gantry: taskkill not run: SYSTEMROOT is not set")
        else:
            try:
                killer = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
            except (OSError, ValueError) as e:
                log_line(log, f"gantry: taskkill could not start: {error_reason(e)}")
            else:
                try:
                    code = killer.wait(timeout=KILL_WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    killer.kill()
                    log_line(log, f"gantry: taskkill did not finish in {KILL_WAIT_SECONDS} s")
                else:
                    if code != 0:
                        log_line(log, f"gantry: taskkill exited {code}")
        proc.kill()  # in every case: the child itself, if taskkill missed it
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as e:
            log_line(log, f"gantry: could not stop the step's process group: {error_reason(e)}")
            proc.kill()
    try:
        proc.wait(timeout=KILL_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        log_line(log, f"gantry: the stopped step had not exited after {KILL_WAIT_SECONDS} s")


def run_step(argv, *, cwd, timeout, log, env=None) -> StepResult:
    """Run one command with no shell in `cwd`, its output into `log`, for at
    most `timeout` seconds. A program that cannot be found or started is
    "could not start"; one that runs too long is "timed out" with its tree
    stopped, whatever code it then reports. An interrupt (Ctrl+C) stops the
    tree too and is raised again: the child's own session keeps it out of
    the terminal's foreground group."""
    program = find_program(argv[0], os.environ if env is None else env)
    if program is None:
        return StepResult("could not start", None)
    try:
        proc = subprocess.Popen([program, *argv[1:]], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=(os.name != "nt"))
    except (OSError, ValueError):
        return StepResult("could not start", None)
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc, log, env)
        return StepResult("timed out", None)
    except BaseException:
        kill_tree(proc, log, env)
        raise
    return StepResult("exit", code)


# --- the lock ----------------------------------------------------------------------------

LOCK_FILE = "lock"
# What the locking call raises when another process holds the lock (U5):
# BlockingIOError on POSIX, PermissionError on Windows. Any other error, and
# any error opening the file, is the job's own failure, never "skipped".
LOCK_HELD = PermissionError if os.name == "nt" else BlockingIOError


def _lock(handle):
    """Lock the open lock file without waiting: byte 0 on Windows (msvcrt
    locks from the current position), the whole file on POSIX."""
    if os.name == "nt":
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def acquire_lock(roots, folder):
    """The open lock handle for `folder`/lock, or None when another run holds it.

    The file is opened for append under the writable `refresh.dir` root with
    links refused, and never written. The lock lives on the handle: it holds
    until the handle is closed or its process ends, and no child inherits it
    (children get no inherited descriptors). An error opening the file, or a
    locking error other than LOCK_HELD, is raised.
    """
    handle = open_confined(roots, folder / LOCK_FILE, "ab", key=REFRESH_KEY, follow_links=False)
    try:
        handle.seek(0)
        try:
            _lock(handle)
        except LOCK_HELD:
            handle.close()
            return None
    except BaseException:
        handle.close()
        raise
    return handle
