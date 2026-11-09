"""Every file access goes through gantry/roots.py, and only under a run's roots (#59).

Every path here is built at run time from tmp_path. The audit hook that
shows a file outside the roots is never opened or removed is installed once,
at import, for the whole test process (one per xdist worker), and cannot be
removed; it records only inside `recording()` and never raises.
"""

import os
import sys
from contextlib import contextmanager
from pathlib import Path, PurePath

import pytest

import helpers_links
from gantry.roots import (
    NUL_MESSAGE,
    PACKAGE_KEY,
    LinkRefused,
    OutsideRoots,
    directory_roots,
    exists_confined,
    gantry_root,
    list_confined,
    mkdir_confined,
    open_confined,
    outside,
    package_roots,
    read_bytes_confined,
    read_text_confined,
    remove_confined,
    resolve_confined,
    sample_dir,
    stat_confined,
    write_confined,
)
from helpers_config import copy_samples
from helpers_links import CI_SUFFIX, SYMLINK_REASON, is_link, junction, link_dir, symlink

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


def pure(value):
    return isinstance(value, PurePath) and not isinstance(value, Path)


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


# --- the confined functions -----------------------------------------------------------


@pytest.fixture
def tree(tmp_path):
    """(copy, roots, elsewhere): harbor read-only, out writable, elsewhere in no root."""
    copy = copy_samples(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_bytes(b"keep")
    (elsewhere / "real.md").write_bytes(b"real")
    return copy, directory_roots([copy / "harbor"], writable=[copy / "out"]), elsewhere


def test_no_concrete_path_crosses_the_boundary(tree):
    copy, roots, _ = tree
    for root in roots.roots + package_roots().roots:
        assert pure(root.path) and pure(root.given), root
    assert pure(gantry_root()) and pure(sample_dir())
    assert pure(resolve_confined(roots, copy / "harbor" / "WBS.md", key="WBS.md"))
    entries = list_confined(roots, copy / "harbor", key="devdoc_dir")
    assert entries
    for entry in entries:
        for value in vars(entry).values():
            assert not isinstance(value, (os.DirEntry, Path)), entry


@pytest.mark.parametrize("call", [exists_confined, stat_confined, list_confined])
def test_outside_every_root_raises_not_false_or_empty(tree, call):
    _, roots, elsewhere = tree
    with pytest.raises(OutsideRoots):
        call(roots, elsewhere, key="x")
    with pytest.raises(OutsideRoots):
        call(roots, elsewhere / "nope", key="x")


def test_inside_missing_is_false_or_file_not_found(tree):
    copy, roots, _ = tree
    missing = copy / "harbor" / "nope"
    assert exists_confined(roots, missing, key="x") is False
    for call in (stat_confined, list_confined, read_bytes_confined):
        with pytest.raises(FileNotFoundError):
            call(roots, missing, key="x")


@pytest.mark.parametrize("mode", ["w", "x", "a", "r+", "wb"])
def test_write_modes_need_a_writable_root(tree, mode):
    copy, roots, _ = tree
    with pytest.raises(OutsideRoots) as e:
        open_confined(roots, copy / "harbor" / "new.txt", mode, key="x")
    assert e.value.kind == "write"
    assert not (copy / "harbor" / "new.txt").exists()
    mkdir_confined(roots, copy / "out", key="output_dir")
    target = copy / "out" / "new.txt"
    if mode == "r+":
        target.write_bytes(b"")
    with open_confined(roots, target, mode, key="output_dir"):
        pass
    assert target.exists()


def test_read_refusal_kind_is_read(tree):
    _, roots, elsewhere = tree
    with pytest.raises(OutsideRoots) as e:
        read_bytes_confined(roots, elsewhere / "keep.txt", key="x")
    assert e.value.kind == "read"


def test_nul_raises_value_error_before_the_root_check(tree):
    _, roots, elsewhere = tree
    for call in (read_bytes_confined, exists_confined, stat_confined, list_confined):
        with pytest.raises(ValueError) as e:
            call(roots, str(elsewhere / "a") + "\x00b", key="x")
        assert not isinstance(e.value, OutsideRoots)
        assert str(e.value) == NUL_MESSAGE


def test_dotdot_leaving_a_root_is_refused(tree, tmp_path):
    copy, roots, _ = tree
    with pytest.raises(OutsideRoots) as e:
        read_bytes_confined(roots, copy / "harbor" / ".." / "lighthouse" / "WBS.md", key="WBS.md", site="harbor")
    assert str(e.value) == "WBS.md for site harbor is outside the configured roots"
    for part in (str(tmp_path), os.path.realpath(tmp_path)):
        assert part not in str(e.value)


def test_write_and_remove_outside_are_refused_and_untouched(tree):
    _, roots, elsewhere = tree
    with recording() as record:
        # The control: a plain open outside must be recorded, so the hook works.
        with open(elsewhere / "keep.txt", "rb"):
            pass
        assert touched(record, elsewhere)
        record.clear()
        with pytest.raises(OutsideRoots) as e:
            write_confined(roots, elsewhere / "x.html", b"page", key="output_dir")
        assert str(e.value) == "output_dir is outside the configured roots"
        with pytest.raises(OutsideRoots):
            remove_confined(roots, elsewhere / "keep.txt", key="output_dir")
        with pytest.raises(OutsideRoots):
            mkdir_confined(roots, elsewhere / "made", key="output_dir")
        found = touched(record, elsewhere)
    assert found == []
    assert (elsewhere / "keep.txt").read_bytes() == b"keep"
    assert not (elsewhere / "x.html").exists()
    assert not (elsewhere / "made").exists()


def test_symlink_below_a_root_is_refused(tree):
    copy, roots, elsewhere = tree
    wbs = copy / "harbor" / "WBS.md"
    wbs.unlink()
    symlink(elsewhere / "real.md", wbs)
    with recording() as record:
        with pytest.raises(OutsideRoots) as e:
            read_bytes_confined(roots, wbs, key="WBS.md", site="harbor")
        found = touched(record, elsewhere)
    assert found == []
    assert str(e.value) == "WBS.md for site harbor is outside the configured roots"
    entry = {e.name: e for e in list_confined(roots, copy / "harbor", key="devdoc_dir")}["WBS.md"]
    assert entry.is_link and entry.leaves_roots
    assert not entry.is_file and not entry.is_dir


@needs_windows
def test_junction_below_a_root_is_refused(tree):
    copy, roots, elsewhere = tree
    (elsewhere / "f.txt").write_bytes(b"outside")
    junc = copy / "harbor" / "junc"
    junction(elsewhere, junc)
    with recording() as record:
        with pytest.raises(OutsideRoots):
            open_confined(roots, junc / "f.txt", "rb", key="devdoc_dir", site="harbor")
        with pytest.raises(OutsideRoots):
            list_confined(roots, junc, key="devdoc_dir", site="harbor")
        found = touched(record, elsewhere)
    assert found == []
    entry = {e.name: e for e in list_confined(roots, copy / "harbor", key="devdoc_dir")}["junc"]
    assert entry.is_link and entry.leaves_roots
    assert not entry.is_file and not entry.is_dir


def test_link_inside_the_roots_lists_as_its_target(tree):
    copy, roots, _ = tree
    sub = copy / "harbor" / "sub"
    sub.mkdir()
    link_dir(sub, copy / "harbor" / "inner")
    entry = {e.name: e for e in list_confined(roots, copy / "harbor", key="devdoc_dir")}["inner"]
    assert entry.is_link and entry.is_dir
    assert not entry.is_file and not entry.leaves_roots


def test_follow_links_false_refuses_a_symlink(tree):
    copy, roots, _ = tree
    link = copy / "harbor" / "link.md"
    symlink(copy / "harbor" / "WBS.md", link)
    assert read_bytes_confined(roots, link, key="WBS.md") == (copy / "harbor" / "WBS.md").read_bytes()
    with pytest.raises(LinkRefused) as e:
        read_text_confined(roots, link, key="WBS.md", site="harbor", follow_links=False)
    assert str(e.value) == "WBS.md for site harbor is a link"
    assert isinstance(e.value, OutsideRoots)


@needs_windows
def test_follow_links_false_refuses_a_junction(tree):
    copy, roots, _ = tree
    sub = copy / "harbor" / "sub"
    sub.mkdir()
    (sub / "x.md").write_bytes(b"inside")
    junction(sub, copy / "harbor" / "j")
    assert read_text_confined(roots, copy / "harbor" / "j" / "x.md", key="x") == "inside"
    with recording() as record:
        with pytest.raises(LinkRefused):
            read_text_confined(roots, copy / "harbor" / "j" / "x.md", key="x", follow_links=False)
        opened = [p for e, p in record if e == "open"]
    assert opened == []


def test_follow_links_false_allows_a_link_at_the_root(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "x.md").write_bytes(b"inside")
    link_dir(target, tmp_path / "linked")
    roots = directory_roots([tmp_path / "linked"])
    assert read_text_confined(roots, tmp_path / "linked" / "x.md", key="x", follow_links=False) == "inside"


def test_outside_lists_by_key_and_skips_package(tmp_path):
    copy = copy_samples(tmp_path)
    roots = directory_roots([copy / ".." / "x", tmp_path / "samples-x", copy / "out", copy])
    assert [r.key for r in outside(roots, copy)] == ["x", "samples-x"]
    assert outside(package_roots(), tmp_path) == []
    assert package_roots().roots[0].key == PACKAGE_KEY


def test_directory_roots_writable_is_a_union(tmp_path):
    roots = directory_roots([tmp_path / "a"], writable=[tmp_path / "w"])
    assert [(r.key, r.writable) for r in roots.roots] == [("a", False), ("w", True)]


def test_every_path_key_has_a_root_kind():
    from gantry.config import KEYS, REQUIREMENTS_KEYS, SITE_KEYS, Key, missing_root_kinds

    assert missing_root_kinds(KEYS, SITE_KEYS, REQUIREMENTS_KEYS) == []
    planted = Key("x", "path", False, None, "#0", "`x`")
    assert missing_root_kinds(KEYS, (planted,)) == ["x"]
    planted = Key("y", "path_or_false", False, None, "#0", "`y`")
    assert missing_root_kinds((planted,), SITE_KEYS) == ["y"]
    for key in (*KEYS, *SITE_KEYS):
        if key.kind in ("path", "path_or_false"):
            assert key.root in ("file", "directory"), key.name


def test_write_confined_mode_and_bytes(tree):
    copy, roots, _ = tree
    out = copy / "out"
    mkdir_confined(roots, out / "sites", key="output_dir")
    write_confined(roots, out / "sites" / "harbor.html", b"<p>page</p>", key="output_dir")
    assert (out / "sites" / "harbor.html").read_bytes() == b"<p>page</p>"
    assert [p.name for p in (out / "sites").iterdir()] == ["harbor.html"]
    with open(out / "sites" / "plain.html", "wb") as handle:
        handle.write(b"x")
    if os.name != "nt":
        mode = os.stat(out / "sites" / "harbor.html").st_mode & 0o777
        assert mode == os.stat(out / "sites" / "plain.html").st_mode & 0o777
