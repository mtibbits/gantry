"""tools/commit_check.py: the commit check against private data (#44).

Every path form is assembled at run time ("~" + "/", "/" + "home" + "/"), so
this file holds none of them and passes the check it tests.
"""

import os
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
    return [l for l in out.splitlines() if l and not l.startswith(("commit-check:", "ci:"))]


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
