"""A clean clone of committed HEAD and the demo-build child runs (#45).

A plain module, not a test module; it resolves like helpers_config (see
sample_roles's import contract). The clone tests grade committed HEAD, so a
work tree with uncommitted tools/, gantry/ or samples/ changes fails them
instead of grading stale bytes.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from helpers_history import git, shallow_clone
from test_samples import git as plain_git

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "demo_build_runner.py"
CHILD_TIMEOUT = 120  # seconds; a measured run takes about 5
GRADED = ("tools", "gantry", "samples")


def clean_clone(tmp) -> Path:
    """A depth-1 clone of ROOT's HEAD at <tmp>/clone; fails when HEAD is not what the tree holds."""
    if plain_git(ROOT, "cat-file", "-e", "HEAD:tools/demo_build.py").returncode != 0:
        pytest.fail("tools/demo_build.py is not committed at HEAD; commit, then re-run", pytrace=False)
    # Untracked files too: the clone would silently grade a new module or sample as absent.
    status = plain_git(ROOT, "status", "--porcelain", "--untracked-files=all", "--", *GRADED)
    if status.returncode != 0 or status.stdout.strip():
        pytest.fail("commit tools/, gantry/, samples/ first (untracked files there count too)", pytrace=False)
    clone = shallow_clone(ROOT, Path(tmp) / "clone")
    assert git(clone, "rev-list", "--count", "HEAD").strip() == "1"
    return clone


def child_env(pythonpath):
    """os.environ minus PYTHON*; PYTHONPATH the one folder given."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
    env["PYTHONPATH"] = str(pythonpath)
    return env


def run_child(root, day, mode, *, cwd=None):
    """demo_build_runner.py ROOT DAY MODE in a child: the CompletedProcess (bytes)."""
    try:
        return subprocess.run([sys.executable, "-P", "-B", str(RUNNER), str(root), str(day), mode],
                              cwd=cwd or root, env=child_env(root), capture_output=True, timeout=CHILD_TIMEOUT)
    except subprocess.TimeoutExpired:
        pytest.fail(f"the child did not finish in {CHILD_TIMEOUT} s", pytrace=False)


def stderr_of(ran):
    return ran.stderr.decode("utf-8", "replace")
