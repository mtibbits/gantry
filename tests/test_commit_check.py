"""tools/commit_check.py: the commit check against private data (#44).

Every path form is assembled at run time ("~" + "/", "/" + "home" + "/"), so
this file holds none of them and passes the check it tests.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import commit_check
from test_samples import PATH_FORM_CONTROLS, ROOT, _decode, git, needs_git

TOOL = ROOT / "tools" / "commit_check.py"

# The tool's own environment in a temp repo: no GIT_* leaks in, and no global
# or system config (test_samples.git(..., hermetic=True) does the same).
HERMETIC_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
HERMETIC_ENV.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
IDENTITY = ("-c", "user.name=Test", "-c", "user.email=test@example.invalid")
TEMPLATE_FILES = (
    ".gitignore",
    "samples/gantry.sample.toml",
    "samples/devagent/config.toml",
    "tools/commit_check.py",
)


def run_git(repo, *args, input=None):
    result = git(repo, *IDENTITY, *args, input=input, hermetic=True)
    assert result.returncode == 0, (args, _decode(result.stderr))
    return _decode(result.stdout)


def stage(repo, rel, data):
    """Write bytes at rel (LF untouched) and force-add them."""
    path = Path(repo) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    run_git(repo, "add", "-f", "--", rel)


def commit(repo, msg="change"):
    run_git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return run_git(repo, "rev-parse", "HEAD").strip()


def tool(repo, *args, env=None):
    result = subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=repo, env=env or HERMETIC_ENV, capture_output=True,
    )
    return result.returncode, result.stdout.decode("utf-8"), result.stderr.decode("utf-8")


def finding_lines(out):
    return [l for l in out.splitlines() if l and not l.startswith(("commit-check:", "ci:", "::"))]


@pytest.fixture(scope="module")
def template_repo(tmp_path_factory):
    repo = tmp_path_factory.mktemp("template") / "repo"
    repo.mkdir()
    run_git(repo, "init", "-q", "-b", "main")
    for rel in TEMPLATE_FILES:
        stage(repo, rel, (ROOT / rel).read_bytes().replace(b"\r\n", b"\n"))
    commit(repo, "initial")
    return repo


@pytest.fixture
def repo(template_repo, tmp_path):
    copy = tmp_path / "repo"
    shutil.copytree(template_repo, copy)
    return copy


# --- the path forms (Task 1) ----------------------------------------------------

SHAPES = {
    "alone": "{}",
    "double": '"{}"',
    "single": "'{}'",
    "paren": "({})",
    "eq": "a={}",
    "eq_spaced": "a = {}",
}

NEGATIVE_CONTROLS = [
    # test_samples' negatives (#42)
    "https://github.com/sample_owner/harbor",
    "../harbor",
    "samples/devagent/out/",
    "samples/c/x",
    "origin/main",
    "and/or",
    "- [~] Erect crane one {issue: Issue-6}",
    "est: ?w",
    "6-12",
    "2026-01-15",
    "P1:",
    # the issue's pass cases
    "https://x",
    "a:b",
    "/gantry:build",
    "~" + "1/3",
    "~" + "5/day",
    "https://host/" + "r/x",
    "https://host/" + "~" + "user/x",
]


@pytest.mark.parametrize("shape", sorted(SHAPES))
@pytest.mark.parametrize("form,sample", PATH_FORM_CONTROLS)
def test_forms_flag_every_control_in_every_shape(form, sample, shape):
    text = SHAPES[shape].format(sample)
    hits = commit_check.path_form_hits(text)
    assert form in {f for f, _ in hits}, (text, hits)


@pytest.mark.parametrize("text", NEGATIVE_CONTROLS)
def test_forms_pass_negative_controls(text):
    assert commit_check.path_form_hits(text) == []


def test_form_names_unchanged():
    assert set(commit_check.PATH_FORMS) == {
        "drive_backslash", "drive_slash", "unc", "posix_home", "msys_drive", "tilde",
    }


def test_tool_source_holds_no_form():
    assert commit_check.path_form_hits(TOOL.read_bytes().decode("utf-8")) == []


def test_line_hits_counts_newlines_only():
    text = "a\x0cb\n" + "~" + "/x\n"
    hits = commit_check.line_hits(text)
    assert [(lineno, form) for lineno, form, _, _ in hits] == [(2, "tilde")]
    _, _, start, end = hits[0]
    assert text.split("\n")[1][start:end] == "~" + "/"


# --- the exemption list (Task 2) --------------------------------------------------

HOME = "/" + "home" + "/" + "someone" + "/"
TILDE = "~" + "/"


def test_exempt_parse_good_and_comments():
    text = (
        "# a comment\r\n"
        "\n"
        "   \n"
        "docs/x.md | absolute-path | see " + HOME + "notes | the example the page explains\r\n"
    )
    entries, errors = commit_check.parse_exemptions(text)
    assert errors == []
    assert entries == [commit_check.Exemption(
        "docs/x.md", "absolute-path", "see " + HOME + "notes",
        "the example the page explains", 4,
    )]


@pytest.mark.parametrize("line", [
    "docs/x.md | absolute-path | " + HOME,
    "docs/x.md | absolute-path | " + HOME + " | ",
    "docs/x.md | absolute-path |  | a reason",
    "docs/x.md | generated-output | " + HOME + " | a reason",
    "docs/x.md | no-such-rule | " + HOME + " | a reason",
    commit_check.EXEMPT_PATH + " | absolute-path | " + HOME + " | a reason",
])
def test_exempt_parse_rejects(line):
    entries, errors = commit_check.parse_exemptions("# header\n" + line + "\n")
    assert entries == []
    assert [lineno for lineno, _ in errors] == [2]


def test_exempt_is_span_level():
    text = "see " + HOME + "notes"
    entry = commit_check.Exemption("docs/x.md", "absolute-path", text, "why", 1)
    line = "a " + text + " and " + TILDE + "x"
    hits = commit_check.line_hits(line)
    by_form = {form: (start, end) for _, form, start, end in hits}
    assert set(by_form) == {"posix_home", "tilde"}
    assert commit_check.is_exempt("docs/x.md", line, *by_form["posix_home"], [entry])
    assert not commit_check.is_exempt("docs/x.md", line, *by_form["tilde"], [entry])
    assert not commit_check.is_exempt("docs/y.md", line, *by_form["posix_home"], [entry])


def test_entry_liveness():
    entry = commit_check.Exemption("docs/x.md", "absolute-path", "see " + HOME, "why", 1)
    assert commit_check.entry_is_live(entry, "one\nwe see " + HOME + "x\n")
    assert not commit_check.entry_is_live(entry, "one\nnothing here\n")
    # The text is present but no longer holds a form: the path moved out of it.
    moved = commit_check.Exemption("docs/x.md", "absolute-path", "see", "why", 1)
    assert not commit_check.entry_is_live(moved, "we see it, and " + HOME + "\n")


# --- rule 4, the ignore rules (Task 3) --------------------------------------------

PROBES = (
    "samples/out/commit-check-probe",
    "samples/history/out/commit-check-probe",
    "samples/devagent/out/commit-check-probe",
)


def gitignore_without(repo, line):
    lines = (repo / ".gitignore").read_text(encoding="utf-8").split("\n")
    assert line in lines
    return "\n".join(l for l in lines if l != line)


@needs_git
def test_ignore_rule_fails_without_out(repo):
    stage(repo, ".gitignore", gitignore_without(repo, "out/"))
    commit(repo)
    rc, out, err = tool(repo, "tree")
    assert rc == 1, (out, err)
    for probe in PROBES:
        assert f".gitignore: ignore-rules ({probe} is not ignored)" in out.splitlines()


@needs_git
def test_ignore_rule_fails_when_sample_ignored(repo):
    stage(repo, ".gitignore", (repo / ".gitignore").read_text(encoding="utf-8") + "samples/gantry.sample.toml\n")
    commit(repo)
    rc, out, err = tool(repo, "tree")
    assert rc == 1, (out, err)
    lines = out.splitlines()
    assert ".gitignore: ignore-rules (samples/gantry.sample.toml is ignored but must not be)" in lines


@needs_git
def test_ignore_rule_passes_on_repo_gitignore(repo):
    rc, out, err = tool(repo, "tree")
    assert (rc, finding_lines(out)) == (0, []), (out, err)
    assert out.splitlines()[-1] == "commit-check: clean"
    probes = commit_check.ignore_probes(repo, env=HERMETIC_ENV)
    assert {p: probes[p] for p in PROBES} == dict.fromkeys(PROBES, True)


@needs_git
def test_ignore_rule_negation_record_reads_as_not_ignored(repo):
    # A '!' pattern matching the probe itself: check-ignore reports a '!' record.
    stage(repo, ".gitignore", gitignore_without(repo, "out/") + "\n*probe\n!commit-check-probe\n")
    commit(repo)
    rc, out, err = tool(repo, "tree")
    assert rc == 1, (out, err)
    assert f".gitignore: ignore-rules ({PROBES[0]} is not ignored)" in out.splitlines()


@needs_git
def test_ignore_rule_directory_negation_empty_record(repo):
    # A directory negation leaves the probe with an empty record: not ignored.
    stage(repo, ".gitignore", (repo / ".gitignore").read_text(encoding="utf-8") + "!samples/history/out/\n")
    commit(repo)
    rc, out, err = tool(repo, "tree")
    assert rc == 1, (out, err)
    assert f".gitignore: ignore-rules ({PROBES[1]} is not ignored)" in out.splitlines()


@needs_git
@pytest.mark.parametrize("direction", ["hides_missing_out", "falsely_ignores_sample"])
def test_ignore_rule_ignores_info_exclude(repo, direction):
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    if direction == "hides_missing_out":
        stage(repo, ".gitignore", gitignore_without(repo, "out/"))
        commit(repo)
        exclude.write_bytes(b"out/\n")
        expected = 1
    else:
        exclude.write_bytes(b"*.toml\n")
        expected = 0
    rc, out, err = tool(repo, "tree")
    assert rc == expected, (out, err)


@needs_git
def test_outside_a_repo_exits_2(tmp_path):
    rc, out, err = tool(tmp_path, "tree")
    assert rc == 2, (out, err)


# --- staged: rules 1 and 2 (Task 4) -----------------------------------------------

CONFIG_SHAPE = 'config_version = 1\noutput_dir = "out"\n'


def stage_all(repo, cases):
    """stage() for many files with one git add (each git call is slow on Windows)."""
    for rel, data in cases.items():
        path = Path(repo) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    run_git(repo, "add", "-f", "--", *cases)


@needs_git
def test_staged_flags_generated_and_config(repo):
    expected = {
        "site/index.html": "generated-output (html page)",
        "week-plan.json": "generated-output (generated file name)",
        "build.json": "generated-output (generated file name)",
        "tests/fixtures/x/update.json": "generated-output (generated file name)",
        "samples/harbor/out/page.txt": "generated-output (under an out/ folder)",
        "conf/gantry.toml": "installation-config (outside samples/)",
        "tests/fixtures/leak/gantry.toml":
            "installation-config (fixture value not self-contained: output_dir)",
        "samples/x.local.toml": "installation-config (*.local.toml)",
    }
    stage_all(repo, {
        "site/index.html": "<p>page</p>\n",
        "week-plan.json": "{}\n",
        "build.json": "{}\n",
        "tests/fixtures/x/update.json": "{}\n",
        "samples/harbor/out/page.txt": "page\n",
        "conf/gantry.toml": CONFIG_SHAPE,
        "tests/fixtures/leak/gantry.toml": 'config_version = 1\noutput_dir = "../out"\n',
        "samples/x.local.toml": "a = 1\n",
    })
    rc, out, err = tool(repo, "staged")
    assert rc == 1, (out, err)
    lines = out.splitlines()
    for path, finding in expected.items():
        assert f"{path}: {finding}" in lines, (path, out)


@needs_git
def test_staged_passes_clean_cases(repo):
    stage_all(repo, {
        "gantry/templates/page.html": "<p>template</p>\n",
        "tests/page_script/known_bad/bad.html": "<p>bad</p>\n",
        "samples/other.toml": CONFIG_SHAPE + 'devagent_config = "devagent/config.toml"\n',
        "tests/fixtures/ok/gantry.toml": (
            'config_version = 1\ndevagent_config = "devagent.toml"\noutput_dir = "out"\n'
            '[project.harbor.issue_source]\nbackend = "github"\n'
        ),
        "samples/harbor/out.md": "a file named out, not a folder\n",
        "tests/fixtures/edge/gantry.toml": (
            'config_version = 1\noutput_dir = ""\nhere = "."\nurl = "https://example.invalid/x"\n'
        ),
    })
    rc, out, err = tool(repo, "staged")
    assert (rc, finding_lines(out)) == (0, []), (out, err)


@needs_git
def test_rule2_fixture_dotdot_fails(repo):
    stage(repo, "tests/fixtures/dd/gantry.toml", 'config_version = 1\noutput_dir = "out"\n[a]\nb = ["../x"]\n')
    rc, out, err = tool(repo, "staged")
    assert rc == 1, (out, err)
    assert ("tests/fixtures/dd/gantry.toml: installation-config "
            "(fixture value not self-contained: a.b[0])") in out.splitlines()


# --- staged: rule 3 and the index (Task 5) ----------------------------------------

# The AC names a user's home folder under both roots, on top of the controls.
FORM_CASES = PATH_FORM_CONTROLS + [
    ("posix_home", "/" + "home" + "/" + "someone" + "/"),
    ("posix_home", "/" + "Users" + "/" + "someone" + "/"),
]


@needs_git
def test_staged_flags_every_form_in_every_shape(repo):
    files = {}
    for i, (form, sample) in enumerate(FORM_CASES):
        for shape in sorted(SHAPES):
            files[f"forms/{form}-{i}-{shape}.txt"] = (form, sample, SHAPES[shape].format(sample))
    stage_all(repo, {rel: text + "\n" for rel, (_, _, text) in files.items()})
    rc, out, err = tool(repo, "staged")
    assert rc == 1, (out, err)
    lines = out.splitlines()
    for rel, (form, sample, _) in files.items():
        assert f"{rel}:1: absolute-path ({form})" in lines, rel
        assert sample not in out, sample


@needs_git
def test_staged_utf16_bom_file_flagged(repo):
    stage(repo, "notes/ps.txt", b"\xff\xfe" + ("x = " + HOME + "\r\n").encode("utf-16-le"))
    rc, out, err = tool(repo, "staged")
    assert rc == 1, (out, err)
    assert "notes/ps.txt:1: absolute-path (posix_home)" in out.splitlines()


@needs_git
def test_staged_nul_file_skipped(repo):
    stage(repo, "notes/blob.bin", b"\x00\x01" + HOME.encode("ascii"))
    rc, out, err = tool(repo, "staged")
    assert (rc, finding_lines(out)) == (0, []), (out, err)


@needs_git
def test_staged_passes_negative_controls(repo):
    stage_all(repo, {f"neg/{i}.txt": text + "\n" for i, text in enumerate(NEGATIVE_CONTROLS)})
    rc, out, err = tool(repo, "staged")
    assert (rc, finding_lines(out)) == (0, []), (out, err)


@needs_git
def test_staged_reads_index_not_worktree(repo):
    stage(repo, "notes/a.md", "see " + HOME + "\n")
    (repo / "notes" / "a.md").write_bytes(b"clean now\n")
    rc, out, err = tool(repo, "staged")
    assert rc == 1, (out, err)
    assert "notes/a.md:1: absolute-path (posix_home)" in out.splitlines()

    stage(repo, "notes/a.md", "clean\n")
    (repo / "notes" / "a.md").write_bytes(("see " + HOME + "\n").encode("ascii"))
    rc, out, err = tool(repo, "staged")
    assert (rc, finding_lines(out)) == (0, []), (out, err)


@needs_git
def test_staged_honours_temp_index(repo, tmp_path):
    # git commit -a and git commit <path> hand the hook a temporary index.
    env = dict(HERMETIC_ENV, GIT_INDEX_FILE=str(tmp_path / "second-index"))
    for args in (("read-tree", "HEAD"),):
        assert subprocess.run(["git", *args], cwd=repo, env=env).returncode == 0
    (repo / "notes").mkdir()
    (repo / "notes" / "a.md").write_bytes(("see " + HOME + "\n").encode("ascii"))
    assert subprocess.run(["git", "add", "notes/a.md"], cwd=repo, env=env).returncode == 0
    rc, out, err = tool(repo, "staged")
    assert (rc, finding_lines(out)) == (0, []), (out, err)
    rc, out, err = tool(repo, "staged", env=env)
    assert rc == 1, (out, err)
    assert "notes/a.md:1: absolute-path (posix_home)" in out.splitlines()


@needs_git
def test_staged_uses_index_exemptions(repo):
    entry = f"notes/a.md | absolute-path | see {HOME} | the note explains the form\n"
    stage(repo, "notes/a.md", "see " + HOME + "\n")
    exempt = repo / commit_check.EXEMPT_PATH
    exempt.parent.mkdir(parents=True, exist_ok=True)
    exempt.write_bytes(entry.encode("utf-8"))
    rc, out, err = tool(repo, "staged")
    assert rc == 1, ("work-tree entry must not count", out, err)
    run_git(repo, "add", "--", commit_check.EXEMPT_PATH)
    rc, out, err = tool(repo, "staged")
    assert (rc, finding_lines(out)) == (0, []), (out, err)


@needs_git
def test_staged_on_unborn_head(tmp_path):
    run_git(tmp_path, "init", "-q", "-b", "main")
    stage(tmp_path, "site/index.html", "<p>page</p>\n")
    rc, out, err = tool(tmp_path, "staged")
    assert rc == 1, (out, err)
    assert "site/index.html: generated-output (html page)" in out.splitlines()


# --- tree: exemptions and the stale check (Task 6) --------------------------------

def stage_exemptions(repo, *entries):
    stage(repo, commit_check.EXEMPT_PATH, "# test list\n" + "".join(e + "\n" for e in entries))


@needs_git
def test_exempted_line_passes_other_path_fails(repo):
    stage(repo, "docs/x.md", f"one\ntwo\nsee {HOME} here\nfour\nand {TILDE}x there\n")
    stage_exemptions(repo, f"docs/x.md | absolute-path | see {HOME} | the page explains it")
    commit(repo)
    rc, out, err = tool(repo, "tree")
    assert rc == 1, (out, err)
    assert finding_lines(out) == ["docs/x.md:5: absolute-path (tilde)"]


@needs_git
def test_stale_entry_missing_file_fails(repo):
    stage_exemptions(repo, f"docs/gone.md | absolute-path | see {HOME} | it was here")
    commit(repo)
    rc, out, err = tool(repo, "tree")
    assert rc == 1, (out, err)
    assert finding_lines(out) == [f"{commit_check.EXEMPT_PATH}:2: exemption (file missing)"]


@needs_git
def test_stale_entry_text_no_longer_flagged_fails(repo):
    stage(repo, "docs/x.md", "see the notes folder\n")
    stage_exemptions(repo, "docs/x.md | absolute-path | see the notes | it held a path once")
    commit(repo)
    rc, out, err = tool(repo, "tree")
    assert rc == 1, (out, err)
    assert finding_lines(out) == [f"{commit_check.EXEMPT_PATH}:2: exemption (text no longer flagged)"]


@needs_git
def test_entry_text_is_flagged_path_tree_clean(repo):
    stage(repo, "docs/x.md", f"see {HOME} here\n")
    stage_exemptions(repo, f"docs/x.md | absolute-path | {HOME} | the page explains it")
    commit(repo)
    rc, out, err = tool(repo, "tree")
    assert (rc, finding_lines(out)) == (0, []), (out, err)


@needs_git
def test_malformed_entry_fails_tree(repo):
    stage_exemptions(repo, "docs/x.md | absolute-path | no reason")
    commit(repo)
    rc, out, err = tool(repo, "tree")
    assert rc == 1, (out, err)
    assert finding_lines(out) == [f"{commit_check.EXEMPT_PATH}:2: exemption (malformed entry)"]


# --- range and message (Task 7) ----------------------------------------------------

def head(repo):
    return run_git(repo, "rev-parse", "HEAD").strip()


@needs_git
def test_range_names_commit_that_added_then_deleted_page(repo):
    base = head(repo)
    stage(repo, "site/index.html", "<p>page</p>\n")
    c1 = commit(repo, "add a page")
    run_git(repo, "rm", "-q", "site/index.html")
    c2 = commit(repo, "remove it")
    rc, out, err = tool(repo, "range", base, c2)
    assert rc == 1, (out, err)
    assert f"{c1[:12]} site/index.html: generated-output (html page)" in out.splitlines()
    assert c2[:12] not in out
    rc, out, err = tool(repo, "tree")
    assert (rc, finding_lines(out)) == (0, []), (out, err)


@needs_git
def test_range_and_message_flag_home_path_in_message(repo, tmp_path):
    base = head(repo)
    msg = f"subject\n\nthe notes live in {HOME}notes\n"
    c1 = commit(repo, msg)
    rc, out, err = tool(repo, "range", base, c1)
    assert rc == 1, (out, err)
    assert finding_lines(out) == [f"{c1[:12]} message:3: absolute-path (posix_home)"]
    assert HOME not in out
    path = tmp_path / "msg.txt"
    path.write_bytes(msg.encode("utf-8"))
    rc, out, err = tool(repo, "message", str(path))
    assert rc == 1, (out, err)
    assert finding_lines(out) == ["message:3: absolute-path (posix_home)"]


@needs_git
def test_range_scans_merge_resolution(repo):
    base = head(repo)
    run_git(repo, "checkout", "-q", "-b", "side")
    stage(repo, "a.txt", "side\n")
    commit(repo, "side")
    run_git(repo, "checkout", "-q", "main")
    stage(repo, "b.txt", "main\n")
    commit(repo, "main")
    run_git(repo, "merge", "-q", "--no-ff", "--no-commit", "side")
    stage(repo, "b.txt", f"main\nresolved with {HOME}x\n")
    merge = commit(repo, "merge side")
    rc, out, err = tool(repo, "range", base, merge)
    assert rc == 1, (out, err)
    assert finding_lines(out) == [f"{merge[:12]} b.txt:2: absolute-path (posix_home)"]


@needs_git
def test_range_root_commit(tmp_path):
    run_git(tmp_path, "init", "-q", "-b", "main")
    stage(tmp_path, "notes/a.md", f"see {HOME}\n")
    root = commit(tmp_path, "root")
    repo = commit_check.Repo(tmp_path, HERMETIC_ENV)
    findings = commit_check.scan_commits(repo, [root], [])
    assert findings == [f"{root[:12]} notes/a.md:1: absolute-path (posix_home)"]


@needs_git
def test_range_needs_two_commits(repo):
    rc, out, err = tool(repo, "range", "HEAD", "no-such-rev")
    assert rc == 2, (out, err)


@needs_git
def test_message_ignores_comment_and_scissors(repo, tmp_path):
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_bytes((
        "subject\n\nbody\n"
        f"# a status line naming {HOME}\n"
        "# ------------------------ >8 ------------------------\n"
        f"diff with {TILDE}x\n"
    ).encode("utf-8"))
    rc, out, err = tool(repo, "message", str(path))
    assert (rc, finding_lines(out)) == (0, []), (out, err)

    run_git(repo, "config", "core.commentChar", ";")
    path.write_bytes(f"subject\n\n; ignored {HOME}\n# checked {TILDE}x\n".encode("utf-8"))
    rc, out, err = tool(repo, "message", str(path))
    assert rc == 1, (out, err)
    assert finding_lines(out) == ["message:4: absolute-path (tilde)"]


# --- ci and the workflow (Task 8) --------------------------------------------------

ZEROS = "0" * 40
WARNING = (
    "::warning title=Commit check changed::This range changes {}; "
    "changes to the check need explicit review (docs/commit-check.md)"
)


def ci(repo, tmp_path, event, payload):
    path = tmp_path / "event.json"
    payload = dict(payload, repository={"default_branch": "main"})
    path.write_text(json.dumps(payload), encoding="utf-8")
    env = dict(HERMETIC_ENV, GITHUB_EVENT_NAME=event, GITHUB_EVENT_PATH=str(path))
    return tool(repo, "ci", env=env)


@pytest.fixture
def ci_repo(repo):
    """before: a commit whose message is flagged; inside: a later flagged commit.

    Only `range` scans messages (`tree` does not), so a named sha shows which
    commits the range held. origin/main points at `before`.
    """
    before = commit(repo, f"before\n\n{HOME}a\n")
    inside = commit(repo, f"inside\n\n{HOME}b\n")
    run_git(repo, "update-ref", "refs/remotes/origin/main", before)
    return repo, before, inside


def named(out, sha):
    return f"{sha[:12]} message:3: absolute-path (posix_home)" in out.splitlines()


@needs_git
def test_ci_pull_request_base_to_head(ci_repo, tmp_path):
    repo, before, inside = ci_repo
    rc, out, err = ci(repo, tmp_path, "pull_request",
                      {"pull_request": {"base": {"sha": before}, "head": {"sha": inside}}})
    assert rc == 1, (out, err)
    assert out.splitlines()[0] == f"ci: range {before[:12]}..{inside[:12]}"
    assert named(out, inside) and not named(out, before)


@needs_git
def test_ci_push_before_after(ci_repo, tmp_path):
    repo, before, inside = ci_repo
    rc, out, err = ci(repo, tmp_path, "push",
                      {"ref": "refs/heads/feature", "before": before, "after": inside})
    assert rc == 1, (out, err)
    assert out.splitlines()[0] == f"ci: range {before[:12]}..{inside[:12]}"
    assert named(out, inside) and not named(out, before)


@needs_git
@pytest.mark.parametrize("before", [ZEROS, "0123456789abcdef" * 2 + "01234567"])
def test_ci_push_new_branch_or_unknown_before(ci_repo, tmp_path, before):
    repo, on_main, inside = ci_repo
    rc, out, err = ci(repo, tmp_path, "push",
                      {"ref": "refs/heads/feature", "before": before, "after": inside})
    assert rc == 1, (out, err)
    assert out.splitlines()[0] == f"ci: range {inside[:12]} --not origin/main"
    assert named(out, inside) and not named(out, on_main)


@needs_git
def test_ci_push_default_branch_zero_before(ci_repo, tmp_path):
    repo, before, inside = ci_repo
    rc, out, err = ci(repo, tmp_path, "push",
                      {"ref": "refs/heads/main", "before": ZEROS, "after": inside})
    assert rc == 1, (out, err)
    assert out.splitlines()[0] == f"ci: range {inside[:12]} (whole history)"
    assert named(out, inside) and named(out, before)


@needs_git
def test_ci_push_branch_deletion_is_empty(ci_repo, tmp_path):
    repo, before, _ = ci_repo
    rc, out, err = ci(repo, tmp_path, "push",
                      {"ref": "refs/heads/feature", "before": before, "after": ZEROS})
    assert out.splitlines()[0] == "ci: range empty"
    assert (rc, finding_lines(out)) == (0, []), (out, err)


@needs_git
def test_ci_warns_on_self_change(repo, tmp_path):
    before = head(repo)
    run_git(repo, "update-ref", "refs/remotes/origin/main", before)
    stage(repo, commit_check.EXEMPT_PATH, "# an edited list\n")
    after = commit(repo, "edit the list")
    rc, out, err = ci(repo, tmp_path, "push",
                      {"ref": "refs/heads/feature", "before": before, "after": after})
    assert (rc, finding_lines(out)) == (0, []), (out, err)
    assert WARNING.format(commit_check.EXEMPT_PATH) in out.splitlines()


@needs_git
def test_ci_no_warning_for_main_side_self_change(repo, tmp_path):
    base = head(repo)
    stage(repo, commit_check.EXEMPT_PATH, "# main edits the list\n")
    on_main = commit(repo, "main edits the list")
    run_git(repo, "update-ref", "refs/remotes/origin/main", on_main)
    run_git(repo, "checkout", "-q", "-b", "feature", base)
    stage(repo, "notes/a.md", "feature work\n")
    before = commit(repo, "feature work")
    run_git(repo, "merge", "-q", "--no-ff", "-m", "merge main forward", "main")
    after = head(repo)
    rc, out, err = ci(repo, tmp_path, "push",
                      {"ref": "refs/heads/feature", "before": before, "after": after})
    assert (rc, finding_lines(out)) == (0, []), (out, err)
    assert not [l for l in out.splitlines() if l.startswith("::warning")], out


@needs_git
def test_ci_unknown_event_exits_2(repo, tmp_path):
    rc, out, err = ci(repo, tmp_path, "workflow_dispatch", {})
    assert rc == 2, (out, err)


def test_workflow_shape():
    lines = [l.strip() for l in (ROOT / ".github" / "workflows" / "commit-check.yml")
             .read_text(encoding="utf-8").splitlines()]
    for line in ("push:", "pull_request:", "contents: read", "fetch-depth: 0",
                 "- run: python tools/commit_check.py ci"):
        assert line in lines, line
    assert not [l for l in lines if "pull_request_target" in l]


# --- names (Task 9) -------------------------------------------------------------------

ENTRY = "zor" + "batel"  # fictional; built here so no source holds it whole
COUNT_RE = re.compile(r"^names: (\d+) match(?:es)?$")


@pytest.fixture
def denylist(tmp_path):
    path = tmp_path / "deny" / "list.txt"
    path.parent.mkdir()
    path.write_bytes(f"# a private list\n\n  {ENTRY}  \n".encode("utf-8"))
    return path


def names(repo, denylist, *args, env=None):
    rc, out, err = tool(repo, "names", "--denylist", str(denylist), *args, env=env)
    counts = [int(m.group(1)) for l in out.splitlines() if (m := COUNT_RE.match(l))]
    assert len(counts) == 1, (rc, out, err)
    assert rc == (1 if counts[0] else 0), (rc, out, err)
    return counts[0], out, err


@needs_git
def test_names_staged_case_insensitive_count_only(repo, denylist):
    stage(repo, "notes/a.md", f"one\nmet {ENTRY.upper()} today\n")
    count, out, err = names(repo, denylist, "--staged")
    assert count == 1
    assert out.splitlines()[-1] == "names: 1 match"
    assert ENTRY.casefold() not in (out + err).casefold()


@needs_git
@pytest.mark.parametrize("text", [
    f"notes/{ENTRY}/todo.md",
    f"{ENTRY}/share",
    f"{ENTRY}_notes",
    f"{ENTRY}2",
    f"x_{ENTRY}",
])
def test_names_word_boundaries(repo, denylist, text):
    stage(repo, "notes/a.md", text + "\n")
    assert names(repo, denylist, "--staged")[0] == 1


@needs_git
def test_names_not_inside_a_longer_word(repo, denylist):
    stage(repo, "notes/a.md", f"un{ENTRY}ed and {ENTRY}s\n")
    assert names(repo, denylist, "--staged")[0] == 0


@needs_git
def test_names_counts_staged_path(repo, denylist):
    stage(repo, f"notes/{ENTRY}/x.md", "clean\n")
    assert names(repo, denylist, "--staged")[0] == 1


@needs_git
@pytest.mark.parametrize("text", [
    f"see https://github.com/{ENTRY}/repo",
    f"github.com/owner/gantry/blob/main/notes/{ENTRY}/x",
    f"fixed in {ENTRY}/repo#7",
    f"Co-Authored-By: {ENTRY} <x@example.invalid>",
    f"Signed-off-by: {ENTRY} <x@example.invalid>",
])
def test_names_removals_count_zero(repo, denylist, text):
    stage(repo, "notes/a.md", "subject\n" + text + "\n")
    assert names(repo, denylist, "--staged")[0] == 0


@needs_git
def test_names_authorship_fields_count_zero(repo, denylist):
    stage_all(repo, {
        "LICENSE": f"MIT License\n\nCopyright (c) 2026 {ENTRY}\n",
        ".claude-plugin/plugin.json": json.dumps({"name": "gantry", "author": {"name": ENTRY}}),
        ".claude-plugin/marketplace.json": json.dumps({
            "owner": {"name": ENTRY},
            "plugins": [{"name": "gantry", "author": {"name": ENTRY}}],
        }),
        "pyproject.toml": f'[project]\nname = "gantry"\nauthors = [{{ name = "{ENTRY}" }}]\n',
    })
    commit(repo)
    assert names(repo, denylist, "--all")[0] == 0

    stage(repo, ".claude-plugin/plugin.json",
          json.dumps({"author": {"name": "Someone Else"}, "description": f"for {ENTRY}"}))
    commit(repo)
    assert names(repo, denylist, "--all")[0] == 1


@needs_git
def test_names_clone_identity(repo, denylist):
    stage(repo, "notes/a.md", f"{ENTRY}/gantry\n")
    count, _, err = names(repo, denylist, "--staged")
    assert count == 1
    assert "no identity was removed" in err

    run_git(repo, "remote", "add", "origin", f"https://github.com/{ENTRY}/gantry.git")
    stage(repo, "notes/a.md", f"add {ENTRY}/gantry\nsee {ENTRY}.github.io/gantry\n")
    assert names(repo, denylist, "--staged")[0] == 0
    stage(repo, "notes/a.md", f"and {ENTRY}/other\n")
    assert names(repo, denylist, "--staged")[0] == 1


@needs_git
def test_names_ignores_exemptions(repo, denylist):
    line = f"{ENTRY} keeps notes in {HOME}x"
    stage_exemptions(repo, f"notes/a.md | absolute-path | {line} | an exempted line")
    commit(repo)
    stage(repo, "notes/a.md", line + "\n")
    assert names(repo, denylist, "--staged")[0] == 1


@needs_git
def test_names_message_and_text(repo, denylist, tmp_path):
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_bytes(f"subject\n\nfor {ENTRY}\n# status naming {ENTRY}\n".encode("utf-8"))
    assert names(repo, denylist, "--message", str(msg))[0] == 1
    body = tmp_path / "body.md"
    body.write_bytes(f"a pull request body for {ENTRY}\n".encode("utf-8"))
    assert names(repo, denylist, "--text", str(body))[0] == 1


@needs_git
def test_names_where_prints_location_not_text(repo, denylist):
    stage_all(repo, {
        "f.md": f"one\ntwo {ENTRY}\n",
        f"notes/{ENTRY}/x.md": "clean\n",
    })
    count, out, err = names(repo, denylist, "--staged", "--where")
    assert count == 2
    lines = out.splitlines()
    assert "names: f.md:2" in lines
    assert "names: notes/<match>/x.md (path)" in lines
    assert ENTRY.casefold() not in (out + err).casefold()


@needs_git
@pytest.mark.parametrize("source", ["config", "flag"])
def test_names_unreadable_denylist_exits_2_naming_key(repo, tmp_path, source):
    missing = tmp_path / "gone.txt"
    if source == "config":
        run_git(repo, "config", "gantry.denylist", str(missing))
        rc, out, err = tool(repo, "names", "--staged")
        assert "names: cannot read the denylist that git config gantry.denylist names (" in err
    else:
        rc, out, err = tool(repo, "names", "--denylist", str(missing), "--staged")
        assert "names: cannot read the denylist that --denylist names (" in err
    assert rc == 2, (out, err)


@needs_git
def test_names_empty_or_absent_denylist_exits_2(repo, tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"# nothing\n\n")
    assert tool(repo, "names", "--denylist", str(empty), "--staged")[0] == 2
    assert tool(repo, "names", "--staged")[0] == 2


# --- install-hook and the hooks (Task 10) -------------------------------------------

HOOKS = ("pre-commit", "commit-msg")


def hooks_dir(repo):
    rel = run_git(repo, "rev-parse", "--git-path", "hooks").strip()
    return (Path(repo) / rel).resolve()


def gantry_config(repo):
    result = git(repo, "config", "--get-regexp", "^gantry\\.", hermetic=True)
    return _decode(result.stdout).strip()


def git_commit(repo, *args):
    """A real `git commit` (hooks run); (rc, stdout + stderr)."""
    result = subprocess.run(
        ["git", *IDENTITY, "commit", "-q", *args],
        cwd=repo, env=HERMETIC_ENV, capture_output=True,
    )
    return result.returncode, _decode(result.stdout) + _decode(result.stderr)


@pytest.fixture
def hooked(repo, denylist):
    rc, out, err = tool(repo, "install-hook", "--denylist", str(denylist))
    assert rc == 0, (out, err)
    return repo


@needs_git
def test_install_hook_writes_into_linked_worktree_git_path(repo, denylist):
    wt = repo.parent / "wt"
    run_git(repo, "worktree", "add", "-q", str(wt))
    rc, out, err = tool(wt, "install-hook", "--denylist", str(denylist))
    assert rc == 0, (out, err)
    for name in HOOKS:
        data = (hooks_dir(wt) / name).read_bytes()
        assert data.split(b"\n")[1].startswith(b"# gantry commit-check hook "), name
        assert b"\r" not in data
    config = gantry_config(wt)
    assert f"gantry.python {sys.executable}" in config.splitlines()
    assert "gantry.denylist " in config


@needs_git
@pytest.mark.parametrize("case", ["denylist_in_work_tree", "hooks_path_set", "foreign_hook"])
def test_install_hook_refuses(repo, denylist, case):
    target = denylist
    if case == "denylist_in_work_tree":
        target = repo / "notes" / "list.txt"
        target.parent.mkdir()
        target.write_bytes(denylist.read_bytes())
    elif case == "hooks_path_set":
        run_git(repo, "config", "core.hooksPath", "githooks")
    else:
        hooks_dir(repo).mkdir(parents=True, exist_ok=True)
        (hooks_dir(repo) / "pre-commit").write_bytes(b"#!/bin/sh\n# someone else's hook\n")
    before = {n: (hooks_dir(repo) / n).read_bytes() for n in HOOKS if (hooks_dir(repo) / n).exists()}
    rc, out, err = tool(repo, "install-hook", "--denylist", str(target))
    assert rc == 2, (out, err)
    reason = {"denylist_in_work_tree": "inside a work tree", "hooks_path_set": "core.hooksPath",
              "foreign_hook": "did not write"}[case]
    assert reason in err, err
    after = {n: (hooks_dir(repo) / n).read_bytes() for n in HOOKS if (hooks_dir(repo) / n).exists()}
    assert after == before
    assert gantry_config(repo) == ""


@needs_git
def test_install_hook_refuses_denylist_in_main_worktree_from_linked(repo, denylist):
    wt = repo.parent / "wt"
    run_git(repo, "worktree", "add", "-q", str(wt))
    inside_main = repo / "private" / "list.txt"
    inside_main.parent.mkdir()
    inside_main.write_bytes(denylist.read_bytes())
    rc, out, err = tool(wt, "install-hook", "--denylist", str(inside_main))
    assert rc == 2, (out, err)
    assert "inside a work tree" in err, err
    assert not [n for n in HOOKS if (hooks_dir(wt) / n).exists()]
    assert gantry_config(wt) == ""


@needs_git
def test_install_hook_reinstalls_over_own(hooked, denylist):
    rc, out, err = tool(hooked, "install-hook", "--denylist", str(denylist))
    assert rc == 0, (out, err)


@needs_git
def test_hook_stops_commit_when_denylist_deleted(hooked, denylist):
    start = head(hooked)
    denylist.unlink()
    stage(hooked, "notes/a.md", "clean\n")
    rc, out = git_commit(hooked, "-m", "clean")
    assert rc != 0, out
    assert "gantry.denylist" in out
    assert head(hooked) == start


@needs_git
def test_hook_stops_commit_with_denylist_name(hooked):
    start = head(hooked)
    stage(hooked, "notes/a.md", f"met {ENTRY}\n")
    rc, out = git_commit(hooked, "-m", "notes")
    assert rc != 0, out
    assert "names: 1 match" in out.splitlines()
    assert head(hooked) == start


@needs_git
def test_hook_stops_commit_with_flagged_path(hooked):
    start = head(hooked)
    stage(hooked, "notes/a.md", f"see {HOME}\n")
    rc, out = git_commit(hooked, "-m", "notes")
    assert rc != 0, out
    assert "notes/a.md:1: absolute-path (posix_home)" in out.splitlines()
    assert head(hooked) == start


@needs_git
def test_commit_msg_hook_stops_home_path_message(hooked):
    start = head(hooked)
    stage(hooked, "notes/a.md", "clean\n")
    rc, out = git_commit(hooked, "-m", f"notes\n\nfrom {HOME}x")
    assert rc != 0, out
    assert "message:3: absolute-path (posix_home)" in out.splitlines()
    assert head(hooked) == start


@needs_git
def test_hook_without_denylist_runs_ci_rules(hooked):
    run_git(hooked, "config", "--unset", "gantry.denylist")
    start = head(hooked)
    stage(hooked, "notes/a.md", "clean\n")
    rc, out = git_commit(hooked, "-m", "clean")
    assert rc == 0, out
    assert "names and hostnames were not checked" in out
    assert head(hooked) != start


@needs_git
def test_hook_missing_tool_prints_remedy(hooked):
    run_git(hooked, "rm", "-q", "tools/commit_check.py")
    start = head(hooked)
    rc, out = git_commit(hooked, "-m", "drop the tool")
    assert rc != 0, out
    assert "tools/commit_check.py is missing" in out and "--no-verify" in out
    assert head(hooked) == start


@needs_git
def test_hook_dead_interpreter_names_key(hooked, tmp_path):
    run_git(hooked, "config", "gantry.python", str(tmp_path / "no-such-python"))
    start = head(hooked)
    stage(hooked, "notes/a.md", "clean\n")
    rc, out = git_commit(hooked, "-m", "clean")
    assert rc != 0, out
    assert "gantry.python" in out and "install-hook" in out
    assert head(hooked) == start


# --- repo-level guards (Task 11) ------------------------------------------------------

DOCS = ROOT / "docs" / "commit-check.md"
LIMITS_HEADING = "## CI passing does not mean no private content"


def docs_section(heading):
    text = DOCS.read_text(encoding="utf-8")
    assert heading in text.splitlines(), heading
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def test_bomless_utf16_is_skipped_as_documented():
    data = ("x = " + HOME + "\n").encode("utf-16-le")
    assert commit_check.check_file("notes/ps.txt", data, []) == []
    section = docs_section(LIMITS_HEADING)
    assert "UTF-16" in section and "byte-order mark" in section


@needs_git
def test_repo_tree_is_clean():
    result = git(ROOT, "rev-parse", "--show-toplevel")
    if result.returncode != 0 or not os.path.samefile(_decode(result.stdout).strip(), ROOT):
        pytest.skip("the gantry root is not a git work tree")
    rc, out, err = tool(ROOT, "tree")
    assert (rc, finding_lines(out)) == (0, []), (out, err)


def test_docs_cover_contract():
    text = DOCS.read_text(encoding="utf-8")
    for token in ("`tree`", "`range", "`staged`", "`message", "`ci`", "`names`", "`install-hook",
                  "--staged", "--message", "--all", "--text", "--where", "--denylist",
                  "gantry.denylist", "gantry.python", "commit_check_exempt.txt",
                  "fetch-depth", "--no-verify", "AUTHORSHIP_FIELDS", "::warning"):
        assert token in text, token


def test_authorship_fields_constant():
    assert commit_check.AUTHORSHIP_FIELDS == (
        ("LICENSE", None),
        (".claude-plugin/plugin.json", "author"),
        (".claude-plugin/marketplace.json", "owner"),
        (".claude-plugin/marketplace.json", "plugins[].author"),
        ("pyproject.toml", "project.authors"),
    )
