"""tools/commit_check.py: the commit check against private data (#44).

Every path form is assembled at run time ("~" + "/", "/" + "home" + "/"), so
this file holds none of them and passes the check it tests.
"""

from pathlib import Path

import pytest

import commit_check
from test_samples import PATH_FORM_CONTROLS, ROOT

TOOL = ROOT / "tools" / "commit_check.py"


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
