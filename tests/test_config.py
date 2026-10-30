"""The installation-config loader and its path rules (#7).

Every drive-letter, UNC and home-folder fixture below is built at run time
by concatenation, so this source holds none of those forms.
"""

import os
import shutil
import tomllib
from datetime import date
from pathlib import Path

import pytest

from gantry import config as cfg
from gantry.cli import main
from test_samples import GANTRY_SAMPLE, SAMPLES, git, git_work_tree, needs_git

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DEVAGENT = ROOT / "samples" / "devagent" / "config.toml"

BS = "\\"
DRIVE = "C" + ":"

BASE = {
    "config_version": "1",
    "devagent_config": '"devagent.toml"',
    "output_dir": '"out"',
}


def write_config(folder, tail="", name="gantry.toml", **raw):
    """Write a valid installation config from raw TOML literals.

    `None` drops a key; `tail` is appended after the top-level keys (for
    tables). devagent_config points at a copy of the sample devagent config
    beside the file.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SAMPLE_DEVAGENT, folder / "devagent.toml")
    values = {**BASE, **raw}
    lines = [f"{key} = {value}" for key, value in values.items() if value is not None]
    text = "\n".join(lines) + "\n" + tail
    path = folder / name
    path.write_bytes(text.encode("utf-8"))
    return path


def norm(p):
    return os.path.normcase(os.path.normpath(str(p)))


def under(child, parent):
    child, parent = norm(child), norm(parent)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


# --- refusals: each exits 1 naming the file and the key or version ------------


def _missing(tmp):
    return tmp / "nope.toml"


def _directory(tmp):
    d = tmp / "a-folder.toml"
    d.mkdir()
    return d


def _bytes(data):
    def make(tmp):
        p = tmp / "gantry.toml"
        p.write_bytes(data)
        return p
    return make


def _raw(tail="", **raw):
    return lambda tmp: write_config(tmp, tail=tail, **raw)


HARBOR = "\n[site.harbor]\n"

ERROR_CASES = {
    "missing": (_missing, ["cannot read the config"]),
    "unreadable": (_directory, ["cannot read the config"]),
    "invalid-toml": (_bytes(b"config_version = \n"), ["not valid TOML"]),
    "invalid-utf8": (_bytes(b"config_version = 1\nx = \"\xff\"\n"), ["not valid TOML"]),
    "huge-int": (_bytes(b"config_version = " + b"9" * 5000 + b"\n"), ["not valid TOML"]),
    "no-version": (_raw(config_version=None), ["config_version", "config_version 1"]),
    "newer": (_raw(config_version="2"), ["config_version 2", "config_version 1"]),
    "older": (_raw(config_version="0"), ["config_version 0", "config_version 1"]),
    "version-string": (_raw(config_version='"1"'), ["config_version must be an integer"]),
    "version-bool": (_raw(config_version="true"), ["config_version must be an integer"]),
    "unknown-top": (_raw(foo="1"), ["'foo'"]),
    "unknown-site-key": (_raw(tail=HARBOR + "foo = 1\n"), ["'site.harbor.foo'"]),
    "missing-output-dir": (_raw(output_dir=None), ["'output_dir'"]),
    "missing-devagent-config": (_raw(devagent_config=None), ["'devagent_config'"]),
    "as-of-string": (_raw(as_of='"2026-01-20"'), ["'as_of'"]),
    "as-of-datetime": (_raw(as_of="2026-01-20T10:00:00"), ["'as_of'"]),
    "unit-zero": (_raw(default_unit_hours="0"), ["'default_unit_hours'"]),
    "unit-negative": (_raw(default_unit_hours="-1"), ["'default_unit_hours'"]),
    "unit-bool": (_raw(default_unit_hours="true"), ["'default_unit_hours'"]),
    "unit-inf": (_raw(default_unit_hours="inf"), ["'default_unit_hours'"]),
    "estimates-true": (_raw(tail=HARBOR + "estimates = true\n"), ["'site.harbor.estimates'"]),
    "estimates-int": (_raw(tail=HARBOR + "estimates = 3\n"), ["'site.harbor.estimates'"]),
}


@pytest.mark.parametrize("case", sorted(ERROR_CASES))
def test_config_errors_exit_1(tmp_path, capsys, case):
    make, fragments = ERROR_CASES[case]
    p = make(tmp_path)
    assert main(["build", "--config", str(p)]) == 1
    err = capsys.readouterr().err
    assert str(p) in err
    assert [f for f in fragments if f not in err] == [], err


# --- the path predicate --------------------------------------------------------

CLASSIFY_INPUTS = [
    DRIVE + "/x",
    DRIVE + BS + "x",
    DRIVE + "x",
    "/x",
    BS + "x",
    BS * 2 + "host" + BS + "share" + BS + "x",
    "rel/x",
    "../x",
]
CLASSIFY_EXPECTED = {
    "nt": ["absolute", "absolute", "foreign", "foreign", "foreign", "absolute", "relative", "relative"],
    "posix": ["foreign", "foreign", "foreign", "absolute", "foreign", "foreign", "relative", "relative"],
}


@pytest.mark.parametrize("flavour", sorted(CLASSIFY_EXPECTED))
def test_classify_path_both_flavours(flavour):
    got = [cfg.classify_path(raw, flavour) for raw in CLASSIFY_INPUTS]
    assert got == CLASSIFY_EXPECTED[flavour]


def set_home(monkeypatch, home):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


def test_resolve_path_reasons(tmp_path, monkeypatch):
    home = tmp_path / "home"
    set_home(monkeypatch, home)
    with pytest.raises(cfg.PathError, match="drive-relative"):
        cfg.resolve_path(DRIVE + "x", tmp_path, flavour="nt")
    with pytest.raises(cfg.PathError, match="another OS"):
        cfg.resolve_path(DRIVE + "x", tmp_path, flavour="posix")
    for flavour in ("nt", "posix"):
        with pytest.raises(cfg.PathError, match="only a bare ~ is expanded"):
            cfg.resolve_path("~" + "someone" + "/x", tmp_path, flavour=flavour)
    assert norm(cfg.resolve_path("~", tmp_path, flavour=os.name)) == norm(home)


def test_relative_paths_resolve_against_config_dir(tmp_path, monkeypatch):
    folder = tmp_path / "cfg"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    p = write_config(
        folder,
        portfolio_file='"p.toml"',
        tail=HARBOR + 'requirements = "r.md"\n',
    )
    monkeypatch.chdir(cwd)
    c = cfg.load_config(os.path.relpath(p, cwd))
    assert norm(c.output_dir) == norm(folder / "out")
    assert norm(c.portfolio_file) == norm(folder / "p.toml")
    assert norm(c.sites["harbor"].requirements) == norm(folder / "r.md")
    assert norm(c.devagent_config) == norm(folder / "devagent.toml")
    for path in (c.output_dir, c.portfolio_file, c.sites["harbor"].requirements):
        assert not under(path, cwd)


def test_tilde_expands_in_installation_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    set_home(monkeypatch, home)
    p = write_config(tmp_path / "cfg", output_dir='"' + "~" + "/gout" + '"')
    c = cfg.load_config(p)
    assert norm(c.output_dir) == norm(home / "gout")


def test_estimates_false_loads(tmp_path):
    p = write_config(tmp_path, tail=HARBOR + "estimates = false\n")
    c = cfg.load_config(p)
    assert c.sites["harbor"].estimates is False


def test_as_of_absent_is_today(tmp_path):
    p = write_config(tmp_path)
    before = date.today()
    c = cfg.load_config(p)
    after = date.today()
    assert c.as_of in {before, after}
    assert type(c.as_of) is date


def test_as_of_date_loads(tmp_path):
    c = cfg.load_config(write_config(tmp_path, as_of="2026-01-20"))
    assert c.as_of == date(2026, 1, 20)


def test_default_unit_hours_default_and_float(tmp_path):
    assert cfg.load_config(write_config(tmp_path / "a")).default_unit_hours == 6
    assert cfg.load_config(write_config(tmp_path / "b", default_unit_hours="2.5")).default_unit_hours == 2.5


@pytest.mark.skipif(os.name == "nt", reason="a drive-letter path is absolute on Windows")
def test_other_os_output_dir_is_error_on_linux(tmp_path, capsys):
    p = write_config(tmp_path, output_dir='"' + DRIVE + "/" + "out" + '"')
    assert main(["build", "--config", str(p)]) == 1
    err = capsys.readouterr().err
    assert "'output_dir'" in err and str(p) in err


@pytest.mark.skipif(os.name != "nt", reason="a /-rooted path is absolute on Linux")
def test_rooted_output_dir_is_error_on_windows(tmp_path, capsys):
    p = write_config(tmp_path, output_dir='"' + "/" + "srv" + "/out" + '"')
    assert main(["build", "--config", str(p)]) == 1
    err = capsys.readouterr().err
    assert "'output_dir'" in err and str(p) in err


def test_reserved_keys_load_and_resolve(tmp_path):
    folder = tmp_path / "cfg"
    p = write_config(
        folder,
        portfolio_file='"none/portfolio.toml"',
        fires_list='"none/fires.md"',
        tail=HARBOR + 'requirements = "none/req.md"\nestimates = "none/est.md"\n',
    )
    c = cfg.load_config(p)
    got = {
        "portfolio_file": c.portfolio_file,
        "fires_list": c.fires_list,
        "requirements": c.sites["harbor"].requirements,
        "estimates": c.sites["harbor"].estimates,
    }
    for key, path in got.items():
        assert isinstance(path, Path), key
        assert path.is_absolute(), key
        assert under(path, folder), key
        assert not path.exists(), key


# --- the sample installation config (#7) ---------------------------------------


def test_sample_config_loads_inside_samples():
    assert GANTRY_SAMPLE.is_file()
    c = cfg.load_config(GANTRY_SAMPLE)
    samples = os.path.normcase(str(SAMPLES.resolve()))
    paths = [c.devagent_config, c.output_dir, c.portfolio_file, c.fires_list]
    for site in c.sites.values():
        paths += [site.requirements, site.estimates]
    paths = [p for p in paths if isinstance(p, Path)]
    assert len(paths) >= 2
    for p in paths:
        assert Path(os.path.normcase(str(p))).is_relative_to(samples), p
    assert "out" in c.output_dir.parts
    raw = tomllib.loads(GANTRY_SAMPLE.read_bytes().decode("utf-8"))
    assert type(raw["as_of"]) is date


@needs_git
def test_sample_config_not_ignored():
    if not git_work_tree(ROOT):
        pytest.skip("the gantry root is not a git work tree")
    result = git(ROOT, "check-ignore", "-q", "--no-index", "samples/gantry.sample.toml")
    assert result.returncode == 1
