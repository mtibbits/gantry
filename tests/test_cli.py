"""The gantry CLI: its grammar, exit codes and the sample build (#7)."""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from gantry.cli import main
from sample_roles import BUSY, NO_WBS, READABLE, UNREADABLE

ROOT = Path(__file__).resolve().parents[1]


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


def sample_projects():
    return list(tomllib.loads((ROOT / "samples/devagent/config.toml").read_text(encoding="utf-8"))["project"])


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
        env={**os.environ},
    )
    assert result.returncode == 0, result.stderr
    check_sample_lines(result.stdout)


def test_main_in_process_lists_sample_sites(capsys, monkeypatch):
    monkeypatch.chdir(ROOT)
    assert main(["build", "--config", SAMPLE_CONFIG]) == 0
    check_sample_lines(capsys.readouterr().out.encode("utf-8"))


def copy_samples(tmp_path):
    copy = tmp_path / "samples"
    shutil.copytree(ROOT / "samples", copy, ignore=shutil.ignore_patterns("out"))
    return copy


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
    before = config.read_bytes()
    after = before + (
        'portfolio_file = "portfolio.toml"\nfires_list = "fires.md"\n'
        f'[site.{BUSY}]\nrequirements = "req.md"\nestimates = "est.md"\n'
    ).encode("utf-8")
    assert after != before
    config.write_bytes(after)
    assert main(["build", "--config", str(config)]) == 0
    captured = capsys.readouterr()
    assert captured.out == plain
    assert captured.err == ""
    assert len(plain.splitlines()) == 6
