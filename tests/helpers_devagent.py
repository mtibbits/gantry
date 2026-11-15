"""The devagent pin and the GANTRY_DEVAGENT_DIR gate (#31).

A plain module, not a test module; it resolves like helpers_config (see
sample_roles's import contract). `tests/devagent.pin` names one devagent
release tag; the contract test runs devagent's own writers from a checkout of
that tag named by GANTRY_DEVAGENT_DIR. Locally a missing or invalid checkout
skips and a version that differs from the pin warns; under a truthy CI each
fails. Windows always skips: the writers need devagent's own toolchain.
`env` and `os_name` replace the ambient values, so tests never set
os.environ.
"""

import json
import os
import re
import warnings
from pathlib import Path

import pytest

from test_samples import ROOT

PIN = ROOT / "tests" / "devagent.pin"
TAG_RE = re.compile(r"^devagent--v([0-9]+\.[0-9]+\.[0-9]+)$")
VARIABLE = "GANTRY_DEVAGENT_DIR"
TOOLCHAIN = "needs devagent's own toolchain (bash and GNU coreutils)"
NOT_CHECKOUT = f"{VARIABLE} is not a devagent checkout (no .claude-plugin/plugin.json version)"
CI_SUFFIX = " (CI is set, so the devagent contract test fails instead of skipping)"


def read_pin():
    """(tag, version) from tests/devagent.pin; raises on a malformed pin."""
    line = PIN.read_bytes().decode("ascii").rstrip("\n")
    match = TAG_RE.match(line)
    if not match:
        raise ValueError(f"tests/devagent.pin is not a devagent release tag: {line!r}")
    return line, match.group(1)


def reason_unset(tag):
    return f"{VARIABLE} is not set; the devagent contract test needs a devagent checkout at {tag}"


def checkout_version(path):
    """The string `version` in <path>/.claude-plugin/plugin.json, or None."""
    try:
        data = json.loads((Path(path) / ".claude-plugin" / "plugin.json").read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) else None


def devagent_status(env, os_name):
    """(path | None, reason | None, mismatch | None).

    `reason` is why there is no usable checkout (skip or fail); `mismatch` is
    the warning for a checkout at another version. No text holds the path.
    """
    if os_name == "nt":
        return None, TOOLCHAIN, None
    tag, want = read_pin()
    value = env.get(VARIABLE) or ""
    if not value:
        return None, reason_unset(tag), None
    path = Path(value)
    found = checkout_version(path)
    if found is None:
        return None, NOT_CHECKOUT, None
    if found != want:
        return path, None, f"{VARIABLE} holds devagent {found}; tests/devagent.pin names {tag} ({want})"
    return path, None, None


def require_devagent(env=None, os_name=None):
    """The checkout's Path; else skip, warn or fail as the module docstring says."""
    env = os.environ if env is None else env
    os_name = os.name if os_name is None else os_name
    path, reason, mismatch = devagent_status(env, os_name)
    ci = bool(env.get("CI"))
    if reason == TOOLCHAIN:
        pytest.skip(reason)
    if reason is not None:
        if ci:
            pytest.fail(reason + CI_SUFFIX, pytrace=False)
        pytest.skip(reason)
    if mismatch is not None:
        if ci:
            pytest.fail(mismatch + CI_SUFFIX, pytrace=False)
        warnings.warn(mismatch, UserWarning, stacklevel=2)
    return path
