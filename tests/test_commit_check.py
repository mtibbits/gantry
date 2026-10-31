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
