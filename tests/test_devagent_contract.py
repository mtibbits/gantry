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


# --- the fixture tool and the script-written fixtures (Task 2) -----------------------------

import commit_check  # noqa: E402
import devagent_record_fixtures as fx  # noqa: E402

WRITTEN = ROOT / "tests" / "fixtures" / "devagent_records" / "written"


def recipe_files(recipe):
    """The record files a recipe leaves, plus its provenance file."""
    names = {"checklist.md", "provenance.json"}
    if recipe.leaves_stuck:
        names.add("STUCK")
    return names


def test_written_fixtures_match_recipes():
    assert len(fx.RECIPES) == 13
    folders = sorted(p.name for p in WRITTEN.iterdir())
    assert folders == sorted(r.issue for r in fx.RECIPES)
    assert len(folders) == len(fx.RECIPES)
    for recipe in fx.RECIPES:
        got = {p.name for p in (WRITTEN / recipe.issue).iterdir()}
        assert got == recipe_files(recipe), recipe.issue
    issue21 = next(r for r in fx.RECIPES if r.issue == "Issue-21")
    assert recipe_files(issue21) == {"checklist.md", "provenance.json"}


def test_provenance_names_the_pin():
    tag, version = hd.read_pin()
    for recipe in fx.RECIPES:
        data = json.loads((WRITTEN / recipe.issue / "provenance.json").read_bytes().decode("utf-8"))
        assert list(data) == ["pin", "devagent_commit", "devagent_version", "writer", "expected_exit", "steps"]
        assert data["pin"] == tag and data["devagent_version"] == version
        assert re.fullmatch(r"[0-9a-f]{40}", data["devagent_commit"])
        assert data["writer"] == recipe.writer and data["expected_exit"] == recipe.expected_exit
        assert data["writer"].endswith(".sh") and data["writer"] in [step[0] for step in data["steps"]]


STUCK_LOCAL = (b"Step:        2 draft\nReason:      A crane waits.\nLast good:   step 0 pull\n"
               b"Created:     2026-11-15 14:41\n")
STUCK_ISO = (b"Step:        2 draft\nReason:      A crane waits.\nCreated:     not the key\n"
             b"Last good:   0 pull\nCreated:     2026-11-15T14:41:25+00:00\n")
CHECKLIST = (b"# Issue-6 \xe2\x80\x94 Workflow checklist\n\nTemplate: standard\n"
             b"Created: 2026-11-15T14:41:25+00:00\nActive revision: 1\n\n## Revision 1\n\n"
             b"- [x]  0. pull\n\n## Log\n- 2026-11-15 14:41  draft: stuck \xe2\x80\x94 A crane.\n"
             b"- 2026-11-15 14:42  pull: done\n")


def test_fix_timestamps_fixed_and_mask():
    assert fx.fix_timestamps("STUCK", STUCK_LOCAL) == STUCK_LOCAL.replace(b"2026-11-15 14:41", b"2026-01-12 09:30")
    assert fx.fix_timestamps("STUCK", STUCK_LOCAL, token=b"<TS>").endswith(b"Created:     <TS>\n")
    fixed = fx.fix_timestamps("STUCK", STUCK_ISO)
    assert fixed.endswith(b"Created:     2026-01-12T09:30:00+00:00\n")
    assert b"Created:     not the key\n" in fixed
    assert fx.fix_timestamps("STUCK", STUCK_ISO, token=b"<TS>").endswith(b"Created:     <TS>\n")
    got = fx.fix_timestamps("checklist.md", CHECKLIST)
    assert b"Created: 2026-01-12T09:30:00+00:00\n" in got
    assert b"- 2026-01-12 09:30  draft: stuck" in got and b"- 2026-01-12 09:30  pull: done" in got
    assert b"2026-11-15" not in got
    masked = fx.fix_timestamps("checklist.md", CHECKLIST, token=b"<TS>")
    assert b"Created: <TS>\n" in masked and masked.count(b"- <TS>  ") == 2
    # A STUCK whose last line is not a Created: line of either form is left as written.
    odd = STUCK_LOCAL.replace(b"2026-11-15 14:41", b"soon")
    assert fx.fix_timestamps("STUCK", odd) == odd


def test_fixtures_hold_no_local_path():
    files = [p for p in WRITTEN.rglob("*") if p.is_file()]
    assert len(files) >= 2 * len(fx.RECIPES)
    for path in files:
        assert commit_check.path_form_hits(path.read_bytes().decode("utf-8")) == [], path
