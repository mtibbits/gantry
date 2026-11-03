"""The Node gate's end-to-end run, shared by the test modules that check it (#53, #9).

A plain module, not a test module, so no test module imports another's gate
helper; it resolves like helpers_config (see sample_roles's import contract).
"""

import os
import subprocess
import sys
import xml.etree.ElementTree as ET

from test_samples import ROOT


def gate_run(tmp_path, ci, module="tests/test_page_checkers.py"):
    """Run `module`, minus its end_to_end tests, with no node on PATH: (exit code, junit cases)."""
    empty = tmp_path / "empty"
    empty.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_ADDOPTS", "CI")}
    env["PATH"] = str(empty)
    if ci is not None:
        env["CI"] = ci
    xml = tmp_path / f"gate-{ci}.xml"
    command = [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", "-k", "not end_to_end",
               "--junitxml", str(xml), module]
    proc = subprocess.run(command, cwd=ROOT, env=env, capture_output=True)
    cases = []
    for case in ET.parse(xml).getroot().iter("testcase"):
        outcome = next(((c.tag, c.get("message") or "") for c in case if c.tag in ("skipped", "failure", "error")),
                       ("passed", ""))
        cases.append(outcome)
    return proc.returncode, cases
