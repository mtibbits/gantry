"""The devagent contract: the pin, the checkout gate, the fixture tool (#31).

The gate tests run on every leg with fake checkouts in tmp_path. The
regeneration test needs a devagent checkout at the pin (GANTRY_DEVAGENT_DIR)
and devagent's own toolchain, so it skips on Windows and locally without the
variable, and fails under CI without it.
"""

import json
import re

import pytest

import helpers_devagent as hd
from test_samples import ROOT

TAG_RE = re.compile(r"^devagent--v([0-9]+\.[0-9]+\.[0-9]+)$")


def fake_checkout(path, version):
    (path / ".claude-plugin").mkdir(parents=True)
    (path / ".claude-plugin" / "plugin.json").write_bytes(
        json.dumps({"name": "devagent", "version": version}).encode("utf-8"))
    return path


# --- the pin and the gate (Task 1) ---------------------------------------------------------


def test_pin_is_one_release_tag():
    data = (ROOT / "tests" / "devagent.pin").read_bytes()
    assert data.endswith(b"\n") and data.count(b"\n") == 1 and b"\r" not in data
    line = data.decode("ascii").rstrip("\n")
    assert TAG_RE.match(line), line
    tag, version = hd.read_pin()
    assert tag == line and version == TAG_RE.match(line).group(1)


def test_gate_skips_unset_locally_and_fails_under_ci():
    tag, _ = hd.read_pin()
    want = hd.reason_unset(tag)
    assert want == f"GANTRY_DEVAGENT_DIR is not set; the devagent contract test needs a devagent checkout at {tag}"
    with pytest.raises(pytest.skip.Exception) as info:
        hd.require_devagent(env={}, os_name="posix")
    assert str(info.value.msg) == want
    with pytest.raises(pytest.fail.Exception) as info:
        hd.require_devagent(env={"CI": "true"}, os_name="posix")
    assert str(info.value.msg) == want + hd.CI_SUFFIX
    assert hd.CI_SUFFIX == " (CI is set, so the devagent contract test fails instead of skipping)"
    with pytest.raises(pytest.skip.Exception):
        hd.require_devagent(env={"GANTRY_DEVAGENT_DIR": ""}, os_name="posix")


def test_gate_invalid_checkout(tmp_path):
    want = "GANTRY_DEVAGENT_DIR is not a devagent checkout (no .claude-plugin/plugin.json version)"
    env = {"GANTRY_DEVAGENT_DIR": str(tmp_path)}
    with pytest.raises(pytest.skip.Exception) as info:
        hd.require_devagent(env=env, os_name="posix")
    assert str(info.value.msg) == want
    with pytest.raises(pytest.fail.Exception) as info:
        hd.require_devagent(env={**env, "CI": "1"}, os_name="posix")
    assert str(info.value.msg) == want + hd.CI_SUFFIX
    assert str(tmp_path) not in want
    # A plugin.json whose version is not a string is no checkout either.
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_bytes(b'{"version": 3}')
    with pytest.raises(pytest.skip.Exception) as info:
        hd.require_devagent(env=env, os_name="posix")
    assert str(info.value.msg) == want


def test_gate_version_mismatch_warns_locally_fails_under_ci(tmp_path):
    tag, version = hd.read_pin()
    checkout = fake_checkout(tmp_path / "da", "9.9.9")
    env = {"GANTRY_DEVAGENT_DIR": str(checkout)}
    with pytest.warns(UserWarning) as caught:
        got = hd.require_devagent(env=env, os_name="posix")
    assert got == checkout
    message = str(caught[0].message)
    with pytest.raises(pytest.fail.Exception) as info:
        hd.require_devagent(env={**env, "CI": "true"}, os_name="posix")
    for text in (message, str(info.value.msg)):
        assert "9.9.9" in text and tag in text and f"({version})" in text
        assert str(checkout) not in text
    assert message == f"GANTRY_DEVAGENT_DIR holds devagent 9.9.9; tests/devagent.pin names {tag} ({version})"


def test_gate_windows_skips_with_toolchain_reason(tmp_path):
    _, version = hd.read_pin()
    checkout = fake_checkout(tmp_path / "da", version)
    env = {"GANTRY_DEVAGENT_DIR": str(checkout), "CI": "true"}
    with pytest.raises(pytest.skip.Exception) as info:
        hd.require_devagent(env=env, os_name="nt")
    assert str(info.value.msg) == "needs devagent's own toolchain (bash and GNU coreutils)"


def test_gate_accepts_matching_checkout(tmp_path, recwarn):
    _, version = hd.read_pin()
    checkout = fake_checkout(tmp_path / "da", version)
    got = hd.require_devagent(env={"GANTRY_DEVAGENT_DIR": str(checkout), "CI": "true"}, os_name="posix")
    assert got == checkout
    assert len(recwarn) == 0
    assert hd.devagent_status({"GANTRY_DEVAGENT_DIR": str(checkout)}, "posix") == (checkout, None, None)
