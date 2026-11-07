"""The gantry CLI: its grammar, exit codes and the sample build (#7, #11)."""

import json
import os
import shutil
import subprocess
import sys
import tomllib

import pytest

from gantry import stamp
from gantry.cli import main
from gantry.config import load_config
from gantry.requirements import in_trace_sites
from gantry.sites import load_sites
from gantry.views import worksite
from gantry.views.worksite import NO_LEAVES
from helpers_config import add_top_level, append_bytes, copy_samples, demo_build, sample_projects, write_site_tree
from sample_roles import BUSY, NO_WBS, READABLE, UNREADABLE
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


def check_written(stdout_bytes, out_dir):
    """The full build's stdout: one absolute path per written file, the pages in
    config order, then the index."""
    assert b"\r" not in stdout_bytes
    lines = stdout_bytes.decode("utf-8").splitlines()
    pages = [name for name in sample_projects() if name in READABLE]
    assert len(lines) == len(pages) + 1 == 5
    assert all(os.path.isabs(line) for line in lines), lines
    for line, name in zip(lines, pages):
        assert os.path.samefile(line, out_dir / "sites" / f"{name}.html"), (line, name)
    assert os.path.samefile(lines[-1], out_dir / "index.html")


def test_python_m_builds_sample_sites(tmp_path):
    copy = copy_samples(tmp_path)
    result = run_module("build", "--config", str(copy / "gantry.sample.toml"))
    assert result.returncode == 0, result.stderr
    check_written(result.stdout, copy / "out")


@pytest.mark.skipif(not shutil.which("gantry"), reason="gantry is not installed on PATH (CI runs it unconditionally)")
def test_console_script_builds_sample_sites(tmp_path):
    copy = copy_samples(tmp_path)
    result = subprocess.run(
        [shutil.which("gantry"), "build", "--config", str(copy / "gantry.sample.toml")],
        cwd=ROOT,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    check_written(result.stdout, copy / "out")


def test_main_in_process_builds_sample_sites(tmp_path, capsys):
    copy = copy_samples(tmp_path)
    assert main(["build", "--config", str(copy / "gantry.sample.toml")]) == 0
    check_written(capsys.readouterr().out.encode("utf-8"), copy / "out")


def test_write_site_tree_builds_sites(tmp_path, capsys):
    page = (FIXTURES / "page.md").read_bytes()
    empty = (FIXTURES / "empty.md").read_bytes()
    config = write_site_tree(tmp_path, {"alpha": page, "beta": empty})
    assert main(["build", "--config", str(config)]) == 0
    out = tmp_path / "out"
    assert capsys.readouterr().out.splitlines() == [
        str(out / "sites" / "alpha.html"), str(out / "sites" / "beta.html"), str(out / "index.html")]
    assert "14 open leaves" in (out / "index.html").read_bytes().decode("utf-8")
    assert NO_LEAVES in (out / "sites" / "beta.html").read_bytes().decode("utf-8")


def written_bytes(out):
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


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

    monkeypatch.setattr(worksite, "render_model", boom)
    code, out, err = site_build(capsys, config, "alpha")
    assert (code, out) == (1, "")
    assert err == "gantry: error: --site alpha: render error: RuntimeError: forced\n"
    assert page.read_bytes() == before
    assert leftovers(tmp_path) == []

    def boom_quoting(*args, **kwargs):
        raise RuntimeError("bad\ntitle \x1b")

    monkeypatch.setattr(worksite, "render_model", boom_quoting)
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


# In sample mode the page carries no build time, so two builds compare byte for byte.
def test_portfolio_file_does_not_change_page(tmp_path, capsys, monkeypatch):
    copy = copy_samples(tmp_path)
    monkeypatch.setattr(stamp, "sample_dir", lambda package_dir=None: copy)
    config = copy / "gantry.sample.toml"
    assert site_build(capsys, config, BUSY)[0] == 0
    page = copy / "out" / "sites" / f"{BUSY}.html"
    plain = page.read_bytes()
    add_top_level(config, 'portfolio_file = "portfolio.toml"\n')
    assert site_build(capsys, config, BUSY)[0] == 0
    assert page.read_bytes() == plain


def test_site_page_reads_estimates_file(tmp_path, capsys):
    copy = copy_samples(tmp_path)
    config = copy / "gantry.sample.toml"
    name = next((copy / BUSY).glob("estimates-*.md")).name
    assert site_build(capsys, config, BUSY)[0] == 0
    page = (copy / "out" / "sites" / f"{BUSY}.html").read_bytes().decode("utf-8")
    assert name in page
    assert "no estimates file" not in page
    append_bytes(config, f"[site.{BUSY}]\nestimates = false\n")
    assert site_build(capsys, config, BUSY)[0] == 0
    page = (copy / "out" / "sites" / f"{BUSY}.html").read_bytes().decode("utf-8")
    assert name not in page
    assert f"no estimates file: site.{BUSY}.estimates is false" in page


# portfolio_file and fires_list are not read yet; a live requirements key on a
# site that can be in the trace does not change the build or warn (#15).
def test_unread_keys_and_requirements_do_not_change_build(tmp_path, monkeypatch):
    copy = copy_samples(tmp_path)
    config = copy / "gantry.sample.toml"
    code, out, _, plain_err = demo_build(tmp_path, monkeypatch, copy=copy)
    assert code == 0
    plain = written_bytes(out)
    assert len(plain) == 5
    add_top_level(config, 'portfolio_file = "portfolio.toml"\nfires_list = "fires.md"\n')
    append_bytes(config, f'[site.{BUSY}]\nrequirements = "req.md"\n')
    code, out, _, err = demo_build(tmp_path, monkeypatch, copy=copy)
    assert code == 0
    assert written_bytes(out) == plain
    # The not-built sites' lines, and nothing more, in both runs.
    assert err == plain_err
    assert "gantry: warning: site" in err and "trace" not in err


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
    assert sample_line_count(result) == 5
    assert names == {"python"}


@needs_posix_bash
def test_launcher_falls_back_to_python3(tmp_path):
    copy = launcher_copy(tmp_path)
    shims = shim_dir(tmp_path, ["python3"])
    result, names = run_launcher(copy, [SAMPLE_CONFIG], shims, copy, tmp_path)
    assert result.returncode == 0, result.stderr
    assert sample_line_count(result) == 5
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
    assert sample_line_count(result) == 5
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
    assert sample_line_count(result) == 5


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
    assert sample_line_count(result) == 5


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


# --- the cannot-be-in-the-trace warnings (#15) ---------------------------------

UNTRACEABLE = '[site.life]\nrequirements = "l.md"\n[site."bad name"]\nrequirements = "b.md"\n'


def _untraceable_tree(tmp_path, tail=""):
    page = (FIXTURES / "page.md").read_bytes()
    return write_site_tree(tmp_path, {"life": page, "bad name": page, "alpha": page}, tail=tail)


def _trace_warnings(config):
    path = os.path.abspath(config)
    return [
        f"gantry: warning: {path}: key 'site.life.requirements' is not read: site life has the name of "
        "the installation namespace (br_namespace = 'life'), so it cannot be in the trace",
        f"gantry: warning: {path}: key 'site.bad name.requirements' is not read: site bad name's name "
        "does not match [A-Za-z0-9][A-Za-z0-9_.-]*, so it cannot be in the trace",
    ]


NOT_BUILT_BAD_NAME = "gantry: warning: site bad name is not built: name does not match [A-Za-z0-9][A-Za-z0-9_.-]*"


def test_build_warns_for_untraceable_sites(tmp_path, capsys):
    config = _untraceable_tree(tmp_path)
    index = tmp_path / "out" / "index.html"
    assert main(["build", "--config", str(config)]) == 0
    plain = capsys.readouterr()
    assert plain.err.splitlines() == [NOT_BUILT_BAD_NAME]
    assert "cannot be in the trace" not in index.read_bytes().decode("utf-8")
    append_bytes(config, UNTRACEABLE)
    assert main(["build", "--config", str(config)]) == 0
    captured = capsys.readouterr()
    assert captured.out == plain.out
    assert captured.err.splitlines() == _trace_warnings(config) + [NOT_BUILT_BAD_NAME]
    # The index lists both warnings at the top, by key, with no absolute path.
    text = index.read_bytes().decode("utf-8")
    assert text.count("cannot be in the trace") == 4  # each warning's line and its data entry
    assert str(tmp_path) not in text
    c = load_config(config)
    traced, warnings = in_trace_sites(c, load_sites(c)[0])
    assert traced == {}
    assert [w.key for w in warnings] == ["site.life.requirements", "site.bad name.requirements"]


def test_build_site_mode_prints_trace_warnings(tmp_path, capsys):
    config = _untraceable_tree(tmp_path, UNTRACEABLE)
    code, out, err = site_build(capsys, config, "alpha")
    assert code == 0
    assert out.strip().endswith("alpha.html")
    assert err.splitlines() == _trace_warnings(config)


def test_trace_warning_escapes_name(tmp_path, capsys):
    tail = '[site."a\\tb"]\nrequirements = "x.md"\n'
    config = write_site_tree(tmp_path, {"a\tb": (FIXTURES / "page.md").read_bytes()}, tail=tail)
    # The one site cannot be built, so no page is written.
    assert main(["build", "--config", str(config)]) == 1
    err = capsys.readouterr().err
    assert err.splitlines()[1:] == [
        "gantry: warning: site a\\tb is not built: name does not match [A-Za-z0-9][A-Za-z0-9_.-]*",
        "gantry: error: no site page was written",
    ]
    assert "site a\\tb's name does not match" in err.splitlines()[0]
    assert "\t" not in err
