"""The gantry CLI: its grammar, exit codes and the sample build (#7)."""

import json
import os
import shutil
import subprocess
import sys
import tomllib

import pytest

from gantry.cli import main
from sample_roles import BUSY, NO_WBS, READABLE, UNREADABLE
from test_samples import ROOT
from test_sites import append_bytes, copy_samples, sample_projects


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


def expected_status(name):
    if name in READABLE | {UNREADABLE}:
        return "WBS.md found"
    if name == NO_WBS:
        return "cannot read: no WBS.md in the planning directory"
    raise AssertionError(f"no role for sample site {name!r}")


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
