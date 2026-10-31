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
