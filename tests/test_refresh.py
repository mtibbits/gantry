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
from helpers_config import write_config
from helpers_links import link_dir, symlink
from test_samples import ROOT

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


@pytest.fixture
def lock_holder(tmp_path):
    """hold(folder) -> the Popen of a process holding folder's refresh lock; every one is killed at teardown."""
    procs = []

    def hold(folder):
        holder = script(tmp_path / "helpers", "holder", HOLDER)
        proc = subprocess.Popen([sys.executable, str(holder), str(ROOT), str(folder)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
        procs.append(proc)
        assert proc.stdout.readline().strip() == b"held"
        return proc

    yield hold
    for proc in procs:
        proc.kill()
        proc.wait()
        proc.stdout.close()


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


def test_lock_dies_with_its_holder(tmp_path, lock_holder):
    proc = lock_holder(tmp_path)
    assert refresh.acquire_lock(writable(tmp_path), tmp_path) is None
    proc.kill()
    proc.wait()
    handle = refresh.acquire_lock(writable(tmp_path), tmp_path)
    assert handle is not None
    handle.close()


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


def test_discard_removes_one_and_ignores_errors(tmp_path, monkeypatch):
    generation = make_generation(tmp_path, "g1", b"x")
    refresh.discard(writable(tmp_path), generation)
    assert not generation.exists()
    refresh.discard(writable(tmp_path), generation)  # already gone: no error

