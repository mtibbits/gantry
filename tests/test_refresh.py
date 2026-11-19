"""The refresh job (#35): its update record, steps, lock, generations and one run.

docs/refresh-and-serve.md states the job. Every file a test makes lives under
tmp_path; the commit check refuses a tracked build.json or update.json.
"""

import json

import pytest

from gantry import refresh
from gantry.refresh import FailedStep, LastBuild, UpdateRead, UpdateRecord, parse_update_record, record_bytes
from gantry.roots import directory_roots

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
