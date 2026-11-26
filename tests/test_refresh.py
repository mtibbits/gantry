"""The refresh job (#35): its update record, steps, lock, generations and one run.

docs/refresh-and-serve.md states the job. Every file a test makes lives under
tmp_path; the commit check refuses a tracked build.json or update.json.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from contextlib import contextmanager
from pathlib import PurePath

import pytest

from gantry import refresh
from gantry.refresh import (
    FailedStep,
    LastBuild,
    StepResult,
    UpdateRead,
    UpdateRecord,
    parse_update_record,
    record_bytes,
    run_step,
)
from gantry.roots import LinkRefused, OutsideRoots, directory_roots
import helpers_devagent as hd
from helpers.run_aggregator import command as aggregator_command
from helpers_config import copy_samples, literal, toml_commands, write_config, write_site_tree, written_bytes
from helpers_links import link_dir, symlink
from test_samples import ROOT, git_env

import sample_portfolio as SP  # noqa: E402

WINDOWS = os.name == "nt"


def script(folder, name, body):
    """A Python script `<name>.py` in folder, from dedented text."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.py"
    path.write_bytes(textwrap.dedent(body).encode("utf-8"))
    return path


def wait_for(predicate, seconds=30):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.05)

# --- Task 3: the update record and its reader ------------------------------------------

FULL = UpdateRecord(
    record_version=1,
    attempted_at="2026-11-19T12:45:03+00:00",
    ok=False,
    steps_run=2,
    failed_step=FailedStep(2, "git", "exit 1"),
    last_ok_at="2026-11-19T12:30:02+00:00",
    interval_minutes=15,
    last_build=LastBuild("2026-11-19T12:45:09+00:00", True, 0, "swapped"),
)
# Empty forms: a first run that succeeded before any build, and a first run that failed.
FIRST_OK = UpdateRecord(1, "2026-11-19T12:45:03+00:00", True, 0, None, "2026-11-19T12:45:03+00:00", 15, None)
FIRST_FAILED = UpdateRecord(1, "2026-11-19T12:45:03+00:00", False, 1, FailedStep(1, "git", "timed out"),
                            None, 1440, LastBuild("2026-11-19T12:45:09+00:00", False, None, "not swapped"))
RECORD_KEYS = ["record_version", "attempted_at", "ok", "steps_run", "failed_step", "last_ok_at",
               "interval_minutes", "last_build"]


def _signal_and_windows_codes():
    # POSIX signal deaths are negative; Windows codes reach ten digits (#35's improve pass).
    for code in (-9, 3221225786):
        yield UpdateRecord(1, FULL.attempted_at, False, 1, FailedStep(1, "git", f"exit {code}"), None, 15,
                           LastBuild(FULL.last_build.at, False, code, "swapped"))


@pytest.mark.parametrize("record", [FULL, FIRST_OK, FIRST_FAILED, *_signal_and_windows_codes()],
                         ids=["full", "first-ok", "first-failed", "exit-minus-9", "exit-3221225786"])
def test_record_round_trips(record):
    data = record_bytes(record)
    assert parse_update_record(data) == UpdateRead("ok", record, None)
    assert data.endswith(b"}\n") and not data.endswith(b"\n\n") and b"\r" not in data
    loaded = json.loads(data)
    assert list(loaded) == RECORD_KEYS
    if loaded["failed_step"] is not None:
        assert list(loaded["failed_step"]) == ["index", "program", "reason"]
    if loaded["last_build"] is not None:
        assert list(loaded["last_build"]) == ["at", "ok", "exit", "swap"]


def test_record_bytes_match_the_documented_shape():
    loaded = json.loads(record_bytes(FULL))
    assert loaded == {
        "record_version": 1,
        "attempted_at": "2026-11-19T12:45:03+00:00",
        "ok": False,
        "steps_run": 2,
        "failed_step": {"index": 2, "program": "git", "reason": "exit 1"},
        "last_ok_at": "2026-11-19T12:30:02+00:00",
        "interval_minutes": 15,
        "last_build": {"at": "2026-11-19T12:45:09+00:00", "ok": True, "exit": 0, "swap": "swapped"},
    }


def _one_line_without(read, text):
    assert read.reason and "\n" not in read.reason and "\r" not in read.reason, read
    assert text not in read.reason, read


def test_read_statuses(tmp_path):
    roots = directory_roots([tmp_path])
    path = tmp_path / "update.json"
    path.write_bytes(record_bytes(FULL))
    assert refresh.read_update_record(roots, path) == UpdateRead("ok", FULL, None)

    missing = refresh.read_update_record(roots, tmp_path / "absent.json")
    assert missing.status == "missing" and missing.record is None

    path.write_bytes(b"{")
    read = refresh.read_update_record(roots, path)
    assert read.status == "unreadable" and read.record is None
    _one_line_without(read, str(tmp_path))

    inner = tmp_path / "inner"
    inner.mkdir()
    read = refresh.read_update_record(directory_roots([inner]), path)
    assert read.status == "unreadable" and "refresh.dir" in read.reason
    _one_line_without(read, str(tmp_path))

    for data in (b'{"record_version": 2}', record_bytes(FULL).replace(b'"record_version": 1', b'"record_version": 2')):
        path.write_bytes(data)
        read = refresh.read_update_record(roots, path)
        assert read.status == "unknown_version" and read.record is None, data
        _one_line_without(read, str(tmp_path))


def _with(**changes):
    """FULL's JSON with keys changed (None-valued key: use NULL), dropped (DROP) or added."""
    loaded = json.loads(record_bytes(FULL))
    for key, value in changes.items():
        if value is DROP:
            del loaded[key]
        else:
            loaded[key] = None if value is NULL else value
    return json.dumps(loaded).encode("utf-8")


DROP, NULL = object(), object()
COMPACT = json.dumps(json.loads(record_bytes(FULL))).encode("utf-8")
SHAPES = {
    "null": b"null",
    "array": b"[]",
    "empty-object": b"{}",
    "version-true": _with(record_version=True),
    "version-string": _with(record_version="1"),
    "version-float": _with(record_version=1.0),
    "key-missing": _with(steps_run=DROP),
    "key-extra": _with(extra=1),
    "ok-string": _with(ok="false"),
    "steps-negative": _with(steps_run=-1),
    "steps-bool": _with(steps_run=True),
    "time-no-offset": _with(attempted_at="2026-11-19T12:45:03"),
    "time-not-a-time": _with(last_ok_at="yesterday"),
    "interval-zero": _with(interval_minutes=0),
    "failed-step-array": _with(failed_step=[]),
    "ok-beside-failed-step": _with(ok=True),
    "failed-without-step": _with(failed_step=NULL),
    "unknown-reason": _with(failed_step={"index": 2, "program": "git", "reason": "exploded"}),
    "reason-exit-text": _with(failed_step={"index": 2, "program": "git", "reason": "exit one"}),
    "step-index-zero": _with(failed_step={"index": 0, "program": "git", "reason": "exit 1"}),
    "step-program-empty": _with(failed_step={"index": 2, "program": "", "reason": "exit 1"}),
    "step-key-extra": _with(failed_step={"index": 2, "program": "git", "reason": "exit 1", "x": 1}),
    "build-swap-unknown": _with(last_build={"at": FULL.last_build.at, "ok": True, "exit": 0, "swap": "done"}),
    "build-exit-string": _with(last_build={"at": FULL.last_build.at, "ok": True, "exit": "0", "swap": "swapped"}),
    "build-exit-bool": _with(last_build={"at": FULL.last_build.at, "ok": True, "exit": False, "swap": "swapped"}),
    "build-key-missing": _with(last_build={"at": FULL.last_build.at, "ok": True, "exit": 0}),
    "duplicate-key": COMPACT.replace(b'"ok": false', b'"ok": false, "ok": false'),
    "nan": COMPACT.replace(b'"steps_run": 2', b'"steps_run": NaN'),
    "invalid-utf8": COMPACT.replace(b'"git"', b'"g\xffit"'),
    "huge-integer": COMPACT.replace(b'"steps_run": 2', b'"steps_run": ' + b"9" * 5000),
    "deep-nesting": b"[" * 100_000,
}


@pytest.mark.parametrize("case", sorted(SHAPES))
def test_reader_guards_shape_and_never_raises(tmp_path, case):
    data = SHAPES[case]
    assert data != record_bytes(FULL)
    read = parse_update_record(data)
    assert read.status == "unreadable" and read.record is None, read
    _one_line_without(read, "\n")
    path = tmp_path / "update.json"
    path.write_bytes(data)
    assert refresh.read_update_record(directory_roots([tmp_path]), path).status == "unreadable"


def test_reader_of_a_folder_is_unreadable(tmp_path):
    (tmp_path / "update.json").mkdir()
    read = refresh.read_update_record(directory_roots([tmp_path]), tmp_path / "update.json")
    assert read.status == "unreadable" and read.record is None
    _one_line_without(read, str(tmp_path))


# --- Task 4: one step, its timeout and the tree kill -----------------------------------

# A grandchild that appends a byte to a file every 50 ms for at most 60 s.
BEAT = """
    import sys, time
    with open(sys.argv[1], "ab", buffering=0) as handle:
        for _ in range(1200):
            handle.write(b".")
            time.sleep(0.05)
"""
# A step that starts BEAT as its own child, then sleeps.
PARENT = """
    import subprocess, sys, time
    subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])
    time.sleep(60)
"""


def heartbeat_step(tmp_path):
    """(argv, heartbeat file): a step whose grandchild beats until it is stopped."""
    beat = tmp_path / "beat"
    argv = [sys.executable, str(script(tmp_path, "parent", PARENT)), str(script(tmp_path, "beat", BEAT)), str(beat)]
    return argv, beat


def assert_heartbeat_stopped(beat):
    assert beat.exists() and beat.stat().st_size > 0
    size = beat.stat().st_size
    time.sleep(1)
    assert beat.stat().st_size == size


def test_step_exit_codes(tmp_path):
    talk = script(tmp_path, "talk", """
        import sys
        print("out-token-" + sys.argv[1], flush=True)
        print("err-token-" + sys.argv[1], file=sys.stderr, flush=True)
        sys.exit(int(sys.argv[1]))
    """)
    with open(tmp_path / "log", "wb") as log:
        assert run_step([sys.executable, str(talk), "0"], cwd=tmp_path, timeout=120, log=log) == StepResult("exit", 0)
        assert run_step([sys.executable, str(talk), "3"], cwd=tmp_path, timeout=120, log=log) == StepResult("exit", 3)
    text = (tmp_path / "log").read_bytes()
    for token in (b"out-token-0", b"err-token-0", b"out-token-3", b"err-token-3"):
        assert token in text


def test_missing_program_could_not_start(tmp_path):
    with open(tmp_path / "log", "wb") as log:
        result = run_step(["gantry-35-no-such-program"], cwd=tmp_path, timeout=120, log=log)
        assert result == StepResult("could not start", None)
        result = run_step([str(tmp_path / "absent-program")], cwd=tmp_path, timeout=120, log=log)
        assert result == StepResult("could not start", None)
    lines = (tmp_path / "log").read_bytes().decode("utf-8").splitlines()
    assert len(lines) == 2 and all(line.startswith("gantry: could not start: ") for line in lines)
    assert lines[0] == "gantry: could not start: the program is in no absolute PATH entry"


def test_timeout_stops_the_whole_tree(tmp_path):
    argv, beat = heartbeat_step(tmp_path)
    with open(tmp_path / "log", "wb") as log:
        assert run_step(argv, cwd=tmp_path, timeout=2, log=log) == StepResult("timed out", None)
    assert_heartbeat_stopped(beat)


def test_taskkill_argv():
    assert refresh.taskkill_argv(42, {"SYSTEMROOT": "R"}) == [
        os.path.join("R", "System32", "taskkill.exe"), "/T", "/F", "/PID", "42"]
    assert refresh.taskkill_argv(42, {}) is None


@pytest.mark.skipif(WINDOWS, reason="a POSIX process group; Windows has no foreground group to leave")
def test_interrupt_kills_the_tree(tmp_path, monkeypatch):
    argv, beat = heartbeat_step(tmp_path)
    real_wait, calls = subprocess.Popen.wait, []

    def wait(self, timeout=None):
        if not calls:
            calls.append(timeout)
            wait_for(lambda: beat.exists() and beat.stat().st_size > 0)
            raise KeyboardInterrupt
        return real_wait(self, timeout=timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", wait)
    with open(tmp_path / "log", "wb") as log, pytest.raises(KeyboardInterrupt):
        run_step(argv, cwd=tmp_path, timeout=120, log=log)
    assert calls == [120]
    assert_heartbeat_stopped(beat)


def plant_probe(folder):
    """A program named `probe` in folder that runs this Python with its arguments:
    a shell script on POSIX, a copy of the base interpreter named probe.exe on
    Windows (which CreateProcess would find in a current folder)."""
    folder.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        shutil.copyfile(getattr(sys, "_base_executable", sys.executable), folder / "probe.exe")
        return
    assert "'" not in sys.executable
    probe = folder / "probe"
    probe.write_bytes(("#!/bin/sh\nexec '" + sys.executable + "' \"$@\"\n").encode("utf-8"))
    probe.chmod(0o755)


def probe_env(*path_entries):
    """os.environ with PATH set to path_entries. On Windows the copied
    interpreter finds its standard library by PYTHONHOME and its DLL by the
    base interpreter's folder, an absolute PATH entry holding no probe."""
    env = {**os.environ, "PATH": os.pathsep.join(path_entries)}
    if WINDOWS:
        base = os.path.dirname(getattr(sys, "_base_executable", sys.executable))
        env["PATH"] = os.pathsep.join([*path_entries, base])
        env["PYTHONHOME"] = sys.base_prefix
    return env


def test_bare_program_resolves_on_path_only(tmp_path, monkeypatch):
    writer = script(tmp_path / "scripts", "writer", """
        import sys
        open(sys.argv[1], "wb").close()
    """)

    def run(cwd, env, sentinel):
        with open(tmp_path / "log", "ab") as log:
            return run_step(["probe", str(writer), str(sentinel)], cwd=cwd, timeout=120, log=log, env=env)

    bin_dir, work = tmp_path / "bin", tmp_path / "work"
    plant_probe(bin_dir)
    work.mkdir()
    assert run(work, probe_env(str(bin_dir)), tmp_path / "on-path") == StepResult("exit", 0)
    assert (tmp_path / "on-path").exists()

    here = tmp_path / "here"
    plant_probe(here)
    monkeypatch.chdir(here)
    assert run(work, probe_env(), tmp_path / "parent-cwd") == StepResult("could not start", None)
    assert run(here, probe_env(), tmp_path / "step-cwd") == StepResult("could not start", None)
    monkeypatch.chdir(tmp_path)
    assert run(work, probe_env("bin"), tmp_path / "relative") == StepResult("could not start", None)
    for name in ("parent-cwd", "step-cwd", "relative"):
        assert not (tmp_path / name).exists(), name


# --- Task 5: the lock -----------------------------------------------------------------

# Takes the refresh lock in another process, says so, and keeps it until killed.
HOLDER = """
    import sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from gantry.refresh import acquire_lock
    from gantry.roots import directory_roots
    folder = Path(sys.argv[2])
    handle = acquire_lock(directory_roots([], writable=[folder]), folder)
    print("held" if handle is not None else "not held", flush=True)
    time.sleep(120)
"""


@contextmanager
def holders(tmp_path, name, body):
    """hold(*args) -> the Popen of the `body` script run with args, once it
    prints "held"; every one is killed on exit."""
    procs = []

    def hold(*args):
        holder = script(tmp_path / "helpers", name, body)
        proc = subprocess.Popen([sys.executable, str(holder), *map(str, args)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
        procs.append(proc)
        assert proc.stdout.readline().strip() == b"held"
        return proc

    try:
        yield hold
    finally:
        for proc in procs:
            proc.kill()
            proc.wait()
            proc.stdout.close()


@pytest.fixture
def lock_holder(tmp_path):
    """hold(folder) -> the Popen of a process holding folder's refresh lock; every one is killed at teardown."""
    with holders(tmp_path, "holder", HOLDER) as hold:
        yield lambda folder: hold(ROOT, folder)


def writable(folder):
    return directory_roots([], writable=[folder])


def test_lock_excludes_a_second_taker(tmp_path):
    roots = writable(tmp_path)
    first = refresh.acquire_lock(roots, tmp_path)
    assert first is not None
    try:
        assert refresh.acquire_lock(roots, tmp_path) is None
    finally:
        first.close()
    again = refresh.acquire_lock(roots, tmp_path)
    assert again is not None
    again.close()


def acquire_after_death(roots, folder):
    """The lock, retried: Windows frees a dead holder's lock soon after, not at once."""
    handles = []
    wait_for(lambda: handles.append(refresh.acquire_lock(roots, folder)) or handles[-1] is not None, seconds=10)
    return handles[-1]


def test_lock_dies_with_its_holder(tmp_path, lock_holder):
    proc = lock_holder(tmp_path)
    assert refresh.acquire_lock(writable(tmp_path), tmp_path) is None
    proc.kill()
    proc.wait()
    acquire_after_death(writable(tmp_path), tmp_path).close()


def test_lock_is_taken_from_byte_0_and_stays_empty(tmp_path, monkeypatch):
    # msvcrt.locking locks from the current position, and "ab" opens at the
    # end: a lock file an older run left non-empty must still be locked at 0.
    (tmp_path / "lock").write_bytes(b"older")
    positions, real = [], refresh._lock

    def spy(handle):
        positions.append(handle.tell())
        return real(handle)

    monkeypatch.setattr(refresh, "_lock", spy)
    handle = refresh.acquire_lock(writable(tmp_path), tmp_path)
    handle.close()
    assert positions == [0]
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    handle = refresh.acquire_lock(writable(fresh), fresh)
    handle.close()
    assert (fresh / "lock").read_bytes() == b""


def test_unopenable_lock_raises_not_none(tmp_path, monkeypatch):
    def refuse(roots, path, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(refresh, "open_confined", refuse)
    with pytest.raises(PermissionError):
        refresh.acquire_lock(writable(tmp_path), tmp_path)


def test_link_at_lock_is_refused(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"keep")
    symlink(target, tmp_path / "lock")
    with pytest.raises(OutsideRoots) as e:
        refresh.acquire_lock(writable(tmp_path), tmp_path)
    assert isinstance(e.value, LinkRefused)
    assert target.read_bytes() == b"keep"


# --- Task 6: generations: the build child, acceptance, the swap and the sweep ----------

ID_SHAPE = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")


def test_generation_ids():
    from datetime import datetime, timezone

    now = datetime(2026, 11, 19, 12, 45, 3, tzinfo=timezone.utc)
    ids = {refresh.new_generation_id(now) for _ in range(1000)}
    assert len(ids) == 1000
    assert all(ID_SHAPE.fullmatch(i) and i.startswith("20261119T124503Z-") for i in ids)
    assert all(refresh.GENERATION_ID_RE.fullmatch(i) for i in ids)


def test_build_argv_and_env(tmp_path):
    from gantry.config import load_config
    from gantry.roots import gantry_root

    config = load_config(write_config(tmp_path / "cfg"))
    generation = tmp_path / "refresh" / "generations" / "g1"
    assert refresh.build_argv(config, None, generation) == [
        sys.executable, "-P", "-m", "gantry", "build", "--config", str(config.path), "--output-dir", str(generation)]
    stand_in = [sys.executable, "stand-in.py", "ok"]
    assert refresh.build_argv(config, stand_in, generation) == [*stand_in, "--output-dir", str(generation)]
    assert stand_in == [sys.executable, "stand-in.py", "ok"]
    inherited = {"PATH": "p", "PYTHONPATH": "elsewhere"}
    env = refresh.build_env(inherited)
    assert env == {"PATH": "p", "PYTHONPATH": str(gantry_root())}
    assert inherited["PYTHONPATH"] == "elsewhere"
    assert refresh.build_env({})["PYTHONPATH"] == str(gantry_root())


def make_generation(folder, name, marker=None):
    generation = folder / "generations" / name
    generation.mkdir(parents=True)
    if marker is not None:
        (generation / "build.json").write_bytes(marker)
    return generation


def test_accepted_table(tmp_path):
    from gantry.build import marker_bytes

    roots = writable(tmp_path)
    cases = [
        (marker_bytes(0), StepResult("exit", 0), True),
        (marker_bytes(1), StepResult("exit", 1), True),
        (None, StepResult("exit", 0), False),
        (marker_bytes(0), StepResult("exit", 1), False),
        (marker_bytes(1), StepResult("exit", 0), False),
        (marker_bytes(2), StepResult("exit", 2), False),
        (b"garbage", StepResult("exit", 0), False),
        (marker_bytes(0), StepResult("timed out", None), False),
        (marker_bytes(0), StepResult("could not start", None), False),
    ]
    for index, (marker, result, expected) in enumerate(cases):
        generation = make_generation(tmp_path, f"g{index}", marker)
        assert refresh.accepted(roots, generation, result) is expected, (marker, result)


def test_swap_writes_only_the_id(tmp_path):
    assert refresh.swap(writable(tmp_path), tmp_path, "20261119T124503Z-0a1b2c3d") is True
    assert (tmp_path / "current").read_bytes() == b"20261119T124503Z-0a1b2c3d"
    assert sorted(os.listdir(tmp_path)) == ["current"]


def test_swap_retries_then_succeeds(tmp_path, monkeypatch):
    real, calls = refresh.write_confined, []

    def flaky(*args, **kwargs):
        calls.append(args[1])
        if len(calls) <= 3:
            raise PermissionError(13, "held open")
        return real(*args, **kwargs)

    monkeypatch.setattr(refresh, "write_confined", flaky)
    assert refresh.swap(writable(tmp_path), tmp_path, "g-1") is True
    assert len(calls) == 4 and (tmp_path / "current").read_bytes() == b"g-1"


def test_swap_gives_up_after_the_window(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError(13, "held open")

    monkeypatch.setattr(refresh, "write_confined", refuse)
    monkeypatch.setattr(refresh, "SWAP_RETRY_SECONDS", 0.2)
    started = time.monotonic()
    assert refresh.swap(writable(tmp_path), tmp_path, "g-1") is False
    assert 0.2 <= time.monotonic() - started < 5
    assert not (tmp_path / "current").exists()


def test_read_current_tri_state(tmp_path):
    roots = writable(tmp_path)
    assert refresh.read_current(roots, tmp_path) is None
    (tmp_path / "current").write_bytes(b"20261119T124503Z-0a1b2c3d")
    assert refresh.read_current(roots, tmp_path) == "20261119T124503Z-0a1b2c3d"
    for data in (b"abc\n", b"../x", b"", b"\xff", b"a b", "é".encode("utf-8")):
        (tmp_path / "current").write_bytes(data)
        assert refresh.read_current(roots, tmp_path) is refresh.UNDETERMINED, data
    (tmp_path / "current").unlink()
    (tmp_path / "current").mkdir()
    assert refresh.read_current(roots, tmp_path) is refresh.UNDETERMINED


def test_sweep_keeps_live_and_previous(tmp_path):
    for name in ("a", "b", "c", "not_an_id"):
        make_generation(tmp_path, name)
    (tmp_path / "generations" / "stray").write_bytes(b"x")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_bytes(b"keep")
    link_dir(elsewhere, tmp_path / "generations" / "linked")
    refresh.sweep(writable(tmp_path), tmp_path, keep={"c", "b"})
    assert sorted(os.listdir(tmp_path / "generations")) == ["b", "c", "linked", "not_an_id", "stray"]
    assert (elsewhere / "keep.txt").read_bytes() == b"keep"


def test_sweep_leaves_what_it_cannot_delete(tmp_path, monkeypatch):
    for name in ("a", "b", "c"):
        make_generation(tmp_path, name)
    real = refresh.rmtree_confined

    def refuse_a(roots, path, **kwargs):
        if os.path.basename(path) == "a":
            raise PermissionError(13, "in use")
        return real(roots, path, **kwargs)

    monkeypatch.setattr(refresh, "rmtree_confined", refuse_a)
    refresh.sweep(writable(tmp_path), tmp_path, keep={"c"})
    assert sorted(os.listdir(tmp_path / "generations")) == ["a", "c"]


def test_discard_removes_one_and_ignores_errors(tmp_path):
    generation = make_generation(tmp_path, "g1", b"x")
    refresh.discard(writable(tmp_path), generation)
    assert not generation.exists()
    refresh.discard(writable(tmp_path), generation)  # already gone: no error


# --- Task 7: refresh(), one run ----------------------------------------------------------

# The build's stand-in: `<mode> --output-dir <generation>`, run in [refresh] dir.
STAND_IN = """
    import os, shutil, sys, time
    mode, out = sys.argv[1], sys.argv[3]
    if mode == "sleep":
        time.sleep(60)
    os.makedirs(out, exist_ok=True)
    if os.path.exists("update.json"):
        shutil.copyfile("update.json", os.path.join(out, "update.json"))
    with open(os.path.join(out, "index.html"), "wb") as handle:
        handle.write(("index " + mode).encode("ascii"))
    print("build-token-" + mode, flush=True)
    if mode == "raise-after-index":
        raise RuntimeError("planted")
    markers = {"ok": b'{"build_version": 1, "exit": 0}\\n', "mismatch": b'{"build_version": 1, "exit": 0}\\n',
               "exit1": b'{"build_version": 1, "exit": 1}\\n', "exit2": b'{"build_version": 1, "exit": 2}\\n',
               "garbage": b"garbage"}
    if mode in markers:
        with open(os.path.join(out, "build.json"), "wb") as handle:
            handle.write(markers[mode])
    sys.exit({"ok": 0, "no-marker": 0, "mismatch": 1, "exit1": 1, "exit2": 2, "garbage": 0}[mode])
"""
# An update command: append its argument to a relative file, then exit with the second (default 0).
APPEND = """
    import sys
    with open("order.txt", "ab") as handle:
        handle.write(sys.argv[1].encode("ascii"))
    print("update-token-" + sys.argv[1], flush=True)
    sys.exit(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
"""


@pytest.fixture
def stand_in(tmp_path):
    """stand_in(mode) -> the stand-in build command for that mode."""
    path = script(tmp_path / "helpers", "stand_in", STAND_IN)
    return lambda mode: [sys.executable, str(path), mode]


@pytest.fixture
def append(tmp_path):
    """append(n, code=0) -> an update command appending n to the config folder's order.txt."""
    path = script(tmp_path / "helpers", "append", APPEND)
    return lambda n, code=0: [sys.executable, str(path), str(n), str(code)]


def installation(tmp_path, update=(), refresh_dir='"refresh"'):
    """A loaded config with a [refresh] table whose dir does not exist yet."""
    from gantry.config import load_config

    tail = f"\n[refresh]\ndir = {refresh_dir}\nupdate = {toml_commands(update)}\n"
    return load_config(write_config(tmp_path / "inst", tail=tail))


def rdir(config):
    """The config's [refresh] dir as a concrete Path."""
    from pathlib import Path

    return Path(config.refresh.dir)


def run(config, stand_in, mode="ok", **kwargs):
    kwargs.setdefault("step_timeout_seconds", 120)
    return refresh.refresh(config, build_command=stand_in(mode), **kwargs)


def record(config):
    read = parse_update_record((rdir(config) / "update.json").read_bytes())
    assert read.status == "ok", read
    return read.record


def live(config):
    """(the live generation's id, its folder)."""
    current = (rdir(config) / "current").read_bytes().decode("ascii")
    return current, rdir(config) / "generations" / current


def generations(config):
    return sorted(os.listdir(rdir(config) / "generations"))


def aware(text):
    from datetime import datetime

    parsed = datetime.fromisoformat(text)
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


SEEDED_OK_AT = "2026-01-02T03:04:05+00:00"
SEEDED_BUILD = LastBuild("2026-01-02T03:05:00+00:00", True, 0, "swapped")


def seed(config, data=None):
    """An earlier record with a fixed old last_ok_at (or `data` as given)."""
    folder = rdir(config)
    folder.mkdir(parents=True, exist_ok=True)
    if data is None:
        data = record_bytes(UpdateRecord(1, SEEDED_OK_AT, True, 0, None, SEEDED_OK_AT, 15, SEEDED_BUILD))
    (folder / "update.json").write_bytes(data)


def test_three_commands_run_in_order(tmp_path, stand_in, append):
    config = installation(tmp_path, [append(1), append(2), append(3)])
    assert run(config, stand_in) == 0
    assert (tmp_path / "inst" / "order.txt").read_bytes() == b"123"
    r = record(config)
    assert (r.ok, r.steps_run, r.failed_step, r.interval_minutes) == (True, 3, None, 15)
    assert r.last_ok_at == r.attempted_at and aware(r.attempted_at)
    assert (r.last_build.ok, r.last_build.exit, r.last_build.swap) == (True, 0, "swapped")
    assert aware(r.last_build.at)
    _, generation = live(config)
    assert (generation / "index.html").read_bytes() == b"index ok"


def test_second_command_failing_stops_the_update(tmp_path, stand_in, append, capsys):
    config = installation(tmp_path, [append(1), append(2, 1), append(3)])
    seed(config)
    assert run(config, stand_in) == 1
    assert (tmp_path / "inst" / "order.txt").read_bytes() == b"12"
    r = record(config)
    program = os.path.basename(sys.executable)
    assert r.failed_step == FailedStep(2, program, "exit 1") and r.steps_run == 2 and r.ok is False
    assert r.last_ok_at == SEEDED_OK_AT
    assert (r.last_build.ok, r.last_build.exit, r.last_build.swap) == (True, 0, "swapped") and live(config)[1].is_dir()
    line = f"gantry: error: update step 2 ({program}): exit 1\n"
    assert line in capsys.readouterr().err
    assert line in (rdir(config) / "last-run.log").read_bytes().decode("utf-8")


def test_config_limits_reach_every_step_and_the_record(tmp_path, stand_in, append, monkeypatch):
    from gantry.config import load_config
    from gantry.roots import gantry_root

    tail = (f"\n[refresh]\ndir = \"refresh\"\nupdate = {toml_commands([append(1), append(2)])}\n"
            "interval_minutes = 7\nstep_timeout_minutes = 3\n")
    config = load_config(write_config(tmp_path / "inst", tail=tail))
    calls, real = [], refresh.run_step

    def spy(argv, **kwargs):
        calls.append(kwargs)
        return real(argv, **kwargs)

    monkeypatch.setattr(refresh, "run_step", spy)
    assert refresh.refresh(config, build_command=stand_in("ok")) == 0
    assert [call["timeout"] for call in calls] == [180, 180, 180]
    assert [call.get("env") is None for call in calls] == [True, True, False]
    assert calls[-1]["env"]["PYTHONPATH"] == str(gantry_root())
    assert record(config).interval_minutes == 7


def test_timed_out_command_is_recorded_and_its_tree_stopped(tmp_path, stand_in):
    argv, beat = heartbeat_step(tmp_path)
    config = installation(tmp_path, [argv])
    assert run(config, stand_in, step_timeout_seconds=2) == 1
    assert record(config).failed_step.reason == "timed out"
    assert_heartbeat_stopped(beat)


def test_missing_program_is_could_not_start(tmp_path, stand_in):
    config = installation(tmp_path, [["gantry-35-no-such-program", "pull"]])
    assert run(config, stand_in) == 1
    r = record(config)
    assert r.failed_step == FailedStep(1, "gantry-35-no-such-program", "could not start") and r.steps_run == 1
    assert r.last_ok_at is None and r.last_build.swap == "swapped"


def test_empty_update_is_a_success_of_zero_steps(tmp_path, stand_in):
    config = installation(tmp_path)
    assert run(config, stand_in) == 0
    r = record(config)
    assert (r.ok, r.steps_run, r.failed_step) == (True, 0, None) and r.last_ok_at == r.attempted_at


def test_missing_refresh_dir_is_created(tmp_path, stand_in):
    config = installation(tmp_path)
    assert not rdir(config).exists()
    assert run(config, stand_in) == 0
    assert sorted(os.listdir(rdir(config))) == ["current", "generations", "last-run.log", "lock", "update.json"]
    assert (rdir(config) / "lock").read_bytes() == b""


def test_record_holds_no_path_or_output_and_the_log_holds_both(tmp_path, stand_in, append):
    from commit_check import path_form_hits

    config = installation(tmp_path, [append(7)])
    assert run(config, stand_in) == 0
    log = (rdir(config) / "last-run.log").read_bytes()
    assert b"update-token-7" in log and b"build-token-ok" in log
    data = record_bytes(record(config)).decode("utf-8")
    assert (rdir(config) / "update.json").read_bytes().decode("utf-8") == data
    assert "token" not in data and str(tmp_path) not in data and path_form_hits(data) == []
    quiet = installation(tmp_path)  # the same folder, no update command
    assert run(quiet, stand_in) == 0
    assert b"update-token-7" not in (rdir(config) / "last-run.log").read_bytes()


def test_the_build_sees_this_runs_failed_update(tmp_path, stand_in, append, monkeypatch):
    config = installation(tmp_path, [append(1, 1)])
    seed(config)
    targets, real = [], refresh.write_confined

    def spy(roots, path, data, **kwargs):
        targets.append(os.path.basename(path))
        return real(roots, path, data, **kwargs)

    monkeypatch.setattr(refresh, "write_confined", spy)
    assert run(config, stand_in) == 1
    assert targets == ["update.json", "current", "update.json"]
    _, generation = live(config)
    seen = parse_update_record((generation / "update.json").read_bytes()).record
    final = record(config)
    assert seen.attempted_at == final.attempted_at and seen.ok is False and seen.last_build == SEEDED_BUILD
    assert final.last_build != SEEDED_BUILD and final.last_build.swap == "swapped"


FAILING = {
    # mode: (step limit, expected last_build.exit, the build-failed reason)
    "no-marker": (120, 0, "no matching completion marker (exit 0)"),
    "mismatch": (120, 1, "no matching completion marker (exit 1)"),
    "exit2": (120, 2, "exit 2"),
    "garbage": (120, 0, "no matching completion marker (exit 0)"),
    "raise-after-index": (120, 1, "no matching completion marker (exit 1)"),
    "sleep": (2, None, "timed out"),
    "missing-program": (120, None, "could not start"),
}


@pytest.mark.parametrize("mode", sorted(FAILING))
def test_forced_build_failures_leave_the_live_generation(tmp_path, stand_in, capsys, mode):
    config = installation(tmp_path)
    assert run(config, stand_in) == 0
    current, generation = live(config)
    before = written_bytes(generation)
    capsys.readouterr()
    limit, code, why = FAILING[mode]
    if mode == "missing-program":
        result = refresh.refresh(config, step_timeout_seconds=limit, build_command=["gantry-35-no-such-build"])
    else:
        result = run(config, stand_in, mode, step_timeout_seconds=limit)
    assert result == 1
    assert live(config) == (current, generation) and written_bytes(generation) == before
    assert generations(config) == [current]
    last = record(config).last_build
    assert (last.ok, last.exit, last.swap) == (False, code, "not swapped")
    assert f"gantry: error: build failed: {why}\n" in capsys.readouterr().err
    assert f"gantry: error: build failed: {why}\n" in (rdir(config) / "last-run.log").read_bytes().decode("utf-8")


def test_build_exit_1_with_its_marker_is_swapped_in(tmp_path, stand_in, capsys):
    config = installation(tmp_path)
    assert run(config, stand_in, "exit1") == 1
    last = record(config).last_build
    assert (last.ok, last.exit, last.swap) == (False, 1, "swapped")
    assert "gantry: error: build exited 1; its generation was swapped in\n" in capsys.readouterr().err


def test_three_refreshes_keep_two_generations(tmp_path, stand_in):
    config = installation(tmp_path)
    ids = []
    for _ in range(3):
        assert run(config, stand_in) == 0
        ids.append(live(config)[0])
    assert generations(config) == sorted(ids[1:])
    other = installation(tmp_path / "middle")
    assert run(other, stand_in) == 0
    first = live(other)[0]
    assert run(other, stand_in, "exit2") == 1
    assert run(other, stand_in) == 0
    assert generations(other) == sorted([first, live(other)[0]])


def test_failed_swap_is_recorded(tmp_path, stand_in, capsys, monkeypatch):
    config = installation(tmp_path)
    assert run(config, stand_in) == 0
    current, _ = live(config)
    real = refresh.write_confined

    def refuse_current(roots, path, data, **kwargs):
        if os.path.basename(path) == "current":
            raise PermissionError(13, "held open")
        return real(roots, path, data, **kwargs)

    monkeypatch.setattr(refresh, "write_confined", refuse_current)
    monkeypatch.setattr(refresh, "SWAP_RETRY_SECONDS", 0.2)
    capsys.readouterr()
    assert run(config, stand_in) == 1
    assert live(config)[0] == current and generations(config) == [current]
    last = record(config).last_build
    assert (last.ok, last.swap) == (False, "swap failed")
    assert "gantry: error: swap failed: current could not be replaced in 0.2 s\n" in capsys.readouterr().err


def test_undeletable_generation_waits_for_the_next_run(tmp_path, stand_in, monkeypatch):
    config = installation(tmp_path)
    assert run(config, stand_in) == 0
    oldest = live(config)[0]
    assert run(config, stand_in) == 0
    real = refresh.rmtree_confined

    def refuse_oldest(roots, path, **kwargs):
        if os.path.basename(path) == oldest:
            raise PermissionError(13, "in use")
        return real(roots, path, **kwargs)

    monkeypatch.setattr(refresh, "rmtree_confined", refuse_oldest)
    assert run(config, stand_in) == 0
    assert len(generations(config)) == 3 and oldest in generations(config)
    monkeypatch.setattr(refresh, "rmtree_confined", real)
    assert run(config, stand_in) == 0
    assert len(generations(config)) == 2 and oldest not in generations(config)


def test_undetermined_current_skips_the_sweep(tmp_path, stand_in):
    config = installation(tmp_path)
    assert run(config, stand_in) == 0
    assert run(config, stand_in) == 0
    (rdir(config) / "current").write_bytes(b"../x")
    assert run(config, stand_in) == 0
    _, generation = live(config)
    assert generation.is_dir() and len(generations(config)) == 3


def snapshot(folder):
    """Every entry under folder with its bytes; `lock` by its size only, since
    on Windows reading a byte another process holds locked fails."""
    return {p.relative_to(folder).as_posix(): (p.stat().st_size if p.name == "lock" else
                                               p.read_bytes() if p.is_file() else None)
            for p in sorted(folder.rglob("*"))}


def test_a_running_refresh_is_skipped(tmp_path, stand_in, lock_holder, capsys):
    config = installation(tmp_path)
    assert run(config, stand_in) == 0
    before = snapshot(rdir(config))
    proc = lock_holder(rdir(config))
    capsys.readouterr()
    assert run(config, stand_in) == 0
    out, err = capsys.readouterr()
    assert out == "skipped: a refresh is still running\n" and err == ""
    assert snapshot(rdir(config)) == before
    proc.kill()
    proc.wait()
    acquire_after_death(writable(rdir(config)), rdir(config)).close()
    assert run(config, stand_in) == 0
    assert live(config)[0] != before["current"].decode("ascii")


NO_DIR = ("gantry: error: {config}: [refresh] dir is not set; add a [refresh] table with dir = \"<path>\" "
          "to run gantry refresh (docs/refresh-and-serve.md)\n")


def test_no_refresh_dir_is_a_config_error(tmp_path, stand_in, capsys):
    from gantry.config import load_config

    config = load_config(write_config(tmp_path / "inst"))
    assert run(config, stand_in) == 1
    assert capsys.readouterr().err == NO_DIR.format(config=config.path)
    assert not (tmp_path / "inst" / "out").exists()


def test_refresh_dir_holding_the_config_is_a_config_error(tmp_path, stand_in, capsys):
    config = installation(tmp_path, refresh_dir='"."')
    assert run(config, stand_in) == 1
    err = capsys.readouterr().err
    assert err.startswith("gantry: error: ") and "key 'refresh.dir' contains the config file itself" in err
    assert not (tmp_path / "inst" / "generations").exists()


@pytest.mark.parametrize("data", [b"{", b'{"record_version": 2}'], ids=["unreadable", "unknown_version"])
def test_unreadable_previous_record_does_not_stop_the_run(tmp_path, stand_in, capsys, data):
    config = installation(tmp_path)
    seed(config, data)
    assert run(config, stand_in) == 0
    status = "unreadable" if data == b"{" else "unknown_version"
    warning = f"gantry: warning: previous update.json is {status}: "
    assert warning in capsys.readouterr().err
    log = (rdir(config) / "last-run.log").read_bytes().decode("utf-8")
    assert warning in log and "last_ok_at starts again" in log
    r = record(config)
    assert r.last_ok_at == r.attempted_at and r.last_build.swap == "swapped"


def _raise_for(monkeypatch, name, leaf, error):
    real = getattr(refresh, name)

    def shim(roots, path, *args, **kwargs):
        if os.path.basename(path) == leaf:
            raise error
        return real(roots, path, *args, **kwargs)

    monkeypatch.setattr(refresh, name, shim)


JOB_FILE_FAILURES = {
    "refresh-folder": ("mkdir_confined", "refresh"),
    "generations-folder": ("mkdir_confined", "generations"),
    "lock": ("open_confined", "lock"),
    "last-run-log": ("open_confined", "last-run.log"),
}


@pytest.mark.parametrize("case", sorted(JOB_FILE_FAILURES))
def test_job_file_failures_return_1(tmp_path, stand_in, capsys, monkeypatch, case):
    config = installation(tmp_path)
    name, leaf = JOB_FILE_FAILURES[case]
    _raise_for(monkeypatch, name, leaf, PermissionError(13, "Permission denied"))
    assert run(config, stand_in) == 1
    out, err = capsys.readouterr()
    assert err.startswith("gantry: error: ") and "Permission denied" in err and "skipped" not in out
    assert not (rdir(config) / "update.json").exists()


def test_link_at_lock_is_refused_by_the_run(tmp_path, stand_in, capsys):
    config = installation(tmp_path)
    rdir(config).mkdir()
    target = tmp_path / "target"
    target.write_bytes(b"keep")
    symlink(target, rdir(config) / "lock")
    assert run(config, stand_in) == 1
    assert "gantry: error: " in capsys.readouterr().err and target.read_bytes() == b"keep"


def test_link_at_last_run_log_is_refused(tmp_path, stand_in, capsys):
    config = installation(tmp_path)
    assert run(config, stand_in) == 0
    before = (rdir(config) / "update.json").read_bytes()
    (rdir(config) / "last-run.log").unlink()
    symlink(rdir(config) / "update.json", rdir(config) / "last-run.log")
    capsys.readouterr()
    assert run(config, stand_in) == 1
    assert "gantry: error: " in capsys.readouterr().err
    assert (rdir(config) / "update.json").read_bytes() == before


# Windows only: an open file blocks a rename onto it and its deletion (U3, U4).
HOLD = """
    import sys, time
    handle = open(sys.argv[1], "rb")
    print("held", flush=True)
    time.sleep(float(sys.argv[2]))
    handle.close()
    time.sleep(60)
"""
OPEN_FILE_BLOCKS = "an open file blocks rename and delete only on Windows"


@pytest.fixture
def file_holder(tmp_path):
    """hold(path, seconds) -> a process holding path open for that long; all killed at teardown."""
    with holders(tmp_path, "hold", HOLD) as hold:
        yield lambda path, seconds=120: hold(path, seconds)


@pytest.mark.skipif(not WINDOWS, reason=OPEN_FILE_BLOCKS)
def test_windows_swap_fails_while_current_is_held_open(tmp_path, stand_in, file_holder, capsys):
    config = installation(tmp_path)
    assert run(config, stand_in) == 0
    current, _ = live(config)
    file_holder(rdir(config) / "current")
    capsys.readouterr()
    assert run(config, stand_in) == 1
    assert "gantry: error: swap failed: " in capsys.readouterr().err
    assert live(config)[0] == current and generations(config) == [current]
    assert record(config).last_build.swap == "swap failed"


@pytest.mark.skipif(not WINDOWS, reason=OPEN_FILE_BLOCKS)
def test_windows_swap_succeeds_once_current_is_released(tmp_path, stand_in, file_holder):
    config = installation(tmp_path)
    assert run(config, stand_in) == 0
    current, _ = live(config)
    file_holder(rdir(config) / "current", 0.5)
    assert run(config, stand_in) == 0
    assert live(config)[0] != current


@pytest.mark.skipif(not WINDOWS, reason=OPEN_FILE_BLOCKS)
def test_windows_open_file_defers_the_delete(tmp_path, stand_in, file_holder):
    config = installation(tmp_path)
    assert run(config, stand_in) == 0
    oldest, generation = live(config)
    assert run(config, stand_in) == 0
    proc = file_holder(generation / "index.html")
    assert run(config, stand_in) == 0
    # Only the folder is asserted: a failed Windows rmtree half-deletes, and
    # what is left differs by Python version (spike O2).
    assert generation.is_dir()
    proc.kill()
    proc.wait()
    assert run(config, stand_in) == 0
    assert not generation.exists()


# --- Task 8: gantry refresh and the real build ------------------------------------------


def absolute_sample_config(tmp_path, update=(), aggregator=None):
    """(config path, samples copy): a config outside the copy, every path
    absolute, reading the copy's sites, with a [refresh] table.

    Its portfolio JSON is where devagent's aggregator writes it with the copy's
    devagent/ folder as its home (DA_HOME), so the aggregator reads the same
    devagent config Gantry does; the committed sample document is placed there
    first. With `aggregator` a devagent checkout, the aggregator is the last
    update step (tests/helpers/run_aggregator.py, #12)."""
    import tomllib

    copy = copy_samples(tmp_path)
    raw = tomllib.loads((copy / "gantry.sample.toml").read_bytes().decode("utf-8"))
    inst = tmp_path / "inst"
    inst.mkdir()
    home = copy / "devagent"
    document = home.joinpath(*SP.DOCUMENT)
    document.parent.mkdir(parents=True)
    document.write_bytes(SP.SAMPLES_WRITTEN.read_bytes())
    if aggregator is not None:
        update = [*update, aggregator_command(aggregator, home, home)]
    lines = [
        "config_version = 2",
        f"devagent_config = {literal(copy / raw['devagent_config'])}",
        f"portfolio_json = {literal(document)}",
        f"output_dir = {literal(inst / 'out')}",
        f"as_of = {raw['as_of'].isoformat()}",
        f"portfolio_file = {literal(copy / raw['portfolio_file'])}",
        f"fires_list = {literal(copy / raw['fires_list'])}",
    ]
    for name, table in raw.get("site", {}).items():
        lines.append(f"\n[site.{name}]")
        lines += [f"{key} = {literal(copy / value) if isinstance(value, str) else str(value).lower()}"
                  for key, value in table.items()]
    lines.append(f"\n[refresh]\ndir = {literal(inst / 'refresh')}\nupdate = {toml_commands(update)}")
    path = inst / "gantry.toml"
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return path, copy


def loaded(path):
    from gantry.config import load_config

    return load_config(path)


# Runs one refresh in a child: `-P -c BOOT <gantry root> <config> <seconds>`.
BOOT = ("import sys; sys.path.insert(0, sys.argv[1]); from gantry.config import load_config; "
        "from gantry.refresh import refresh; "
        "sys.exit(refresh(load_config(sys.argv[2]), step_timeout_seconds=float(sys.argv[3])))")


def launch(config_path, env, cwd, seconds=120):
    return subprocess.run([sys.executable, "-P", "-c", BOOT, str(ROOT), str(config_path), str(seconds)],
                          env=env, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, timeout=600)


def minimal_env(tmp_path):
    """Only PATH, naming an empty folder (and SYSTEMROOT on Windows)."""
    empty = tmp_path / "empty-path"
    empty.mkdir(exist_ok=True)
    env = {"PATH": str(empty)}
    if WINDOWS:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return env


def generated_at(copy):
    """The `generated_at` of the copy's portfolio JSON (absolute_sample_config's)."""
    return json.loads(copy.joinpath("devagent", *SP.DOCUMENT).read_bytes())["generated_at"]


def test_sample_config_refresh(tmp_path, capsys):
    """The refresh runs the aggregator, then builds (#12): the document is the aggregator's, not the placed one."""
    path, copy = absolute_sample_config(tmp_path, aggregator=hd.require_devagent())
    assert_sample_refresh(path, capsys)
    assert generated_at(copy) != SP.FIXED_TIME


def test_sample_config_refresh_on_the_placed_document(tmp_path, capsys):
    """The refresh job alone, on the committed document: no aggregator, every host."""
    path, copy = absolute_sample_config(tmp_path)
    assert_sample_refresh(path, capsys)
    assert generated_at(copy) == SP.FIXED_TIME


def assert_sample_refresh(path, capsys):
    from gantry.build import marker_bytes
    from gantry.cli import main
    from sample_roles import NOT_BUILT, READABLE

    config = loaded(path)
    assert main(["refresh", "--config", str(path)]) == 0, capsys.readouterr().err
    current, generation = live(config)
    assert ID_SHAPE.fullmatch(current)
    assert (generation / "build.json").read_bytes() == marker_bytes(0)
    pages = {p.stem for p in (generation / "sites").glob("*.html")}
    assert len(READABLE) == 4 and pages == set(READABLE) and not pages & NOT_BUILT
    index = (generation / "index.html").read_bytes().decode("utf-8")
    assert "built " in index and "sample data as of" not in index
    assert not (path.parent / "out").exists()


def test_failed_update_still_builds_and_swaps(tmp_path):
    from gantry.cli import main

    path, _ = absolute_sample_config(tmp_path, update=[["gantry-35-no-such-program"]])
    assert main(["refresh", "--config", str(path)]) == 1
    config = loaded(path)
    _, generation = live(config)
    assert (generation / "index.html").is_file()
    assert record(config).failed_step.reason == "could not start"


def test_build_exit_1_on_purpose_is_swapped_in(tmp_path, capsys):
    from gantry.build import marker_bytes
    from gantry.cli import main

    path = write_site_tree(tmp_path / "tree", {"alpha": None}, tail='\n[refresh]\ndir = "refresh"\n')
    config = loaded(path)
    assert main(["refresh", "--config", str(path)]) == 1
    _, generation = live(config)
    assert (generation / "index.html").is_file()
    assert (generation / "build.json").read_bytes() == marker_bytes(1)
    last = record(config).last_build
    assert (last.ok, last.exit, last.swap) == (False, 1, "swapped")
    assert "gantry: error: build exited 1; its generation was swapped in\n" in capsys.readouterr().err


def test_removed_project_has_no_page_after_the_next_refresh(tmp_path, capsys):
    from gantry.cli import main
    from sample_roles import READABLE, SMALL

    path, copy = absolute_sample_config(tmp_path, aggregator=hd.require_devagent())
    config = loaded(path)
    assert main(["refresh", "--config", str(path)]) == 0, capsys.readouterr().err
    assert generated_at(copy) != SP.FIXED_TIME
    assert (live(config)[1] / "sites" / f"{SMALL}.html").is_file()
    devagent = copy / "devagent" / "config.toml"
    text = devagent.read_bytes().decode("utf-8")
    pattern = r"^\[project\." + SMALL + r"(\.[^\]]*)?\]\n(?:^(?!\[).*\n)*"
    removed = re.sub(pattern, "", text, flags=re.M)
    assert removed != text and f"project.{SMALL}" not in removed
    devagent.write_bytes(removed.encode("utf-8"))
    assert main(["refresh", "--config", str(path)]) == 0, capsys.readouterr().err
    pages = {p.stem for p in (live(config)[1] / "sites").glob("*.html")}
    assert pages == set(READABLE) - {SMALL}


def test_refresh_in_a_minimal_environment(tmp_path):
    """The aggregator step runs with only an empty PATH: its argv names every program absolutely and
    carries the search path the aggregator gets (tests/helpers/run_aggregator.py)."""
    path, copy = absolute_sample_config(tmp_path, aggregator=hd.require_devagent())
    work = tmp_path / "work"
    work.mkdir()
    result = launch(path, minimal_env(tmp_path), work)
    assert result.returncode == 0, result.stderr
    assert ID_SHAPE.fullmatch(live(loaded(path))[0])
    assert generated_at(copy) != SP.FIXED_TIME


def test_refresh_in_a_minimal_environment_on_the_placed_document(tmp_path):
    path, copy = absolute_sample_config(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    result = launch(path, minimal_env(tmp_path), work)
    assert result.returncode == 0, result.stderr
    assert ID_SHAPE.fullmatch(live(loaded(path))[0])
    assert generated_at(copy) == SP.FIXED_TIME


@pytest.mark.skipif(not WINDOWS, reason="taskkill.exe is a Windows program")
def test_windows_minimal_environment_still_stops_the_tree(tmp_path):
    argv, beat = heartbeat_step(tmp_path)
    path, _ = absolute_sample_config(tmp_path, update=[argv])
    work = tmp_path / "work"
    work.mkdir()
    result = launch(path, minimal_env(tmp_path), work, seconds=2)
    assert result.returncode == 1, result.stderr
    assert record(loaded(path)).failed_step.reason == "timed out"
    assert_heartbeat_stopped(beat)


def test_planted_json_module_never_runs(tmp_path):
    path, _ = absolute_sample_config(tmp_path)
    config = loaded(path)
    sentinel = tmp_path / "sentinel"
    planted = ("open(" + repr(str(sentinel)) + ", 'wb').close()\n"
               "raise ImportError('planted json')\n").encode("utf-8")
    work = tmp_path / "work"
    for folder in (tmp_path / "inst", work, rdir(config)):
        folder.mkdir(exist_ok=True)
        (folder / "json.py").write_bytes(planted)
    result = launch(path, minimal_env(tmp_path), work)
    assert result.returncode == 0, result.stderr
    assert ID_SHAPE.fullmatch(live(config)[0]) and not sentinel.exists()
    # Control: the same build without -P imports the planted json.py.
    no_p = [sys.executable, "-m", "gantry", "build", "--config", str(path)]
    assert refresh.refresh(config, step_timeout_seconds=120, build_command=no_p) == 1
    assert sentinel.exists()


# --- Task 9: the end-to-end data root ---------------------------------------------------


@pytest.fixture
def git_hermetic(git_ready, monkeypatch):
    """git through refresh(): no ambient GIT_* (git -C does not override an
    exported GIT_DIR), no global or system config."""
    hermetic = git_env(hermetic=True)
    for name in os.environ.keys() - hermetic.keys():
        monkeypatch.delenv(name)
    for name, value in hermetic.items():
        monkeypatch.setenv(name, value)


def page(config, site):
    """The live generation's page for one site."""
    return (live(config)[1] / "sites" / f"{site}.html").read_bytes().decode("utf-8")


def test_refresh_repo_shape(tmp_path, git_hermetic):
    from helpers.refresh_repo import make_refresh_repo
    from helpers_history import git

    repo = make_refresh_repo(tmp_path)
    assert git(repo.data, "rev-parse", "--is-shallow-repository").strip() == "false"
    config = loaded(repo.config)
    assert config.refresh.update == (("git", "-C", str(repo.data), "pull", "--ff-only"),)
    assert not repo.refresh_dir.exists() and repo.wbs().is_file()
    assert config.portfolio_json == PurePath(repo.document) and repo.document.is_file()
    assert (repo.da_home / "config.toml").read_bytes() == (repo.config.parent / "devagent.toml").read_bytes()
    # With a devagent checkout, the aggregator is the second update step.
    with_aggregator = make_refresh_repo(tmp_path / "two", aggregator=tmp_path / "devagent")
    update = loaded(with_aggregator.config).refresh.update
    assert len(update) == 2 and update[0][:2] == ("git", "-C")
    assert update[1] == tuple(aggregator_command(tmp_path / "devagent", with_aggregator.config.parent,
                                                 with_aggregator.da_home))


def test_end_to_end_pull_then_unreachable_remote(tmp_path, git_hermetic, capsys):
    """The pull, then the aggregator, then the build (#12): the page shows the pushed title."""
    from helpers.refresh_repo import make_refresh_repo

    repo = make_refresh_repo(tmp_path, aggregator=hd.require_devagent())
    config = loaded(repo.config)
    repo.push_title("Keep the lamp log", "Keep the lamp journal")
    assert refresh.refresh(config, step_timeout_seconds=120) == 0, capsys.readouterr().err
    assert "Keep the lamp journal" in page(config, repo.site)
    first = record(config)
    live_before = (repo.refresh_dir / "current").read_bytes()

    repo.push_title("Keep the lamp journal", "Keep the lamp diary")
    repo.break_remote()
    assert refresh.refresh(config, step_timeout_seconds=120) == 1
    r = record(config)
    assert r.ok is False and r.steps_run == 1
    assert (r.failed_step.index, r.failed_step.program) == (1, "git")
    assert r.failed_step.reason.startswith("exit ") and r.failed_step.reason != "exit 0"
    assert r.last_ok_at == first.attempted_at
    assert (repo.refresh_dir / "current").read_bytes() != live_before
    assert "Keep the lamp journal" in page(config, repo.site) and "Keep the lamp diary" not in page(config, repo.site)


def test_stale_index_lock_fails_the_update_until_removed(tmp_path, git_hermetic, capsys):
    """The pull's own failure, on the placed document (no aggregator): the data clone's WBS.md is what moves."""
    from helpers.refresh_repo import make_refresh_repo

    repo = make_refresh_repo(tmp_path)
    config = loaded(repo.config)
    assert refresh.refresh(config, step_timeout_seconds=120) == 0, capsys.readouterr().err
    repo.push_title("Keep the lamp log", "Keep the lamp journal")
    lock = repo.data / ".git" / "index.lock"
    lock.write_bytes(b"")
    assert refresh.refresh(config, step_timeout_seconds=120) == 1
    assert record(config).failed_step.index == 1
    assert b"Keep the lamp journal" not in repo.wbs().read_bytes()
    assert b"index.lock" in (repo.refresh_dir / "last-run.log").read_bytes()
    lock.unlink()
    assert refresh.refresh(config, step_timeout_seconds=120) == 0, capsys.readouterr().err
    assert b"Keep the lamp journal" in repo.wbs().read_bytes()


def test_end_to_end_pull_then_unreachable_remote_on_the_placed_document(tmp_path, git_hermetic, capsys):
    """The refresh job's record of a pull that cannot reach its remote, with no aggregator step."""
    from helpers.refresh_repo import make_refresh_repo

    repo = make_refresh_repo(tmp_path)
    config = loaded(repo.config)
    assert refresh.refresh(config, step_timeout_seconds=120) == 0, capsys.readouterr().err
    first = record(config)
    repo.break_remote()
    assert refresh.refresh(config, step_timeout_seconds=120) == 1
    r = record(config)
    assert r.ok is False and r.steps_run == 1
    assert (r.failed_step.index, r.failed_step.program) == (1, "git")
    assert r.last_ok_at == first.attempted_at


def test_failed_aggregator_step_is_the_recorded_failed_step(tmp_path, git_hermetic, capsys):
    """A failed aggregator is update step 2: the build still runs on the document already there, and is
    swapped in (#35's rule for a failed update)."""
    from helpers.refresh_repo import make_refresh_repo

    repo = make_refresh_repo(tmp_path, aggregator=hd.require_devagent())
    config = loaded(repo.config)
    assert refresh.refresh(config, step_timeout_seconds=120) == 0, capsys.readouterr().err
    before = repo.document.read_bytes()
    live_before = (repo.refresh_dir / "current").read_bytes()
    repo.break_aggregator()
    assert refresh.refresh(config, step_timeout_seconds=120) == 1
    r = record(config)
    assert r.ok is False and r.steps_run == 2
    assert (r.failed_step.index, r.failed_step.reason) == (2, "exit 1")
    assert repo.document.read_bytes() == before
    assert (repo.refresh_dir / "current").read_bytes() != live_before
    # The new generation shows the OLD document's data time (#12's data-time line).
    from datetime import datetime, timezone

    written = datetime.fromisoformat(json.loads(before)["generated_at"]).astimezone(timezone.utc)
    index = (live(config)[1] / "index.html").read_bytes().decode("utf-8")
    assert f"; devagent data written {written:%Y-%m-%d %H:%M} UTC" in index


# --- final review fixes (#35) ----------------------------------------------------------


@pytest.mark.skipif(WINDOWS, reason="a POSIX process group; the Windows kill is taskkill's")
def test_a_step_that_cannot_be_stopped_is_recorded_not_raised(tmp_path, stand_in, monkeypatch):
    # killpg and kill both refused (EPERM: a step run as another user, say).
    nap = script(tmp_path / "helpers", "nap", """
        import time
        time.sleep(4)
    """)
    config = installation(tmp_path, [[sys.executable, str(nap)]])

    def refuse(*args):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(refresh.os, "killpg", refuse)
    monkeypatch.setattr(refresh.os, "kill", refuse)
    monkeypatch.setattr(refresh, "KILL_WAIT_SECONDS", 0.5)
    assert run(config, stand_in, step_timeout_seconds=1) == 1
    assert record(config).failed_step.reason == "timed out"
    assert b"could not stop" in (rdir(config) / "last-run.log").read_bytes()


class _FullDisk:
    """last-run.log on a full disk: the children's descriptor works, Gantry's own writes fail."""

    def __init__(self, real):
        self.real = real

    def fileno(self):
        return self.real.fileno()

    def write(self, data):
        raise OSError(28, "No space left on device")

    def flush(self):
        pass

    def close(self):
        self.real.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def test_a_log_that_cannot_be_written_does_not_raise(tmp_path, stand_in, monkeypatch, capsys):
    config = installation(tmp_path, [["gantry-35-no-such-program"]])
    real = refresh.open_confined

    def full(roots, path, *args, **kwargs):
        handle = real(roots, path, *args, **kwargs)
        return _FullDisk(handle) if os.path.basename(path) == "last-run.log" else handle

    monkeypatch.setattr(refresh, "open_confined", full)
    assert run(config, stand_in) == 1
    assert "gantry: error: update step 1 (gantry-35-no-such-program): could not start" in capsys.readouterr().err
    assert record(config).last_build.swap == "swapped"


def test_an_unexpected_os_error_is_an_error_line_not_raised(tmp_path, stand_in, monkeypatch, capsys):
    config = installation(tmp_path)

    def broken(environ):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(refresh, "build_env", broken)
    assert run(config, stand_in) == 1
    assert "gantry: error: " in capsys.readouterr().err and "Input/output error" in (
        rdir(config) / "last-run.log").read_bytes().decode("utf-8")


@pytest.mark.parametrize("which", [1, 2])
def test_a_record_that_cannot_be_written_stops_the_run(tmp_path, stand_in, monkeypatch, capsys, which):
    config = installation(tmp_path)
    real, writes = refresh.write_confined, []

    def refuse_record(roots, path, data, **kwargs):
        if os.path.basename(path) == "update.json":
            writes.append(path)
            if len(writes) >= which:  # this record write and its retries
                raise PermissionError(13, "Permission denied")
        return real(roots, path, data, **kwargs)

    monkeypatch.setattr(refresh, "write_confined", refuse_record)
    monkeypatch.setattr(refresh, "SWAP_RETRY_SECONDS", 0.2)
    assert run(config, stand_in) == 1
    assert "gantry: error: cannot write update.json: Permission denied\n" in capsys.readouterr().err
    if which == 1:  # no build follows a failed first record
        assert generations(config) == [] and not (rdir(config) / "current").exists()
    else:  # the build was swapped in; no sweep ran after the failed second record
        assert len(generations(config)) == 1 and (rdir(config) / "current").exists()


def test_a_first_word_ending_in_a_separator_records_no_path(tmp_path, stand_in):
    folder = tmp_path / "programs"
    folder.mkdir()
    config = installation(tmp_path, [[str(folder) + os.sep]])
    assert run(config, stand_in) == 1
    r = record(config)
    assert r.failed_step == FailedStep(1, "programs", "could not start")
    assert str(tmp_path) not in (rdir(config) / "update.json").read_bytes().decode("utf-8")

