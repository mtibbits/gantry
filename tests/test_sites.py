"""The site list: read only from devagent's config.toml (#7).

Sample site names come from sample_roles and from the parsed sample config,
never as literals. Fixture-only names are the local constants below.
"""

import os
from pathlib import Path

import pytest

from gantry import config as cfg
from gantry import sites as st
from gantry.cli import main
from helpers_config import append_bytes, copy_samples, sample_projects, write_config
from sample_roles import NO_WBS, READABLE, UNREADABLE
from test_samples import GANTRY_SAMPLE, SAMPLES

ZETA, ALPHA, FERRY, NODIR = "zeta", "alpha", "ferry", "nodir"
BAD_NAME = "bad name"


def fixture_config(tmp_path, devagent_text, gantry_tail=""):
    """A gantry config beside a devagent config holding `devagent_text`."""
    p = write_config(tmp_path, tail=gantry_tail)
    (tmp_path / "devagent.toml").write_bytes(devagent_text.encode("utf-8"))
    return p


def site_list(config_path):
    return st.load_sites(cfg.load_config(config_path))


def test_sites_are_parsed_project_keys_in_order(tmp_path):
    sites, warnings = site_list(GANTRY_SAMPLE)
    assert [s.name for s in sites] == sample_projects()
    assert warnings == []
    devagent = (
        f"[project.{ZETA}.permissions]\nallow = 1\n"
        f'[project.{ALPHA}]\ndevdoc_dir = "a"\n'
        f'[project.{ZETA}]\ndevdoc_dir = "z"\n'
    )
    sites, _ = site_list(fixture_config(tmp_path, devagent))
    assert [s.name for s in sites] == [ZETA, ALPHA]


def test_subtables_are_fields_not_sites(tmp_path):
    devagent = (
        f'[project.{ALPHA}]\ndevdoc_dir = "a"\n'
        f"[project.{ALPHA}.permissions]\nallow = 1\n"
        f'[project.{ALPHA}.step_models]\ndraft = "x"\n'
        f'[project.{ALPHA}.issue_source]\nbackend = "github"\n'
    )
    sites, _ = site_list(fixture_config(tmp_path, devagent))
    assert [s.name for s in sites] == [ALPHA]
    sample_sites, _ = site_list(GANTRY_SAMPLE)
    assert len(sample_sites) == len(sample_projects()) == 6


def test_added_project_appears_without_gantry_config_change(tmp_path):
    copy = copy_samples(tmp_path)
    gantry_config = copy / GANTRY_SAMPLE.name
    gantry_before = gantry_config.read_bytes()
    append_bytes(copy / "devagent" / "config.toml", f'\n[project.{FERRY}]\ndevdoc_dir = "../{sorted(READABLE)[0]}"\n')
    sites, _ = site_list(gantry_config)
    assert gantry_config.read_bytes() == gantry_before
    assert sites[-1].name == FERRY
    assert sites[-1].problem is None
    assert [s.name for s in sites] == sample_projects() + [FERRY]


def test_problem_sites_listed_run_exits_0(tmp_path, capsys):
    copy = copy_samples(tmp_path)
    gantry_config = copy / GANTRY_SAMPLE.name
    append_bytes(
        copy / "devagent" / "config.toml",
        f'\n[project.{NODIR}]\nx = 1\n\n[project."{BAD_NAME}"]\ndevdoc_dir = "../{sorted(READABLE)[0]}"\n',
    )
    sites, _ = site_list(gantry_config)
    problems = {s.name: s.problem for s in sites}
    assert problems[NODIR] == "no devdoc_dir"
    assert problems[BAD_NAME] == "name does not match [A-Za-z0-9][A-Za-z0-9_.-]*"
    capsys.readouterr()
    assert main(["build", "--config", str(gantry_config)]) == 0
    lines = capsys.readouterr().out.splitlines()
    fields = {line.split("\t")[0]: line.split("\t") for line in lines}
    assert fields[NODIR][2].startswith("cannot read: ")
    assert fields[BAD_NAME][2].startswith("cannot read: ")
    assert len(lines) == len(sample_projects()) + 2


def test_control_character_names_print_escaped(tmp_path, capsys):
    copy = copy_samples(tmp_path)
    gantry_config = copy / GANTRY_SAMPLE.name
    # TOML escapes inside quoted keys: a tab, a newline, an ESC, and (under
    # [project]) a non-table site whose problem message would name it.
    append_bytes(
        copy / "devagent" / "config.toml",
        '\n[project."a\\tb"]\nx = 1\n'
        '[project."c\\nd"]\nx = 1\n'
        '[project."e\\u001bf"]\nx = 1\n'
        '[project]\n"g\\u001bh" = 1\n',
    )
    sites, _ = site_list(gantry_config)
    assert [s.name for s in sites][-4:] == ["a\tb", "c\nd", "e\x1bf", "g\x1bh"]
    capsys.readouterr()
    assert main(["build", "--config", str(gantry_config)]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out
    lines = out.split("\n")
    assert lines.pop() == ""
    assert len(lines) == len(sites)
    fields = [line.split("\t") for line in lines]
    assert all(len(f) == 3 for f in fields), fields
    assert [f[0] for f in fields] == sample_projects() + ["a\\tb", "c\\nd", "e\\x1bf", "g\\x1bh"]
    for name, _, status in fields[-4:]:
        assert status == "cannot read: name does not match [A-Za-z0-9][A-Za-z0-9_.-]*", (name, status)


def test_unmatched_site_table_warns(tmp_path, capsys):
    copy = copy_samples(tmp_path)
    gantry_config = copy / GANTRY_SAMPLE.name
    append_bytes(gantry_config, f"\n[site.{FERRY}]\n")
    config = cfg.load_config(gantry_config)
    sites, warnings = st.load_sites(config)
    dfile = config.devagent_config
    assert warnings == [st.SiteWarning(f"site.{FERRY}", f"{config.path}: [site.{FERRY}] names no project in {dfile}")]
    assert len(sites) == len(sample_projects())
    capsys.readouterr()
    assert main(["build", "--config", str(gantry_config)]) == 0
    captured = capsys.readouterr()
    assert "gantry: warning:" in captured.err
    assert f"site.{FERRY}" in captured.err
    assert len(captured.out.splitlines()) == len(sample_projects())


def test_sample_site_problems():
    sites, _ = site_list(GANTRY_SAMPLE)
    by_name = {s.name: s for s in sites}
    for name in sorted(READABLE | {UNREADABLE}):
        assert by_name[name].problem is None, name
    assert by_name[NO_WBS].problem == "no WBS.md in the planning directory"
    for s in sites:
        assert os.path.samefile(s.planning_dir, SAMPLES / s.name), s.name


def test_problem_checks_order_and_stat(tmp_path):
    (tmp_path / "file-not-dir").write_bytes(b"x\n")
    devagent = (
        '[project]\nstringy = "not a table"\n'
        "[project.numdir]\ndevdoc_dir = 3\n"
        '[project.gone]\ndevdoc_dir = "missing-folder"\n'
        '[project.filedir]\ndevdoc_dir = "file-not-dir"\n'
    )
    sites, _ = site_list(fixture_config(tmp_path, devagent))
    problems = {s.name: s.problem for s in sites}
    assert problems == {
        "stringy": "[project.stringy] is not a table",
        "numdir": "devdoc_dir is not a string",
        "gone": "planning directory does not exist",
        "filedir": "planning directory is not a directory",
    }
    by_name = {s.name: s for s in sites}
    assert by_name["gone"].planning_dir == Path(os.path.normpath(tmp_path / "missing-folder"))
    assert by_name["stringy"].planning_dir is None
    assert by_name["numdir"].planning_dir is None


def test_wbs_folder_is_not_found(tmp_path):
    (tmp_path / "plans" / "WBS.md").mkdir(parents=True)
    sites, _ = site_list(fixture_config(tmp_path, '[project.p]\ndevdoc_dir = "plans"\n'))
    assert sites[0].problem == "WBS.md is not a file"


def test_nul_devdoc_dir_is_a_site_problem_not_a_crash(tmp_path, capsys):
    devagent = (
        '[project.nul]\ndevdoc_dir = "a\\u0000b"\n'
        f'[project.{ALPHA}]\ndevdoc_dir = "plans"\n'
    )
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "WBS.md").write_bytes(b"x\n")
    p = fixture_config(tmp_path, devagent)
    sites, _ = site_list(p)
    assert sites[0].problem.startswith("cannot check the planning directory: ")
    assert sites[1].problem is None
    capsys.readouterr()
    assert main(["build", "--config", str(p)]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 2


@pytest.mark.parametrize("target,error,expected", [
    ("plans", PermissionError(13, "Permission denied"), "cannot check the planning directory: Permission denied"),
    ("plans", ValueError("path too long"), "cannot check the planning directory: path too long"),
    ("WBS.md", PermissionError(13, "Permission denied"), "cannot check WBS.md: Permission denied"),
    ("WBS.md", ValueError("path too long"), "cannot check WBS.md: path too long"),
])
def test_cannot_check_is_not_absent(tmp_path, monkeypatch, target, error, expected):
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "WBS.md").write_bytes(b"x\n")
    p = fixture_config(tmp_path, '[project.p]\ndevdoc_dir = "plans"\n')
    config = cfg.load_config(p)
    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        if Path(path).name == target:
            raise error
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(st.os, "stat", fake_stat)
    sites, _ = st.load_sites(config)
    assert sites[0].problem == expected
