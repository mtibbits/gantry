"""The gantry CLI: its grammar, exit codes and the sample build (#7)."""

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from gantry.cli import main

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
