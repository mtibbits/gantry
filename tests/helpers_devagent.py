"""The devagent pins and their checkout gate (#31, #39).

A plain module, not a test module; it resolves like helpers_config (see
sample_roles's import contract). Each pin file under tests/ names one devagent
release tag, and one variable names a checkout of that tag (PINS):

- floor: `tests/devagent.pin`, the oldest devagent Gantry supports, whose
  checkout GANTRY_DEVAGENT_DIR names. The contract test runs devagent's own
  writers from it; every tool reads this pin, and every committed fixture is
  written at it.
- latest: `tests/devagent-latest.pin`, the newest devagent contract Gantry
  mirrors, whose checkout GANTRY_DEVAGENT_LATEST_DIR names: for tests of a
  contract the floor lacks.

Either variable satisfies its own pin only. Locally a missing or invalid
checkout skips and a version that differs from the pin warns; under a truthy
CI each fails. Windows always skips: the writers need devagent's own
toolchain. `env` and `os_name` replace the ambient values, so tests never set
os.environ. `pin` is "floor" wherever it is not given.
"""

import os
import re
import warnings
from pathlib import Path

import pytest

from devagent_record_fixtures import checkout_version
from devagent_record_fixtures import read_pin as _pin_line

TAG_RE = re.compile(r"^devagent--v([0-9]+\.[0-9]+\.[0-9]+)$")
# pin name: (its file under tests/, the variable that names its checkout).
PINS = {"floor": ("devagent.pin", "GANTRY_DEVAGENT_DIR"),
        "latest": ("devagent-latest.pin", "GANTRY_DEVAGENT_LATEST_DIR")}
TOOLCHAIN = "needs devagent's own toolchain (bash and GNU coreutils)"
CI_SUFFIX = " (CI is set, so the devagent contract test fails instead of skipping)"
UNSET_TEXT = "{variable} is not set; the devagent contract test needs a devagent checkout at {tag}"
NOT_CHECKOUT_TEXT = "{variable} is not a devagent checkout (no .claude-plugin/plugin.json version)"
MISMATCH_TEXT = "{variable} holds devagent {found}; tests/{file} names {tag} ({want})"
# The floor pin's names, as #31 gave them.
VARIABLE = PINS["floor"][1]
NOT_CHECKOUT = NOT_CHECKOUT_TEXT.format(variable=VARIABLE)


def read_pin(pin="floor"):
    """(tag, version) from the pin's file under tests/; raises on a malformed pin."""
    file, _ = PINS[pin]
    # The floor's file is the tools' own PIN; the latest sits beside this module.
    line = _pin_line() if pin == "floor" else _pin_line(Path(__file__).resolve().parent / file)
    match = TAG_RE.match(line)
    if not match:
        raise ValueError(f"tests/{file} is not a devagent release tag: {line!r}")
    return line, match.group(1)


def reason_unset(tag, pin="floor"):
    return UNSET_TEXT.format(variable=PINS[pin][1], tag=tag)


def devagent_status(env, os_name, pin="floor"):
    """(path | None, reason | None, mismatch | None) for the pin's checkout.

    `reason` is why there is no usable checkout (skip or fail); `mismatch` is
    the warning for a checkout at another version. No text holds the path.
    """
    if os_name == "nt":
        return None, TOOLCHAIN, None
    file, variable = PINS[pin]
    tag, want = read_pin(pin)
    value = env.get(variable) or ""
    if not value:
        return None, reason_unset(tag, pin), None
    path = Path(value)
    found = checkout_version(path)
    if found is None:
        return None, NOT_CHECKOUT_TEXT.format(variable=variable), None
    if found != want:
        return path, None, MISMATCH_TEXT.format(variable=variable, found=found, file=file, tag=tag, want=want)
    return path, None, None


def require_devagent(env=None, os_name=None, *, pin="floor"):
    """The pin's checkout as a Path; else skip, warn or fail as the module docstring says."""
    env = os.environ if env is None else env
    os_name = os.name if os_name is None else os_name
    path, reason, mismatch = devagent_status(env, os_name, pin)
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
