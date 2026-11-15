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


# --- the contract test and the CI fetch (Task 8) -------------------------------------------

import shutil  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"

# Every devagent source line docs/devagent-formats.md cites, at the pin:
# (path, first line, last line, a text that must still sit in that range).
CITES = (
    ("scripts/lib/config.sh", 446, 451, '"${devdoc%/}" "$issue"'),
    ("scripts/stuck.sh", 41, 41, "local cur cur_name last_good last_good_name"),
    ("scripts/stuck.sh", 55, 55, 'if [[ -n "$last_good" ]]; then'),
    ("scripts/stuck.sh", 59, 59, 'checklist_mark "$checklist" "$cur" "!"'),
    ("scripts/stuck.sh", 61, 69, "date '+%Y-%m-%d %H:%M'"),
    ("scripts/checklist-stuck.sh", 66, 70, "'!'"),
    ("scripts/checklist-stuck.sh", 72, 77, "Created:     $(date -Iseconds)"),
    ("scripts/unstuck.sh", 66, 66, 'rm -f "$issue_dir/STUCK"'),
    ("scripts/checklist-unstuck.sh", 42, 42, 'rm -f "$issue_dir/STUCK"'),
    ("scripts/cleanup.sh", 271, 271, "add -A -- ."),
    ("scripts/lib/checklist.sh", 68, 68,
     r"'^- \[(.)\][[:space:]]+([0-9]+)\.[[:space:]]+([A-Za-z][A-Za-z0-9_-]*)'"),
    ("scripts/lib/checklist.sh", 77, 79, "/^## Revision / { last = NR }"),
    ("scripts/lib/checklist.sh", 133, 155, '"$glyph" != "x" && "$glyph" != "-"'),
    ("scripts/lib/checklist.sh", 309, 314, "' '|x|-|'!'|'~'|'?'|P) return 0 ;;"),
    ("scripts/lib/checklist.sh", 365, 382, "if (ln[i] > blk)"),
    ("scripts/park.sh", 63, 63, 'checklist_mark "$checklist" "$cur" "P"'),
    ("scripts/park.sh", 68, 68, 'state_add_parked "$project" "$issue"'),
    ("scripts/resume.sh", 53, 56, 'checklist_find_glyph_line "$checklist" P'),
    ("scripts/lib/log.sh", 17, 19, "message must be a single line"),
    ("scripts/lib/log.sh", 56, 56, "chmod --reference="),
    ("scripts/lib/wbs-tracker.py", 149, 149, 'PROGRESS_GLYPHS = {"x", "~"}'),
    ("scripts/lib/wbs-tracker.py", 178, 178, "if any(g in PROGRESS_GLYPHS for g in glyphs):"),
    ("scripts/status.sh", 24, 47, 'state_get "$project" active_issue'),
    ("templates/imPlan_template.md", 1, 2, "(context:/model:)"),
    ("templates/imPlan_template.md", 30, 33, "## Tasks"),
)


def committed_record_files():
    return sorted(p for p in WRITTEN.rglob("*") if p.is_file() and p.name in fx.RECORD_FILES)


def test_fixtures_regenerate_byte_for_byte():
    checkout = hd.require_devagent()
    diffs = fx.check(checkout, WRITTEN)
    assert list(diffs) == []
    assert diffs.compared == len(committed_record_files()) == 18


def test_changed_fixture_fails_naming_fixture_script_and_line(tmp_path):
    kept, made = tmp_path / "kept", tmp_path / "made"
    shutil.copytree(WRITTEN, kept)
    shutil.copytree(WRITTEN, made)
    assert fx.compare(kept, made) == []
    target = kept / "Issue-12" / "checklist.md"
    data = target.read_bytes()
    changed = data.replace(b"- [P]  2. draft", b"- [ ]  2. draft")
    assert changed != data
    target.write_bytes(changed)
    diffs = fx.compare(kept, made)
    line = data.split(b"\n").index(b"- [P]  2. draft") + 1
    assert diffs == [f"Issue-12/checklist.md: written by park.sh at devagent 3.0.0: first differing line {line}: "
                     f"fixture '- [ ]  2. draft', devagent '- [P]  2. draft'"]
    assert diffs.compared == len(committed_record_files())
    # A timestamp is masked on both sides: changing only its digits is no difference.
    target.write_bytes(data)
    stuck = kept / "Issue-14" / "STUCK"
    before = stuck.read_bytes()
    after = before.replace(b"2026-01-12T09:30:00+00:00", b"2026-01-13T19:31:07+00:00")
    assert after != before
    stuck.write_bytes(after)
    assert fx.compare(kept, made) == []
    # A file only one side holds is named, either way.
    (made / "Issue-13" / "STUCK").unlink()
    (kept / "Issue-77").mkdir()
    (kept / "Issue-77" / "STUCK").write_bytes(b"Step:        2 draft\n")
    assert fx.compare(kept, made) == [
        "Issue-13/STUCK: not written by stuck.sh at devagent 3.0.0",
        "Issue-77/STUCK: committed but written by no recipe",
    ]


def test_plan_template_lists_tasks_under_tasks_heading():
    checkout = hd.require_devagent()
    lines = (checkout / "templates" / "imPlan_template.md").read_bytes().decode("utf-8").split("\n")
    assert "context:/model:" in lines[0] + lines[1]
    start = lines.index("## Tasks")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    from gantry.devagent_records import CHECKBOX_RE

    assert [line for line in lines[start + 1:end] if CHECKBOX_RE.fullmatch(line)]


def test_cited_devagent_lines_hold():
    checkout = hd.require_devagent()
    assert len(CITES) >= 20
    for path, first, last, needle in CITES:
        lines = (checkout / path).read_bytes().decode("utf-8").split("\n")
        assert any(needle in line for line in lines[first - 1:last]), (path, first, last, needle)


def workflow_steps(text):
    """The workflow's lines, stripped, YAML comment lines dropped."""
    return [line.strip() for line in text.split("\n") if line.strip() and not line.strip().startswith("#")]


def fetch_order_problems(text):
    """Why the workflow does not fetch devagent at the pin before pytest, or []."""
    lines = workflow_steps(text)
    problems = []
    try:
        pytest_at = lines.index("- run: python -m pytest -q -rs")
    except ValueError:
        return ["no pytest step"]
    clone = [i for i, line in enumerate(lines) if line.startswith("git clone ") and "tests/devagent.pin" in line]
    env = [i for i, line in enumerate(lines) if "GANTRY_DEVAGENT_DIR=" in line and "$GITHUB_ENV" in line]
    if not clone or clone[0] > pytest_at:
        problems.append("no git clone of the pinned tag before pytest")
    if not env or env[0] > pytest_at:
        problems.append("GANTRY_DEVAGENT_DIR is not written to $GITHUB_ENV before pytest")
    if any("devagent--v" in line for line in lines):
        problems.append("a literal devagent tag in the workflow")
    return problems


def test_workflow_fetches_devagent_before_pytest():
    text = WORKFLOW.read_bytes().decode("utf-8")
    assert fetch_order_problems(text) == []
    commit = json.loads((WRITTEN / "Issue-6" / "provenance.json").read_bytes().decode("utf-8"))["devagent_commit"]
    assert "provenance.json" in text and commit not in text
    # Planted control: the clone line gone, its comment kept.
    lines = text.split("\n")
    kept = [line for line in lines if not line.strip().startswith("git clone ")]
    assert len(kept) == len(lines) - 1
    planted = "\n".join(kept)
    assert "tests/devagent.pin" in planted
    assert fetch_order_problems(planted) == ["no git clone of the pinned tag before pytest"]


def test_every_fixture_names_the_same_devagent_commit():
    commits = {json.loads((WRITTEN / r.issue / "provenance.json").read_bytes().decode("utf-8"))["devagent_commit"]
               for r in fx.RECIPES}
    assert len(commits) == 1


# --- the doc's cites (Task 10) -------------------------------------------------------------

DOC = ROOT / "docs" / "devagent-formats.md"
DEVAGENT_CITE_RE = re.compile(r"(?<![\w/.-])((?:scripts|templates)/[\w./-]+?):([0-9]+)(?:-([0-9]+))?(?![\w-])")


def doc_cites(text):
    """{(path, first, last)} for every devagent `path:line` or `path:first-last` cite."""
    return {(m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))) for m in DEVAGENT_CITE_RE.finditer(text)}


def test_doc_names_the_pin_and_every_cite():
    text = DOC.read_bytes().decode("utf-8")
    tag, _ = hd.read_pin()
    assert tag in text
    for path, first, last, _ in CITES:
        cite = f"{path}:{first}" if first == last else f"{path}:{first}-{last}"
        assert cite in text, cite


def test_doc_cites_equal_cites_table():
    found = doc_cites(DOC.read_bytes().decode("utf-8"))
    table = {(path, first, last) for path, first, last, _ in CITES}
    assert len(found) >= 20
    assert found == table, (sorted(found - table), sorted(table - found))
    # The parser sees a cite in prose and ignores a gantry path that holds templates/.
    assert doc_cites("see `scripts/park.sh:63` and gantry/templates/page.html:4") == {("scripts/park.sh", 63, 63)}


# --- final review fixes ----------------------------------------------------------------------


class _Seen(Exception):
    pass


def test_regenerate_resolves_a_relative_checkout(tmp_path, monkeypatch):
    """A relative --devagent or GANTRY_DEVAGENT_DIR names the checkout from the
    caller's folder, not from the temp folder the writers run in."""
    _, version = hd.read_pin()
    fake_checkout(tmp_path / "da", version)
    monkeypatch.chdir(tmp_path)
    seen = []

    def stop(devagent, work):
        seen.append(devagent)
        raise _Seen()

    monkeypatch.setattr(fx, "preflight", stop)
    with pytest.raises(_Seen):
        fx.regenerate("da", tmp_path / "out", commit="0" * 40)
    assert seen == [(tmp_path / "da").resolve()]
    assert seen[0].is_absolute()


def test_main_resolves_a_relative_checkout(tmp_path, monkeypatch):
    _, version = hd.read_pin()
    fake_checkout(tmp_path / "da", version)
    monkeypatch.chdir(tmp_path)
    seen = []

    def check(devagent, out):
        seen.append(devagent)
        return fx.Differences()

    monkeypatch.setattr(fx, "check", check)
    assert fx.main(["--devagent", "da", "--check"]) == 0
    assert [str(p) for p in seen] == [str((tmp_path / "da").resolve())]
