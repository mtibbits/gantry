"""Every file access goes through gantry/roots.py, and only under a run's roots (#59).

Every path here is built at run time from tmp_path. The audit hook that
shows a file outside the roots is never opened or removed is installed once,
at import, for the whole test process (one per xdist worker), and cannot be
removed; it records only inside `recording()` and never raises.
"""

import os
import sys
from contextlib import contextmanager

import pytest

import helpers_links
from helpers_links import CI_SUFFIX, SYMLINK_REASON, is_link, junction, symlink

WINDOWS = os.name == "nt"
needs_windows = pytest.mark.skipif(not WINDOWS, reason="a junction needs Windows")

# --- the audit hook -------------------------------------------------------------------

_EVENTS = {"open", "os.remove", "os.rename", "os.mkdir", "os.rmdir", "os.scandir", "os.listdir"}
_RECORD = []
_ON = [False]


def _hook(event, args):
    if not _ON[0] or event not in _EVENTS:
        return
    try:
        _RECORD.append((event, str(args[0])))
    except Exception:
        pass  # an audit hook must never raise


sys.addaudithook(_hook)


@contextmanager
def recording():
    """The (event, first argument) pairs the process raises while inside."""
    _RECORD.clear()
    _ON[0] = True
    try:
        yield _RECORD
    finally:
        _ON[0] = False


def touched(record, folder):
    """The recorded events whose path lies under `folder`, as given or resolved."""
    prefixes = {os.path.normcase(str(folder)), os.path.normcase(os.path.realpath(folder))}
    return [(e, p) for e, p in record if any(os.path.normcase(p).startswith(x) for x in prefixes)]


# --- Task 1: the link helpers ---------------------------------------------------------


def test_symlink_helper_skips_without_privilege_and_fails_under_ci(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        e = OSError("A required privilege is not held by the client")
        e.winerror = 1314
        raise e

    monkeypatch.setattr(helpers_links.os, "symlink", refuse)
    with pytest.raises(pytest.skip.Exception) as skipped:
        symlink(tmp_path / "t", tmp_path / "l", env={})
    assert str(skipped.value) == SYMLINK_REASON
    with pytest.raises(pytest.fail.Exception) as failed:
        symlink(tmp_path / "t", tmp_path / "l", env={"CI": "1"})
    assert str(failed.value).endswith(CI_SUFFIX)


@needs_windows
def test_junction_helper_is_a_link(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "f.txt").write_bytes(b"inside")
    j = tmp_path / "j"
    junction(target, j)
    assert is_link(j)
    assert not os.path.islink(j)
    assert (j / "f.txt").read_bytes() == b"inside"
