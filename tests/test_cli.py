"""The gantry CLI: its grammar, exit codes and the sample build (#7)."""

import json
import os
import shutil
import subprocess
import sys
import tomllib

import pytest

from gantry.cli import main, status_text
from gantry.views import worksite
from gantry.views.worksite import NO_LEAVES
from gantry.wbs_stopgap import parse_wbs
from helpers_config import append_bytes, copy_samples, sample_projects, write_site_tree
from sample_roles import BUSY, NO_WBS, OWN_UNIT, SMALL, UNESTIMATED, UNREADABLE
from test_samples import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "wbs"


def run_module(*args, cwd=ROOT):
    return subprocess.run(
        [sys.executable, "-m", "gantry", *args],
        cwd=cwd,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )


# --- grammar -----------------------------------------------------------------


def test_missing_config_is_usage_error():
    result = run_module("build")
    assert result.returncode == 2
    assert b"--config" in result.stderr


def test_missing_config_in_process(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["build"])
    assert exc.value.code == 2
    assert "--config" in capsys.readouterr().err


def test_no_subcommand_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_versions_agree():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == "0.0.0"
    assert plugin["version"] == "0.0.0"
    assert pyproject["project"]["name"] == "gantry"
    assert plugin["name"] == "gantry"


# --- build end to end on the sample ------------------------------------------

SAMPLE_CONFIG = "samples/gantry.sample.toml"


# Hand-counted from samples/*/WBS.md: leaf counts, then each reported kind.
EXPECTED_STATUS = {
    BUSY: "open 8, finished 6",
    OWN_UNIT: "open 6, finished 3; no_issue 3, unresolved_ref 1",
    UNESTIMATED: "open 3, finished 3; unresolved_ref 1",
    SMALL: "open 2, finished 3",
    UNREADABLE: "cannot read: WBS.md is not valid UTF-8 at byte 88",
    NO_WBS: "cannot read: no WBS.md in the planning directory",
}


def expected_status(name):
    if name not in EXPECTED_STATUS:
        raise AssertionError(f"no role for sample site {name!r}")
    return EXPECTED_STATUS[name]


def check_sample_lines(stdout_bytes):
    assert b"\r" not in stdout_bytes
    lines = stdout_bytes.decode("utf-8").splitlines()
    projects = sample_projects()
    assert len(lines) == len(projects) == 6
    fields = [line.split("\t") for line in lines]
    assert all(len(f) == 3 for f in fields), fields
    assert [f[0] for f in fields] == projects
    for name, where, status in fields:
        assert status == expected_status(name), (name, status)
        assert os.path.samefile(where, ROOT / "samples" / name)


def test_python_m_lists_sample_sites():
    result = run_module("build", "--config", SAMPLE_CONFIG)
    assert result.returncode == 0, result.stderr
    check_sample_lines(result.stdout)


@pytest.mark.skipif(not shutil.which("gantry"), reason="gantry is not installed on PATH (CI runs it unconditionally)")
def test_console_script_lists_sample_sites():
    result = subprocess.run(
        [shutil.which("gantry"), "build", "--config", SAMPLE_CONFIG],
        cwd=ROOT,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    check_sample_lines(result.stdout)


def test_main_in_process_lists_sample_sites(capsys, monkeypatch):
    monkeypatch.chdir(ROOT)
    assert main(["build", "--config", SAMPLE_CONFIG]) == 0
    check_sample_lines(capsys.readouterr().out.encode("utf-8"))


def test_status_text_forms():
    ids = (ROOT / "tests" / "fixtures" / "wbs" / "ids.md").read_bytes().decode("utf-8")
    assert status_text(parse_wbs(ids, "alpha")) == (
        "open 7, finished 0; no_issue 2, duplicate_issue 1, key_collision 1, "
        "unresolved_ref 2, bad_priority 2, skipped_line 2"
    )
    assert status_text(parse_wbs("- [ ] One {issue: Issue-1}\n", "alpha")) == "open 1, finished 0"


def test_write_site_tree_lists_sites(tmp_path, capsys):
    page = (FIXTURES / "page.md").read_bytes()
    empty = (FIXTURES / "empty.md").read_bytes()
    config = write_site_tree(tmp_path, {"alpha": page, "beta": empty})
    assert main(["build", "--config", str(config)]) == 0
    lines = [line.split("\t") for line in capsys.readouterr().out.splitlines()]
    assert [f[0] for f in lines] == ["alpha", "beta"]
    assert lines[0][2] == "open 14, finished 1; no_issue 2, unresolved_ref 1"
    assert lines[1][2] == "open 0, finished 0; no_nodes 1"


def snapshot(root):
    return {
        p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
    }


def test_build_writes_nothing(tmp_path, capsys):
    copy = copy_samples(tmp_path)
    before = snapshot(tmp_path)
    assert main(["build", "--config", str(copy / "gantry.sample.toml")]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 6
    assert snapshot(tmp_path) == before
    assert not (copy / "out").exists()


# --- build --site ---------------------------------------------------------------


def site_build(capsys, config, name):
    """(exit code, stdout, stderr) of `build --site name`."""
    code = main(["build", "--config", str(config), "--site", name])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def leftovers(folder):
    return sorted(p.name for p in folder.rglob("*.tmp"))


def test_site_unreadable_writes_no_page(tmp_path, capsys):
    copy = copy_samples(tmp_path)
    config = copy / "gantry.sample.toml"
    for name, reason in ((UNREADABLE, "WBS.md is not valid UTF-8 at byte 88"),
                         (NO_WBS, "no WBS.md in the planning directory")):
        code, out, err = site_build(capsys, config, name)
        assert (code, out) == (1, "")
        assert err == f"gantry: error: --site {name}: cannot build: {reason}\n"
        assert not (copy / "out").exists()


def test_site_no_nodes_writes_page(tmp_path, capsys):
    config = write_site_tree(tmp_path, {"alpha": (FIXTURES / "empty.md").read_bytes()})
    code, out, err = site_build(capsys, config, "alpha")
    page = tmp_path / "out" / "sites" / "alpha.html"
    assert (code, out, err) == (0, f"{page}\n", "")
    assert NO_LEAVES in page.read_bytes().decode("utf-8")


def test_site_unconfigured_exits_1(tmp_path, capsys):
    config = write_site_tree(tmp_path, {"alpha": (FIXTURES / "page.md").read_bytes()})
    code, out, err = site_build(capsys, config, "nowhere")
    assert (code, out) == (1, "")
    assert err == f"gantry: error: --site nowhere: no such site in {tmp_path / 'devagent.toml'}\n"
    assert not (tmp_path / "out").exists()


def test_site_invalid_name_exits_1(tmp_path, capsys):
    config = write_site_tree(tmp_path, {"bad\tname": (FIXTURES / "page.md").read_bytes()})
    code, out, err = site_build(capsys, config, "bad\tname")
    assert (code, out) == (1, "")
    assert err == "gantry: error: --site bad\\tname: cannot build: name does not match [A-Za-z0-9][A-Za-z0-9_.-]*\n"
    assert not (tmp_path / "out").exists()


def test_site_creates_sites_folder(tmp_path, capsys):
    config = write_site_tree(tmp_path, {"alpha": (FIXTURES / "page.md").read_bytes()}, output_dir='"deep/out"')
    assert not (tmp_path / "deep").exists()
    code, out, _ = site_build(capsys, config, "alpha")
    page = tmp_path / "deep" / "out" / "sites" / "alpha.html"
    assert (code, out) == (0, f"{page}\n")
    assert page.read_bytes().startswith(b"<!doctype html>\n")
    assert leftovers(tmp_path) == []


def test_site_output_unwritable(tmp_path, capsys):
    config = write_site_tree(tmp_path, {"alpha": (FIXTURES / "page.md").read_bytes()})
    (tmp_path / "out").write_bytes(b"a file, not a folder")
    code, out, err = site_build(capsys, config, "alpha")
    assert (code, out) == (1, "")
    assert err.startswith(f"gantry: error: --site alpha: cannot write {tmp_path / 'out' / 'sites' / 'alpha.html'}: ")
    (tmp_path / "out").unlink()
    (tmp_path / "out" / "sites" / "alpha.html").mkdir(parents=True)
    code, out, err = site_build(capsys, config, "alpha")
    assert (code, out) == (1, "")
    assert err.startswith("gantry: error: --site alpha: cannot write ")
    assert leftovers(tmp_path) == []
    assert (tmp_path / "out" / "sites" / "alpha.html").is_dir()


def test_site_render_error_leaves_no_page(tmp_path, capsys, monkeypatch):
    config = write_site_tree(tmp_path, {"alpha": (FIXTURES / "page.md").read_bytes()})
    assert site_build(capsys, config, "alpha")[0] == 0
    page = tmp_path / "out" / "sites" / "alpha.html"
    before = page.read_bytes()

    def boom(*args, **kwargs):
        raise RuntimeError("forced")

    monkeypatch.setattr(worksite, "render_page", boom)
    code, out, err = site_build(capsys, config, "alpha")
    assert (code, out) == (1, "")
    assert err == "gantry: error: --site alpha: render error: RuntimeError: forced\n"
    assert page.read_bytes() == before
    assert leftovers(tmp_path) == []

    def boom_quoting(*args, **kwargs):
        raise RuntimeError("bad\ntitle \x1b")

    monkeypatch.setattr(worksite, "render_page", boom_quoting)
    code, out, err = site_build(capsys, config, "alpha")
    assert (code, out) == (1, "")
    assert err.startswith("gantry: error: --site alpha: render error: RuntimeError: bad")
    assert err.count("\n") == 1 and err.endswith("\n") and "\x1b" not in err
    assert page.read_bytes() == before


def test_python_m_builds_sample_site(tmp_path):
    copy = copy_samples(tmp_path)
    result = run_module("build", "--config", str(copy / "gantry.sample.toml"), "--site", BUSY)
    assert result.returncode == 0, result.stderr
    printed = result.stdout.decode("utf-8").strip()
    assert os.path.isabs(printed)
    assert os.path.samefile(printed, copy / "out" / "sites" / f"{BUSY}.html")


def test_listing_does_not_import_the_view():
    code = "import sys, gantry.cli; gantry.cli.main(['build', '--config', 'samples/gantry.sample.toml']); " \
           "sys.exit('gantry.views.worksite' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                            env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.splitlines()) == 6


def test_portfolio_file_does_not_change_page(tmp_path, capsys):
    copy = copy_samples(tmp_path)
    config = copy / "gantry.sample.toml"
    assert site_build(capsys, config, BUSY)[0] == 0
    page = copy / "out" / "sites" / f"{BUSY}.html"
    plain = page.read_bytes()
    append_bytes(config, 'portfolio_file = "portfolio.toml"\n')
    assert site_build(capsys, config, BUSY)[0] == 0
    assert page.read_bytes() == plain


def test_reserved_keys_have_no_effect_on_build(tmp_path, capsys):
    copy = copy_samples(tmp_path)
    config = copy / "gantry.sample.toml"
    assert main(["build", "--config", str(config)]) == 0
    plain = capsys.readouterr().out
    append_bytes(
        config,
        'portfolio_file = "portfolio.toml"\nfires_list = "fires.md"\n'
        f'[site.{BUSY}]\nrequirements = "req.md"\nestimates = "est.md"\n',
    )
    assert main(["build", "--config", str(config)]) == 0
    captured = capsys.readouterr()
    assert captured.out == plain
    assert captured.err == ""
    assert len(plain.splitlines()) == 6


# --- the plugin launcher (tools/gantry-build.sh) -----------------------------
# POSIX bash only: Windows is checked by hand (Task 10). The launcher uses
# bash builtins only, so PATH can hold nothing but stand-in pythons ("shims").

BASH = shutil.which("bash")
needs_posix_bash = pytest.mark.skipif(
    os.name == "nt" or not BASH, reason="Windows is checked by hand (Task 10)"
)


def launcher_copy(tmp_path):
    copy = tmp_path / "repo"
    skip = shutil.ignore_patterns("out", "__pycache__")
    for part in ("gantry", "tools", "samples"):
        shutil.copytree(ROOT / part, copy / part, ignore=skip)
    return copy


def shim_dir(tmp_path, names, body=None):
    """A folder of builtins-only stand-ins that log their name and run this Python."""
    folder = tmp_path / "shims"
    folder.mkdir(exist_ok=True)
    assert "'" not in sys.executable
    if body is None:
        body = "exec '" + sys.executable + "' \"$@\"\n"
    for name in names:
        shim = folder / name
        shim.write_bytes(("#!/bin/sh\nprintf '%s\\n' \"${0##*/}\" >> \"$MARK\"\n" + body).encode("utf-8"))
        shim.chmod(0o755)
    return folder


def run_launcher(copy, args, shims, cwd, tmp_path):
    mark = tmp_path / "mark"
    result = subprocess.run(
        [BASH, str(copy / "tools" / "gantry-build.sh"), *args],
        cwd=cwd,
        capture_output=True,
        env={**os.environ, "PATH": str(shims), "MARK": str(mark)},
    )
    names = set(mark.read_text().split()) if mark.exists() else set()
    return result, names


def sample_line_count(result):
    return len(result.stdout.decode("utf-8").splitlines())


@needs_posix_bash
def test_launcher_prefers_python(tmp_path):
    copy = launcher_copy(tmp_path)
    shims = shim_dir(tmp_path, ["python", "python3"])
    result, names = run_launcher(copy, [SAMPLE_CONFIG], shims, copy, tmp_path)
    assert result.returncode == 0, result.stderr
    assert sample_line_count(result) == 6
    assert names == {"python"}


@needs_posix_bash
def test_launcher_falls_back_to_python3(tmp_path):
    copy = launcher_copy(tmp_path)
    shims = shim_dir(tmp_path, ["python3"])
    result, names = run_launcher(copy, [SAMPLE_CONFIG], shims, copy, tmp_path)
    assert result.returncode == 0, result.stderr
    assert sample_line_count(result) == 6
    assert names == {"python3"}


@needs_posix_bash
def test_launcher_no_python(tmp_path):
    copy = launcher_copy(tmp_path)
    shims = shim_dir(tmp_path, [])
    result, _ = run_launcher(copy, [SAMPLE_CONFIG], shims, copy, tmp_path)
    assert result.returncode == 127
    assert b"python3" in result.stderr


@needs_posix_bash
def test_launcher_reports_failed_version_probe(tmp_path):
    copy = launcher_copy(tmp_path)
    shims = shim_dir(tmp_path, ["python"], body="echo 'not really python'\nexit 9\n")
    result, _ = run_launcher(copy, [SAMPLE_CONFIG], shims, copy, tmp_path)
    assert result.returncode == 1
    assert b"3.11 or later" in result.stderr
    assert b"not really python" in result.stderr


@needs_posix_bash
def test_launcher_one_literal_argument(tmp_path):
    copy = launcher_copy(tmp_path)
    name = "gantry sample $(touch pwned) 'q'.toml"
    (copy / "samples" / "gantry.sample.toml").rename(copy / "samples" / name)
    shims = shim_dir(tmp_path, ["python"])
    result, _ = run_launcher(copy, ["samples/" + name], shims, copy, tmp_path)
    assert result.returncode == 0, result.stderr
    assert sample_line_count(result) == 6
    assert list(tmp_path.rglob("pwned")) == []


@needs_posix_bash
def test_launcher_P_ignores_cwd_package(tmp_path):
    copy = launcher_copy(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "gantry").mkdir(parents=True)
    (elsewhere / "gantry" / "__init__.py").write_bytes(b'raise SystemExit("decoy gantry imported")\n')
    shims = shim_dir(tmp_path, ["python"])
    config = copy / "samples" / "gantry.sample.toml"
    result, _ = run_launcher(copy, [str(config)], shims, elsewhere, tmp_path)
    assert b"decoy" not in result.stderr
    assert result.returncode == 0, result.stderr
    assert sample_line_count(result) == 6


@needs_posix_bash
def test_launcher_no_arg_is_usage_error(tmp_path):
    copy = launcher_copy(tmp_path)
    shims = shim_dir(tmp_path, ["python", "python3"])
    result, _ = run_launcher(copy, [], shims, copy, tmp_path)
    assert result.returncode == 2
    assert b"--config" in result.stderr
    result, _ = run_launcher(copy, ["a.toml", "b.toml"], shims, copy, tmp_path)
    assert result.returncode == 2
    assert b"usage:" in result.stderr
    result, _ = run_launcher(copy, [""], shims, copy, tmp_path)
    assert result.returncode == 2
    assert b"--config" in result.stderr


@needs_posix_bash
def test_launcher_dash_leading_config_is_a_value(tmp_path):
    copy = launcher_copy(tmp_path)
    samples = copy / "samples"
    (samples / "gantry.sample.toml").rename(samples / "-dash.toml")
    shims = shim_dir(tmp_path, ["python"])
    result, _ = run_launcher(copy, ["-dash.toml"], shims, samples, tmp_path)
    assert result.returncode == 0, result.stderr
    assert sample_line_count(result) == 6


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes only")
def test_site_page_mode_follows_umask(tmp_path, capsys):
    config = write_site_tree(tmp_path, {"alpha": (FIXTURES / "page.md").read_bytes()})
    old = os.umask(0o022)
    try:
        assert site_build(capsys, config, "alpha")[0] == 0
    finally:
        os.umask(old)
    page = tmp_path / "out" / "sites" / "alpha.html"
    assert page.stat().st_mode & 0o777 == 0o644
