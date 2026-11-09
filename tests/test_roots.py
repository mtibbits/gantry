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


def _two_roots(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for folder in (a, b):
        folder.mkdir()
        (folder / "secret.md").write_bytes(b"secret")
    return a, b, directory_roots([a, b])


def test_follow_links_false_refuses_a_link_to_its_own_root(tmp_path):
    a, _, roots = _two_roots(tmp_path)
    link_dir(a, a / "loop")
    with pytest.raises(LinkRefused):
        read_text_confined(roots, a / "loop" / "secret.md", key="x", follow_links=False)


def test_follow_links_false_refuses_a_link_to_a_sibling_root(tmp_path):
    a, b, roots = _two_roots(tmp_path)
    link_dir(b, a / "to_b")
    with pytest.raises(LinkRefused):
        read_text_confined(roots, a / "to_b" / "secret.md", key="x", follow_links=False)


def test_follow_links_false_refuses_a_link_before_dotdot(tmp_path):
    a, _, roots = _two_roots(tmp_path)
    (a / "deep" / "inner").mkdir(parents=True)
    (a / "deep" / "x.md").write_bytes(b"deep")
    (a / "x.md").write_bytes(b"top")
    link_dir(a / "deep" / "inner", a / "dj")
    with pytest.raises(LinkRefused):
        read_text_confined(roots, str(a / "dj") + os.sep + ".." + os.sep + "x.md", key="x", follow_links=False)


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


# --- Task 4: config_roots and the confined site list ----------------------------------


def literal(path):
    """A TOML literal string for a path: no escapes, so a Windows path needs none."""
    text = str(path)
    assert "'" not in text
    return f"'{text}'"


def replace_bytes(path, old, new):
    before = path.read_bytes().decode("utf-8")
    after = before.replace(old, new)
    assert after != before
    path.write_bytes(after.encode("utf-8"))


def roots_of(config_path):
    from gantry.config import load_config
    from gantry.sites import config_roots

    return config_roots(load_config(config_path))


def test_sample_roots_lie_inside_the_sample_directory():
    from helpers_config import sample_projects
    from test_samples import GANTRY_SAMPLE

    roots = roots_of(GANTRY_SAMPLE)
    assert outside(roots, sample_dir()) == []
    found = {(r.key, r.site) for r in roots.roots}
    assert {("devdoc_dir", n) for n in sample_projects()} <= found
    assert {("--config", None), ("devagent_config", None), ("output_dir", None), ("portfolio_file", None),
            ("site.lighthouse.requirements", None)} <= found
    assert [r.key for r in roots.roots if r.writable] == ["output_dir"]


def _out_output_dir(copy, elsewhere):
    replace_bytes(copy / "gantry.sample.toml", 'output_dir = "out"', f"output_dir = {literal(elsewhere)}")


def _out_requirements(copy, elsewhere):
    replace_bytes(copy / "gantry.sample.toml", 'requirements = "lighthouse/requirements.md"',
                  'requirements = "../../x"')


def _out_devdoc_dir(copy, elsewhere):
    replace_bytes(copy / "devagent" / "config.toml", 'devdoc_dir = "../harbor"',
                  f"devdoc_dir = {literal(elsewhere)}")


def _link_out(make):
    def move(copy, elsewhere):
        import shutil

        shutil.copytree(copy / "harbor", elsewhere / "harbor")
        shutil.rmtree(copy / "harbor")
        make(elsewhere / "harbor", copy / "harbor")
    return move


OUT_CASES = {
    "output_dir": (_out_output_dir, [("output_dir", None)]),
    "requirements": (_out_requirements, [("site.lighthouse.requirements", None)]),
    "devdoc_dir": (_out_devdoc_dir, [("devdoc_dir", "harbor")]),
    "linked site": (_link_out(lambda t, l: symlink(t, l, directory=True)), [("devdoc_dir", "harbor")]),
    "junction site": (_link_out(junction), [("devdoc_dir", "harbor")]),
}


@pytest.mark.parametrize("case", list(OUT_CASES))
def test_outside_names_the_root_changed(tmp_path, case):
    copy = copy_samples(tmp_path)
    assert outside(roots_of(copy / "gantry.sample.toml"), copy) == []
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    move, expected = OUT_CASES[case]
    move(copy, elsewhere)
    assert [(r.key, r.site) for r in outside(roots_of(copy / "gantry.sample.toml"), copy)] == expected


def test_writable_root_may_not_contain_an_input(tmp_path):
    from gantry.config import ConfigError

    copy = copy_samples(tmp_path)
    config = copy / "gantry.sample.toml"
    replace_bytes(config, 'output_dir = "out"', f"output_dir = {literal(copy)}")
    with pytest.raises(ConfigError) as e:
        roots_of(config)
    text = str(e.value)
    assert "'output_dir'" in text and "contains" in text
    assert "devdoc_dir of site harbor" in text and "the config file itself" in text
    assert "key 'devagent_config'" in text
    replace_bytes(config, f"output_dir = {literal(copy)}", f"output_dir = {literal(copy / 'harbor')}")
    with pytest.raises(ConfigError) as e:
        roots_of(config)
    assert "'output_dir' equals devdoc_dir of site harbor" in str(e.value)
    replace_bytes(config, f"output_dir = {literal(copy / 'harbor')}", 'output_dir = "out"')
    assert roots_of(config)


def test_writable_root_containment_exits_1_at_the_cli(tmp_path):
    from helpers_config import run_main

    copy = copy_samples(tmp_path)
    replace_bytes(copy / "gantry.sample.toml", 'output_dir = "out"', 'output_dir = "."')
    code, out, err = run_main("--config", str(copy / "gantry.sample.toml"))
    assert code == 1 and out == ""
    assert "'output_dir' contains" in err


def test_estimates_naming_a_folder_admits_nothing_under_it(tmp_path):
    copy = copy_samples(tmp_path)
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "x.md").write_bytes(b"x")
    config = copy / "gantry.sample.toml"
    config.write_bytes(config.read_bytes() + f"\n[site.harbor]\nestimates = {literal(folder)}\n".encode())
    roots = roots_of(config)
    with pytest.raises(OutsideRoots):
        read_bytes_confined(roots, folder / "x.md", key="estimates file", site="harbor")


def test_nul_devdoc_dir_adds_no_root_and_is_a_problem(tmp_path):
    from gantry.config import load_config
    from gantry.sites import config_roots, load_sites
    from helpers_config import write_config

    p = write_config(tmp_path)
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "WBS.md").write_bytes(b"x\n")
    (tmp_path / "devagent.toml").write_bytes(
        b'[project.nul]\ndevdoc_dir = "a\\u0000b"\n[project.good]\ndevdoc_dir = "plans"\n')
    config = load_config(p)
    assert [r.site for r in config_roots(config).roots if r.key == "devdoc_dir"] == ["good"]
    sites, _ = load_sites(config)
    assert sites[0].problem.startswith("cannot check the planning directory: ")
    assert sites[1].problem is None


def test_bootstrap_reads_only_what_each_step_names(tmp_path):
    from gantry.config import load_config
    from gantry.roots import bootstrap_roots, with_file
    from gantry.sites import config_roots

    copy = copy_samples(tmp_path)
    path = copy / "gantry.sample.toml"
    roots = bootstrap_roots(path)
    assert read_bytes_confined(roots, path, key="--config")
    devagent = copy / "devagent" / "config.toml"
    with pytest.raises(OutsideRoots):
        read_bytes_confined(roots, devagent, key="devagent_config")
    roots = with_file(roots, devagent, key="devagent_config")
    assert read_bytes_confined(roots, devagent, key="devagent_config")
    with pytest.raises(OutsideRoots):
        read_bytes_confined(roots, copy / "harbor" / "WBS.md", key="WBS.md", site="harbor")
    roots = config_roots(load_config(path))
    assert read_bytes_confined(roots, copy / "harbor" / "WBS.md", key="WBS.md", site="harbor")


# --- Task 5: the stamp, the build and the CLI ------------------------------------------


def test_missing_output_dir_and_parents_are_created(tmp_path):
    from helpers_config import run_main

    copy = copy_samples(tmp_path)
    replace_bytes(copy / "gantry.sample.toml", 'output_dir = "out"', 'output_dir = "deep/out"')
    code, _, err = run_main("--config", str(copy / "gantry.sample.toml"), "--site", "harbor")
    assert code == 0, err
    assert (copy / "deep" / "out" / "sites" / "harbor.html").is_file()


def test_copy_named_samples_outside_the_checkout_is_not_sample_mode(tmp_path):
    from datetime import datetime, timezone

    from gantry.config import load_config
    from gantry.stamp import sample_mode, stamp_text

    copy = copy_samples(tmp_path)
    assert copy.name == "samples"
    config = load_config(copy / "gantry.sample.toml")
    assert sample_mode(config) is False
    assert stamp_text(config, now=datetime(2026, 11, 7, 14, 3, tzinfo=timezone.utc)).endswith(
        "UTC; data as of 2026-01-20")


def _stamp_fixtures(tmp_path):
    """{name: config path}: the sample config, a copy, and the copy with a root moved out."""
    from test_samples import GANTRY_SAMPLE

    found = {"sample": GANTRY_SAMPLE}
    for name, move in (("copy", None), ("output_dir", _out_output_dir), ("devdoc_dir", _out_devdoc_dir)):
        copy = copy_samples(tmp_path / name)
        if move is not None:
            elsewhere = tmp_path / name / "elsewhere"
            elsewhere.mkdir()
            move(copy, elsewhere)
        found[name] = copy / "gantry.sample.toml"
    return found


def test_stamp_agrees_with_outside(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from gantry import stamp
    from gantry.config import load_config
    from gantry.sites import config_roots
    from gantry.stamp import SAMPLE_STAMP

    now = datetime(2026, 11, 7, 14, 3, tzinfo=timezone.utc)
    fixtures = _stamp_fixtures(tmp_path)

    def agree(samples):
        verdicts = {}
        for name, path in fixtures.items():
            config = load_config(path)
            sample = stamp.stamp_text(config, now=now) == SAMPLE_STAMP.format(date=config.as_of.isoformat())
            assert sample == (outside(config_roots(config), samples) == []), name
            verdicts[name] = sample
        return verdicts

    assert agree(sample_dir()) == {"sample": True, "copy": False, "output_dir": False, "devdoc_dir": False}
    # The stamp follows the sample directory it is given: point it at the copy.
    copy = fixtures["copy"].parent
    monkeypatch.setattr(stamp, "sample_dir", lambda: PurePath(copy))
    assert agree(PurePath(copy)) == {"sample": False, "copy": True, "output_dir": False, "devdoc_dir": False}


def test_build_lists_linked_site_outside_roots(tmp_path):
    from helpers_config import run_main

    copy = copy_samples(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "WBS.md").write_bytes((copy / "harbor" / "WBS.md").read_bytes())
    (copy / "harbor" / "WBS.md").unlink()
    symlink(elsewhere / "WBS.md", copy / "harbor" / "WBS.md")
    code, out, err = run_main("--config", str(copy / "gantry.sample.toml"))
    assert code == 0, err
    assert "site harbor is not built: WBS.md for site harbor is outside the configured roots" in err
    assert "orchard.html" in out and "index.html" in out


def test_sites_folder_linked_out_fails_site_build(tmp_path):
    from helpers_config import run_main

    copy = copy_samples(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (copy / "out").mkdir()
    link_dir(elsewhere, copy / "out" / "sites")
    code, out, err = run_main("--config", str(copy / "gantry.sample.toml"), "--site", "harbor")
    assert code == 1 and out == ""
    assert "cannot write " in err and "output_dir is outside the configured roots" in err
    assert list(elsewhere.iterdir()) == []


def test_render_error_names_roots_by_key(tmp_path):
    from gantry.build import render_reason
    from gantry.config import load_config
    from gantry.sites import config_roots

    copy = copy_samples(tmp_path)
    config = load_config(copy / "gantry.sample.toml")
    roots = config_roots(config)
    e = ValueError(f"cannot use {config.output_dir} or {config.devagent_config} or {copy / 'harbor' / 'WBS.md'}")
    assert render_reason(e, roots) == (
        "render error: ValueError: cannot use output_dir or the devagent config or devdoc_dir of harbor"
        + ("\\\\" if os.name == "nt" else "/") + "WBS.md")


# --- Task 6: the per-file readers -----------------------------------------------------


def _unreadable_estimates(result, site):
    return [e.message for e in result.inputs[site].data.report if e.kind == "estimates_unreadable"]


def test_estimates_folder_key_is_unreadable_and_page_builds(tmp_path):
    from gantry.build import build_all
    from gantry.config import load_config

    copy = copy_samples(tmp_path)
    folder = tmp_path / "folder"
    folder.mkdir()
    config = copy / "gantry.sample.toml"
    config.write_bytes(config.read_bytes() + f"\n[site.harbor]\nestimates = {literal(folder)}\n".encode())
    result = build_all(load_config(config))
    assert "harbor" in result.pages
    assert len(_unreadable_estimates(result, "harbor")) == 1


def test_estimates_link_out_is_unreadable_and_page_builds(tmp_path):
    from gantry.build import build_all
    from gantry.config import load_config

    copy = copy_samples(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    name = "estimates-2026-01-15-effort-and-model-tiers.md"
    (elsewhere / name).write_bytes((copy / "harbor" / name).read_bytes())
    (copy / "harbor" / name).unlink()
    symlink(elsewhere / name, copy / "harbor" / name)
    result = build_all(load_config(copy / "gantry.sample.toml"))
    assert "harbor" in result.pages
    (message,) = _unreadable_estimates(result, "harbor")
    assert "outside the configured roots" in message


def test_view_refuses_before_reading_a_template(tmp_path, monkeypatch):
    from gantry import render
    from gantry.views import worksite
    from gantry.wbs_stopgap import parse_wbs

    m = worksite.page_model("alpha", parse_wbs("- [ ] A {issue: Issue-1}\n", "alpha"), default_unit_hours=6)
    render.asset.cache_clear()
    try:
        monkeypatch.setattr(render, "package_roots", lambda: directory_roots([tmp_path]))
        with recording() as record:
            with pytest.raises(OutsideRoots) as e:
                worksite.render_model(m)
            opened = [p for event, p in record if event == "open" and "templates" in p]
        assert str(e.value) == "the gantry package is outside the configured roots"
        assert opened == []
    finally:
        monkeypatch.undo()
        render.asset.cache_clear()


def test_no_module_but_roots_imports_path_tempfile_or_resources():
    """A tripwire only; #60's scan is the gate."""
    package = Path(__file__).resolve().parents[1] / "gantry"
    found = []
    for path in sorted(package.rglob("*.py")):
        if path.name == "roots.py" and path.parent == package:
            continue
        text = path.read_bytes().decode("utf-8")
        for bad in ("from pathlib import Path\r", "from pathlib import Path\n", "from pathlib import Path,",
                    "import tempfile", "importlib", "import shutil", "open("):
            if bad in text:
                found.append((path.name, bad.strip()))
    assert found == []


def test_mkdir_confined_fails_as_path_mkdir_does(tmp_path):
    """Under a file, the error is Path.mkdir's own, so a --site message keeps its text."""
    (tmp_path / "out").write_bytes(b"x")
    with pytest.raises(OSError) as plain:
        (tmp_path / "out" / "sites").mkdir(parents=True, exist_ok=True)
    roots = directory_roots([tmp_path], writable=[tmp_path])
    with pytest.raises(OSError) as confined:
        mkdir_confined(roots, tmp_path / "out" / "sites", key="output_dir")
    assert (type(confined.value), confined.value.errno, confined.value.strerror) == (
        type(plain.value), plain.value.errno, plain.value.strerror)


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
