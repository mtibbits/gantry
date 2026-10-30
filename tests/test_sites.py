"""The site list: read only from devagent's config.toml (#7).

Sample site names come from sample_roles and from the parsed sample config,
never as literals. Fixture-only names are the local constants below.
"""

import os
import shutil
import tomllib
from pathlib import Path

from gantry import config as cfg
from gantry import sites as st
from gantry.cli import main
from sample_roles import NO_WBS, READABLE, UNREADABLE
from test_samples import GANTRY_SAMPLE, SAMPLES

ZETA, ALPHA, FERRY, NODIR = "zeta", "alpha", "ferry", "nodir"
BAD_NAME = "bad name"


def sample_projects():
    return list(tomllib.loads(SAMPLES.joinpath("devagent", "config.toml").read_text(encoding="utf-8"))["project"])


def copy_samples(tmp_path):
    copy = tmp_path / "samples"
    shutil.copytree(SAMPLES, copy, ignore=shutil.ignore_patterns("out"))
    return copy


def append_bytes(path, text):
    before = path.read_bytes()
    after = before + text.encode("utf-8")
    assert after != before
    path.write_bytes(after)


def fixture_config(tmp_path, devagent_text, gantry_tail=""):
    """A gantry config beside a devagent config holding `devagent_text`."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "devagent.toml").write_bytes(devagent_text.encode("utf-8"))
    p = tmp_path / "gantry.toml"
    text = 'config_version = 1\ndevagent_config = "devagent.toml"\noutput_dir = "out"\n' + gantry_tail
    p.write_bytes(text.encode("utf-8"))
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
